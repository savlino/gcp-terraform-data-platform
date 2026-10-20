import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ingest.config import schema_path


@dataclass(frozen=True)
class BuildConfig:
    dataset_id: str
    location: str
    bbox: tuple[float, float, float, float]
    maximum_bytes_billed: int
    mart_zoom: int
    minimum_row_percent: float
    latency_ceiling_ms: float
    schema_path: Path

    @classmethod
    def from_env(cls) -> "BuildConfig":
        dataset = os.environ.get("BIGQUERY_DATASET", "network_perf")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,1023}", dataset):
            raise ValueError("BIGQUERY_DATASET must be a valid dataset ID.")
        location = os.environ.get("REGION", "europe-southwest1").strip()
        if not location:
            raise ValueError("REGION must not be empty.")
        box = json.loads(
            os.environ.get(
                "REGION_BBOX", '{"min_lon":-9.6,"max_lon":4.4,"min_lat":35.8,"max_lat":43.9}'
            )
        )
        bbox = tuple(float(box[key]) for key in ("min_lon", "max_lon", "min_lat", "max_lat"))
        min_lon, max_lon, min_lat, max_lat = bbox
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("REGION_BBOX must have ordered coordinates in WGS84 ranges.")
        budget = int(os.environ.get("MAXIMUM_BYTES_BILLED", str(10 * 1024**3)))
        zoom = int(os.environ.get("MART_ZOOM", "10"))
        percent = float(os.environ.get("MINIMUM_ROW_PERCENT", "50"))
        ceiling = float(os.environ.get("LATENCY_CEILING_MS", "10000"))
        if budget <= 0 or not 1 <= zoom <= 16:
            raise ValueError("MAXIMUM_BYTES_BILLED must be positive; MART_ZOOM must be 1..16.")
        if not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("MINIMUM_ROW_PERCENT must be finite and within 0..100.")
        if not math.isfinite(ceiling) or ceiling <= 0:
            raise ValueError("LATENCY_CEILING_MS must be finite and positive.")
        return cls(
            dataset,
            location,
            (min_lon, max_lon, min_lat, max_lat),
            budget,
            zoom,
            percent,
            ceiling,
            schema_path("ookla_tiles.json", "NATIVE_SCHEMA_PATH"),
        )
