"""Test environment, set before anything imports the app.

`api.store` and `api.security` both read configuration at module scope, so these have to
land in os.environ before the first import of either — which is why this is module-level
code and not a fixture.

APP_ENV is deliberately left unset: it is what arms the production seal in api/auth.py, and
the suite should exercise the same code path a developer's laptop does, not a special one.
"""
from __future__ import annotations

import os
import pathlib
import tempfile

os.environ["AUTH_MODE"] = "stub"
os.environ["SESSION_BACKEND"] = "memory"
os.environ.setdefault("RUNS_DB", str(pathlib.Path(tempfile.gettempdir()) / "sea_test_runs.db"))
os.environ.pop("APP_ENV", None)
# Set (to blank) rather than popped: load_dotenv never overrides a key already in os.environ,
# so this is what stops a developer's .env from shipping every test request to their SigNoz.
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
# "auto" would search through the gateway whenever a developer's key is set, and the search tests
# drive the DuckDuckGo path with fakes. Tests of "auto" itself set it explicitly.
os.environ["SEARCH_BACKEND"] = "ddg"
# S3 is found through boto3's default credential chain, so any AWS credentials on the machine
# would have every saved run in the suite uploaded to the real bucket. Tests of S3 fake it.
os.environ["S3_SYNC"] = "0"
