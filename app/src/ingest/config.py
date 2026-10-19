import os
import re
from dataclasses import dataclass
from pathlib import Path


def parse_quarter(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"[0-9]{4}-Q[1-4]", value):
        raise ValueError("MIN_QUARTER must use YYYY-Q1 through YYYY-Q4.")
    quarter = (int(value[:4]), int(value[-1]))
    if quarter < (2023, 3):
        raise ValueError("MIN_QUARTER cannot be earlier than 2023-Q3.")
    return quarter


@dataclass(frozen=True)
class Config:
    project_id: str
    raw_bucket: str
    min_quarter: tuple[int, int]
    s3_region: str
    schema_path: Path
    impersonate_service_account: str | None

    @classmethod
    def from_env(cls) -> "Config":
        project = os.environ.get("PROJECT_ID", "").strip()
        bucket = os.environ.get("RAW_BUCKET", "").strip()
        if not project or not bucket or bucket.startswith("gs://"):
            raise ValueError("Set PROJECT_ID and RAW_BUCKET (bucket name without gs://).")
        region = os.environ.get("S3_REGION", "us-west-2").strip()
        if not region:
            raise ValueError("S3_REGION must not be empty.")
        target = os.environ.get("IMPERSONATE_SERVICE_ACCOUNT")
        if target is not None and not target.strip():
            raise ValueError("IMPERSONATE_SERVICE_ACCOUNT is set but empty; refusing ADC fallback.")
        return cls(
            project,
            bucket,
            parse_quarter(os.environ.get("MIN_QUARTER", "2023-Q3")),
            region,
            Path(
                os.environ.get(
                    "SOURCE_SCHEMA_PATH",
                    str(Path(__file__).resolve().parents[3] / "schemas" / "ookla_raw.json"),
                )
            ),
            target.strip() if target is not None else None,
        )
