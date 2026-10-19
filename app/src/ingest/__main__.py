import argparse
import json
import logging
from datetime import UTC, datetime

import google.auth
import pyarrow.fs as fs
from google.auth import impersonated_credentials
from google.auth.credentials import Credentials
from google.cloud import bigquery, storage

from ingest.alerts import notify
from ingest.build import BuildError, build
from ingest.build_config import BuildConfig
from ingest.config import Config, parse_quarter
from ingest.discover import discover, http_session
from ingest.schema_check import check_source
from ingest.state import pending
from ingest.transfer import transfer

LOGGER = logging.getLogger("ingest")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "message": record.getMessage(),
        }
        if record.exc_info:
            result["exception"] = self.formatException(record.exc_info)
        return json.dumps(result)


def application_credentials(config: Config) -> Credentials:
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    credentials, _ = google.auth.default(scopes=scopes)
    if config.impersonate_service_account is not None:
        credentials = impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=config.impersonate_service_account,
            target_scopes=scopes,
            lifetime=3600,
        )
    return credentials


def storage_client(config: Config) -> storage.Client:
    return storage.Client(project=config.project_id, credentials=application_credentials(config))


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--limit must be a positive integer.")
    return parsed


def main() -> int:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    parser = argparse.ArgumentParser(description="Sync Ookla raw files and build BigQuery tables.")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync")
    sync.add_argument("--type", choices=["mobile", "fixed", "all"], default="all")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument(
        "--quarter",
        type=parse_quarter,
        metavar="YYYY-QN",
        help="Sync only this quarter (at or after MIN_QUARTER); does not force a transfer.",
    )
    sync.add_argument(
        "--limit",
        type=positive_int,
        help="Limit transfers per run; up-to-date files do not count.",
    )
    build_command = commands.add_parser("build")
    build_command.add_argument("--type", choices=["mobile", "fixed", "all"], default="all")
    build_command.add_argument("--quarter", type=parse_quarter, metavar="YYYY-QN")
    build_command.add_argument("--dry-run", action="store_true")
    build_command.add_argument(
        "--force", action="store_true", help="Rebuild selected raw quarters even when ETags match."
    )
    args = parser.parse_args()
    try:
        config = Config.from_env()
        if args.quarter is not None and args.quarter < config.min_quarter:
            raise ValueError("--quarter must be at or after MIN_QUARTER.")
        settings = BuildConfig.from_env() if args.command == "build" else None
        effective_config = {
            "command": args.command,
            "project_id": config.project_id,
            "raw_bucket": config.raw_bucket,
            "min_quarter": config.min_quarter,
            "s3_region": config.s3_region,
            "source_schema_path": str(config.schema_path),
            "impersonation_enabled": config.impersonate_service_account is not None,
            "network_type": args.type,
            "quarter": args.quarter,
            "dry_run": args.dry_run,
        }
        if settings is not None:
            effective_config.update(
                dataset_id=settings.dataset_id,
                location=settings.location,
                region_bbox=settings.bbox,
                maximum_bytes_billed=settings.maximum_bytes_billed,
                mart_zoom=settings.mart_zoom,
                minimum_row_percent=settings.minimum_row_percent,
                latency_ceiling_ms=settings.latency_ceiling_ms,
                native_schema_path=str(settings.schema_path),
                force=args.force,
            )
        else:
            effective_config["limit"] = args.limit
        LOGGER.info("Effective configuration: %s", json.dumps(effective_config, sort_keys=True))
        if settings is not None:
            credentials = application_credentials(config)
            try:
                with (
                    storage.Client(project=config.project_id, credentials=credentials) as gcs,
                    bigquery.Client(project=config.project_id, credentials=credentials) as bq,
                ):
                    summary = build(
                        gcs, bq, config, settings, args.type, args.quarter, args.force, args.dry_run
                    )
            except Exception as error:
                if not args.dry_run:
                    requested_quarter = (
                        f"{args.quarter[0]}-Q{args.quarter[1]}" if args.quarter else "all"
                    )
                    notify(
                        credentials,
                        config.project_id,
                        error.notification
                        if isinstance(error, BuildError)
                        else (
                            f"Build FAILED; type={args.type}; "
                            f"quarter={requested_quarter}; "
                            "rule=build_setup_or_discovery. Inspect application logs."
                        ),
                    )
                raise
            if not args.dry_run:
                notify(credentials, config.project_id, summary)
            return 0
        # Fail on a missing/invalid contract even when the bucket is already current.
        contract = json.loads(config.schema_path.read_text(encoding="utf-8"))
        if not isinstance(contract, list) or not contract:
            raise ValueError("Source schema contract must be a non-empty JSON array.")
        with storage_client(config) as client, http_session() as session:
            bucket = client.bucket(config.raw_bucket)
            layers = ["mobile", "fixed"] if args.type == "all" else [args.type]
            sources = [
                source
                for layer in layers
                for source in discover(session, layer, config.min_quarter)
            ]
            sources.sort(key=lambda source: (source.year, source.quarter, source.key))
            if args.quarter is not None:
                sources = [
                    source for source in sources if (source.year, source.quarter) == args.quarter
                ]
                if not sources:
                    raise ValueError(
                        "Requested quarter has no source objects for the selected type."
                    )
            decisions = [decision for source in sources if (decision := pending(bucket, source))]
            selected = decisions[: args.limit] if args.limit is not None else decisions
            LOGGER.info(
                "Discovered %d objects; %d pending; %d selected; dry_run=%s",
                len(sources),
                len(decisions),
                len(selected),
                args.dry_run,
            )
            source_fs = (
                fs.S3FileSystem(
                    anonymous=True,
                    region=config.s3_region,
                    connect_timeout=10,
                    request_timeout=120,
                    retry_strategy=fs.AwsStandardS3RetryStrategy(max_attempts=4),
                )
                if selected and not args.dry_run
                else None
            )
            for decision in selected:
                LOGGER.info(
                    "%s %s -> gs://%s/%s (%d bytes, %s)",
                    "Would transfer" if args.dry_run else "Transferring",
                    decision.source.key,
                    config.raw_bucket,
                    decision.source.destination,
                    decision.source.size,
                    decision.reason,
                )
                if source_fs is not None:
                    check_source(
                        source_fs, decision.source.key, decision.source.size, config.schema_path
                    )
                    LOGGER.info("Source footer schema checked: %s", decision.source.key)
                    transfer(session, bucket, decision)
            LOGGER.info("Sync succeeded; transferred=%d", 0 if args.dry_run else len(selected))
        return 0
    except Exception:
        LOGGER.exception("%s failed", args.command.capitalize())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
