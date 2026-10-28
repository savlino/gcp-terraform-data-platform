import json
import logging
import re
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files

from google.cloud import bigquery, storage

from ingest.build_config import BuildConfig
from ingest.config import Config
from ingest.quality import QualityError, QualityResult, evaluate

LOGGER = logging.getLogger(__name__)
RAW_KEY = re.compile(r"ookla/type=(mobile|fixed)/year=([0-9]{4})/quarter=([1-4])/[^/]+\.parquet")


class BuildError(RuntimeError):
    """Safe notification context; the original error remains in the exception chain."""

    def __init__(self, network_type: str, quarter: str, rules: str) -> None:
        self.notification = (
            f"Build FAILED; type={network_type}; quarter={quarter}; rule={rules}. "
            "Inspect application logs."
        )
        super().__init__(self.notification)


@dataclass(frozen=True)
class RawPartition:
    network_type: str
    year: int
    quarter: int
    uri: str
    name: str
    etag: str
    generation: int

    @property
    def quarter_start(self) -> date:
        return date(self.year, self.quarter * 3 - 2, 1)

    @property
    def stage_name(self) -> str:
        return f"stg_ookla_tiles_{self.network_type}_{self.year}q{self.quarter}"

    @property
    def raw_stage_name(self) -> str:
        return f"stg_raw_{self.network_type}_{self.year}q{self.quarter}"


def raw_partitions(
    gcs: storage.Client, config: Config, network_type: str, quarter: tuple[int, int] | None
) -> list[RawPartition]:
    found: dict[tuple[str, int, int], RawPartition] = {}
    for blob in gcs.list_blobs(config.raw_bucket, prefix="ookla/"):
        match = RAW_KEY.fullmatch(blob.name)
        if match is None:
            LOGGER.warning("Ignoring unrecognized raw key: %s", blob.name)
            continue
        layer, year_text, quarter_text = match.groups()
        year, q = int(year_text), int(quarter_text)
        if (year, q) < config.min_quarter or (quarter is not None and (year, q) != quarter):
            continue
        if network_type != "all" and layer != network_type:
            continue
        metadata = blob.metadata or {}
        etag = metadata.get("source_etag")
        if not etag or blob.generation is None or metadata.get("source_size") != str(blob.size):
            raise ValueError(f"Raw object has missing/inconsistent sync metadata: {blob.name}")
        key = (layer, year, q)
        if key in found:
            raise ValueError(f"Multiple raw files for {layer} {year}-Q{q}; unsupported grain.")
        found[key] = RawPartition(
            layer,
            year,
            q,
            f"gs://{config.raw_bucket}/{blob.name}",
            blob.name,
            etag,
            int(blob.generation),
        )
    if quarter is not None and not found:
        raise ValueError("Requested quarter has no raw objects for the selected type.")
    return sorted(found.values(), key=lambda p: (p.year, p.quarter, p.network_type))


def sql(name: str, dataset: str, stage: str = "", raw_stage: str = "") -> str:
    return (
        files("ingest")
        .joinpath("sql", name + ".sql")
        .read_text(encoding="utf-8")
        .format(dataset=dataset, stage=stage, raw_stage=raw_stage)
    )


def query(
    client: bigquery.Client,
    settings: BuildConfig,
    text: str,
    parameters: list[bigquery.ScalarQueryParameter],
    destination: str | None = None,
    collect: bool = True,
) -> list[bigquery.Row]:
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=settings.maximum_bytes_billed,
        query_parameters=parameters,
        use_legacy_sql=False,
    )
    if destination is not None:
        job_config.destination = destination
        job_config.write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE
        job_config.create_disposition = bigquery.CreateDisposition.CREATE_NEVER
    rows = client.query(text, job_config=job_config, location=settings.location).result(
        max_results=None if collect else 0
    )
    return list(rows) if collect else []


def partition_parameters(
    partition: RawPartition, settings: BuildConfig, loaded_at: datetime
) -> list[bigquery.ScalarQueryParameter]:
    min_lon, max_lon, min_lat, max_lat = settings.bbox
    return [
        bigquery.ScalarQueryParameter(name, kind, value)
        for name, kind, value in (
            ("network_type", "STRING", partition.network_type),
            ("year", "INT64", partition.year),
            ("quarter", "INT64", partition.quarter),
            ("quarter_start", "DATE", partition.quarter_start),
            ("source_uri", "STRING", partition.uri),
            ("source_etag", "STRING", partition.etag),
            ("loaded_at", "TIMESTAMP", loaded_at),
            ("min_lon", "FLOAT64", min_lon),
            ("max_lon", "FLOAT64", max_lon),
            ("min_lat", "FLOAT64", min_lat),
            ("max_lat", "FLOAT64", max_lat),
            ("latency_ceiling", "FLOAT64", settings.latency_ceiling_ms),
        )
    ]


def publish_partition(
    gcs: storage.Client,
    bq: bigquery.Client,
    config: Config,
    settings: BuildConfig,
    dataset: str,
    partition: RawPartition,
) -> QualityResult:
    stage_id = f"{dataset}.{partition.stage_name}"
    table = bigquery.Table(
        stage_id,
        schema=[
            bigquery.SchemaField.from_api_repr(field)
            for field in json.loads(settings.schema_path.read_text(encoding="utf-8"))
        ],
    )
    table.expires = datetime.now(UTC) + timedelta(days=1)
    table.labels = {
        "project": "gcp-tf-portfolio",
        "env": config.project_id.rsplit("-", 1)[-1],
        "managed_by": "terraform",
    }
    # A conflicting deterministic stage name fails instead of clobbering another build.
    with ExitStack() as cleanup:
        bq.create_table(table)
        cleanup.callback(bq.delete_table, stage_id, not_found_ok=True)
        raw_stage_id = f"{dataset}.{partition.raw_stage_name}"
        raw_table = bigquery.Table(raw_stage_id)
        raw_table.expires = table.expires
        raw_table.labels = table.labels
        bq.create_table(raw_table)
        cleanup.callback(bq.delete_table, raw_stage_id, not_found_ok=True)
        bq.load_table_from_uri(
            partition.uri,
            raw_stage_id,
            job_config=bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.PARQUET,
                autodetect=True,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                create_disposition=bigquery.CreateDisposition.CREATE_NEVER,
            ),
            location=settings.location,
        ).result()
        parameters = partition_parameters(partition, settings, datetime.now(UTC))
        query(
            bq,
            settings,
            sql("stage", dataset, stage_id, raw_stage_id),
            parameters,
            destination=stage_id,
            collect=False,
        )
        rows = query(bq, settings, sql("quality", dataset, stage_id, raw_stage_id), parameters)
        if len(rows) != 1:
            raise ValueError("DQ query did not return exactly one aggregate row.")
        stats = dict(rows[0].items())
        result = evaluate(stats, settings.minimum_row_percent, int(stats["previous_rows"]))
        current = gcs.bucket(config.raw_bucket).get_blob(partition.name)
        if (
            current is None
            or int(current.generation or 0) != partition.generation
            or (current.metadata or {}).get("source_etag") != partition.etag
        ):
            raise ValueError("Raw object changed during build; rerun after sync completes.")
        parameters.extend(
            [
                bigquery.ScalarQueryParameter("source_rows", "INT64", result.source_rows),
                bigquery.ScalarQueryParameter("loaded_rows", "INT64", result.rows),
            ]
        )
        query(bq, settings, sql("publish", dataset, stage_id), parameters, collect=False)
        LOGGER.info(
            "Published %s %s; rows=%d; DQ=PASS",
            partition.network_type,
            partition.quarter_start,
            result.rows,
        )
        return result


def build(
    gcs: storage.Client,
    bq: bigquery.Client,
    config: Config,
    settings: BuildConfig,
    network_type: str,
    quarter: tuple[int, int] | None,
    force: bool,
    dry_run: bool,
) -> str:
    started = time.monotonic()
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", config.project_id):
        raise ValueError("PROJECT_ID must be a valid GCP project ID for SQL identifiers.")
    dataset = f"{config.project_id}.{settings.dataset_id}"
    partitions = raw_partitions(gcs, config, network_type, quarter)
    watermarks = {
        (row["network_type"], row["quarter_start"]): (row["source_etag"], row["source_uri"])
        for row in query(bq, settings, sql("watermarks", dataset), [])
    }
    selected = [
        p
        for p in partitions
        if force or watermarks.get((p.network_type, p.quarter_start)) != (p.etag, p.uri)
    ]
    for partition in selected:
        LOGGER.info(
            "%s build %s %s from %s",
            "Would" if dry_run else "Selected",
            partition.network_type,
            partition.quarter_start,
            partition.uri,
        )
    if dry_run:
        LOGGER.info(
            "Build dry-run: %d selected; no staging, mutations, mart or alerts", len(selected)
        )
        return f"Build dry-run: {len(selected)} selected"
    total = 0
    outlier_rows = 0
    null_down_rows = 0
    completed: list[str] = []
    for partition in selected:
        try:
            result = publish_partition(gcs, bq, config, settings, dataset, partition)
        except Exception as error:
            rules = (
                ", ".join(
                    f"{rule.rule}({rule.violations})"
                    for rule in error.result.rules
                    if rule.severity == "ERROR" and rule.violations
                )
                if isinstance(error, QualityError)
                else "partition_processing"
            )
            raise BuildError(
                partition.network_type, f"{partition.year}-Q{partition.quarter}", rules
            ) from error
        total += result.rows
        outlier_rows += result.outlier_rows
        null_down_rows += result.null_down_rows
        completed.append(f"{partition.network_type} {partition.year}-Q{partition.quarter}")
    try:
        query(
            bq,
            settings,
            sql("mart", dataset),
            [
                bigquery.ScalarQueryParameter("all_start", "DATE", date.min),
                bigquery.ScalarQueryParameter("mart_zoom", "INT64", settings.mart_zoom),
                bigquery.ScalarQueryParameter(
                    "latency_ceiling", "FLOAT64", settings.latency_ceiling_ms
                ),
                bigquery.ScalarQueryParameter("loaded_at", "TIMESTAMP", datetime.now(UTC)),
            ],
            collect=False,
        )
    except Exception as error:
        raise BuildError("all", "all", "mart_rebuild") from error
    null_share = f"{100 * null_down_rows / total:.2f}%" if total else "n/a"
    summary = (
        f"Build DQ={'PASS' if selected else 'NOT_RUN'}; partitions={len(selected)}; "
        f"rows={total}; mart rebuilt; duration_s={time.monotonic() - started:.2f}; "
        f"WARN (loaded partitions): outlier_rows={outlier_rows}, "
        f"null_down_rows={null_down_rows}, null_down_share={null_share}; "
        f"loaded={', '.join(completed) or 'none (unchanged)'}"
    )
    LOGGER.info(summary)
    return summary
