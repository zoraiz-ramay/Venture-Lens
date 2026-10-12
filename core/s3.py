"""S3 downloads: pitch-deck PDFs and the GlassDollar data file.

PDFs are stored in the S3 bucket defined by S3_BUCKET (env var). When a pitch deck is needed but
not present locally, `fetch_pdf` (called from `data._resolve_pdf`) downloads it to a
container-local cache dir so pypdf can read it without changes elsewhere. `fetch_data_file` restores
the GlassDollar export at API startup (`api/main.py`). The runs.db sync lives in `api/store.py`.
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import logging

log = logging.getLogger(__name__)

# This app's own bucket. The platform names it hydra-data-app-<repo slug> and grants the deployed
# container's instance role access to it. The old default named the bucket of the app this one was
# copied from, which this app's role cannot reach.
S3_BUCKET = os.getenv("S3_BUCKET", "hydra-data-app-venture-lens-hydra")
S3_REGION = os.getenv("AWS_REGION", "us-west-2")
S3_PREFIX = "pdfs/"

# Container-local cache so we don't re-download the same PDF twice per session
_CACHE_DIR = pathlib.Path(tempfile.gettempdir()) / "pdf_s3_cache"


def _client():
    # No keys passed: the deployed container's instance role is found by boto3's default chain.
    # This used to require HYDRA_DATA_AWS_* keys, which the platform does not give deployed apps,
    # so S3 was silently off in production and every redeploy started from an empty database.
    import boto3
    return boto3.client("s3", region_name=S3_REGION)


_creds_found: bool | None = None


def _available() -> bool:
    """Whether S3 can be reached: credentials resolve and S3_SYNC is not "0".

    Resolved once per process. Locally there are no AWS credentials, so S3 stays off and the
    database is just a file."""
    global _creds_found
    if os.getenv("S3_SYNC", "1").strip() == "0":
        return False
    if _creds_found is None:
        try:
            import boto3
            _creds_found = boto3.Session().get_credentials() is not None
        except Exception:
            _creds_found = False
        log.info("S3 %s (bucket %s)", "available" if _creds_found else "unavailable: no AWS credentials",
                 S3_BUCKET)
    return _creds_found


def fetch_pdf(basename: str) -> str:
    """Download a PDF from S3 to the local cache and return its local path.

    Returns empty string when unavailable (no creds, not found, or error).
    """
    if not _available():
        return ""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local = _CACHE_DIR / basename
    if local.exists():
        return str(local)
    key = f"{S3_PREFIX}{basename}"
    try:
        _client().download_file(S3_BUCKET, key, str(local))
        log.info("Downloaded s3://%s/%s -> %s", S3_BUCKET, key, local)
        return str(local)
    except Exception as exc:
        log.debug("S3 fetch failed for %s: %s", key, exc)
        return ""





def fetch_data_file(s3_key: str, local_path: str) -> str:
    """Download an arbitrary data file from S3 to a local path. Returns the local path on success."""
    if not _available():
        return ""
    if pathlib.Path(local_path).exists():
        return local_path
    try:
        pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        _client().download_file(S3_BUCKET, s3_key, local_path)
        log.info("Downloaded s3://%s/%s -> %s", S3_BUCKET, s3_key, local_path)
        return local_path
    except Exception as exc:
        log.debug("S3 fetch failed for %s: %s", s3_key, exc)
        return ""
