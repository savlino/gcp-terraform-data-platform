import argparse
import json
import logging
from datetime import UTC, datetime

import google.auth
import pyarrow.fs as fs
from google.auth import impersonated_credentials
from google.cloud import storage

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


def storage_client(config: Config) -> storage.Client:
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    credentials, _ = google.auth.default(scopes=scopes)
    if config.impersonate_service_account is not None:
        credentials = impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=config.impersonate_service_account,
            target_scopes=scopes,
            lifetime=3600,
        )
    return storage.Client(project=config.project_id, credentials=credentials)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--limit must be a positive integer.")
    return parsed


def main() -> int:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    parser = argparse.ArgumentParser(description="Sync public Ookla Parquet into a GCS raw bucket.")
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
    args = parser.parse_args()
    try:
        config = Config.from_env()
        if args.quarter is not None and args.quarter < config.min_quarter:
            raise ValueError("--quarter must be at or after MIN_QUARTER.")
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
        LOGGER.exception("Sync failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
