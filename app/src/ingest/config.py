import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

BUCKET_PATTERN = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
REGION_PATTERN = re.compile(r"[a-z]{2}(-[a-z]+)+-[0-9]")


def schema_path(filename: str, override: str) -> Path:
    if override in os.environ:
        return Path(os.environ[override])
    directory = os.environ.get("SCHEMAS_DIR")
    if directory is not None and not directory.strip():
        raise ValueError("SCHEMAS_DIR is set but empty.")
    root = (
        Path(directory)
        if directory is not None
        else Path(__file__).resolve().parents[3] / "schemas"
    )
    return root / filename


def parse_quarter(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"[0-9]{4}-Q[1-4]", value):
        raise ValueError("MIN_QUARTER must use YYYY-Q1 through YYYY-Q4.")
    quarter = (int(value[:4]), int(value[-1]))
    if quarter < (2023, 3):
        raise ValueError("MIN_QUARTER cannot be earlier than 2023-Q3.")
    return quarter


@dataclass(frozen=True)
class SourceConfig:
    """Public source bucket shared by HTTPS listing/download and Arrow footer reads."""

    bucket: str = "ookla-open-data"
    region: str = "us-west-2"
    endpoint_override: str | None = None

    def __post_init__(self) -> None:
        # Bucket and region become part of a hostname: validate before use.
        if not BUCKET_PATTERN.fullmatch(self.bucket) or ".." in self.bucket:
            raise ValueError("SOURCE_BUCKET must be a valid S3 bucket name.")
        if not REGION_PATTERN.fullmatch(self.region):
            raise ValueError("SOURCE_REGION must be an AWS region, e.g. us-west-2.")
        if self.endpoint_override is not None:
            parts = urlsplit(self.endpoint_override)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
                or parts.path not in ("", "/")
            ):
                raise ValueError("SOURCE_ENDPOINT must be a plain https:// origin.")

    @property
    def endpoint(self) -> str:
        if self.endpoint_override is not None:
            return self.endpoint_override.rstrip("/")
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com"

    def object_url(self, key: str) -> str:
        return f"{self.endpoint}/{quote(key, safe='/')}"

    def arrow_path(self, key: str) -> str:
        return f"{self.bucket}/{key}"

    @classmethod
    def from_env(cls) -> "SourceConfig":
        if "S3_REGION" in os.environ:
            raise ValueError("S3_REGION was renamed to SOURCE_REGION; unset S3_REGION.")
        endpoint = os.environ.get("SOURCE_ENDPOINT")
        return cls(
            os.environ.get("SOURCE_BUCKET", cls.bucket).strip(),
            os.environ.get("SOURCE_REGION", cls.region).strip(),
            endpoint.strip() if endpoint is not None else None,
        )


@dataclass(frozen=True)
class Config:
    project_id: str
    raw_bucket: str
    min_quarter: tuple[int, int]
    source: SourceConfig
    schema_path: Path
    impersonate_service_account: str | None

    @classmethod
    def from_env(cls) -> "Config":
        project = os.environ.get("PROJECT_ID", "").strip()
        bucket = os.environ.get("RAW_BUCKET", "").strip()
        if not project or not bucket or bucket.startswith("gs://"):
            raise ValueError("Set PROJECT_ID and RAW_BUCKET (bucket name without gs://).")
        target = os.environ.get("IMPERSONATE_SERVICE_ACCOUNT")
        if target is not None and not target.strip():
            raise ValueError("IMPERSONATE_SERVICE_ACCOUNT is set but empty; refusing ADC fallback.")
        return cls(
            project,
            bucket,
            parse_quarter(os.environ.get("MIN_QUARTER", "2023-Q3")),
            SourceConfig.from_env(),
            schema_path("ookla_raw.json", "SOURCE_SCHEMA_PATH"),
            target.strip() if target is not None else None,
        )
