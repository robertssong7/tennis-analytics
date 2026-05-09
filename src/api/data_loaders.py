"""S3-backed loaders for large data artifacts that are too big for git.

The bucket is `tennisiq-data-assets` and the canonical artifacts live under
`processed/`. We download lazily on first use into the local data directory
so subsequent reads are zero-network.

Module is import-safe — boto3 is only required when an actual download is
needed. If the local file is already present, no AWS calls happen.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

S3_BUCKET = "tennisiq-data-assets"
LOCAL_DATA_DIR = Path("data/processed")


def ensure_local(filename: str, s3_key_prefix: str = "processed/") -> Path:
    """Return local path for a file, downloading from S3 if absent.

    Idempotent: if the local file already exists at non-trivial size we
    skip the network call entirely. We treat <1MB as "probably a partial
    or placeholder" and re-fetch.
    """
    local_path = LOCAL_DATA_DIR / filename
    if local_path.exists() and local_path.stat().st_size > 1_000_000:
        return local_path

    LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    s3_key = f"{s3_key_prefix}{filename}"
    try:
        import boto3  # local import keeps cold start cheap when file is present

        s3 = boto3.client("s3")
        logger.info(f"Downloading s3://{S3_BUCKET}/{s3_key} to {local_path}")
        s3.download_file(S3_BUCKET, s3_key, str(local_path))
        logger.info(
            f"Downloaded {local_path} ({local_path.stat().st_size:,} bytes)"
        )
    except Exception as exc:
        logger.warning(f"S3 download failed for {filename}: {exc}")
    return local_path


def load_parsed_points(columns: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """Load parsed_points.parquet, fetching from S3 if not on disk.

    `columns` is forwarded to `pd.read_parquet` to keep peak memory low.
    Raises FileNotFoundError if the file is not local AND the S3 download
    failed; callers should handle that to degrade gracefully.
    """
    path = ensure_local("parsed_points.parquet")
    if not path.exists() or path.stat().st_size < 1_000_000:
        raise FileNotFoundError(
            "parsed_points.parquet unavailable locally and S3 download failed."
        )
    return pd.read_parquet(path, columns=list(columns) if columns else None)
