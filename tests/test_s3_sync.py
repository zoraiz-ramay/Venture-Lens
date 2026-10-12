"""The S3 copy of runs.db is complete, and a burst of writes uploads it once.

The database runs in WAL mode, so a fresh commit lives in runs.db-wal until a checkpoint. The old
upload sent runs.db alone — a copy that could lack the newest runs, which is exactly the copy a
redeployed container restores from.
"""
import os
import shutil
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api import store  # noqa: E402


@pytest.fixture
def wal_db(tmp_path, monkeypatch):
    path = str(tmp_path / "runs.db")
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")      # keep commits in the WAL, as between checkpoints
    con.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, company TEXT)")
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # the schema is in runs.db; new rows are not
    monkeypatch.setattr(store, "DB_PATH", path)
    yield path, con
    con.close()


def _companies(path):
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT company FROM runs")]
    finally:
        con.close()


def test_a_bare_file_copy_misses_a_wal_commit_and_the_snapshot_does_not(wal_db, tmp_path):
    path, con = wal_db
    con.execute("INSERT INTO runs (company) VALUES ('Wandelbots')")
    con.commit()
    bare = str(tmp_path / "bare.db")
    shutil.copy(path, bare)                          # what upload_file(DB_PATH) used to send
    assert _companies(bare) == []
    snap = str(tmp_path / "snap.db")
    store._snapshot(snap)
    assert _companies(snap) == ["Wandelbots"]


@pytest.fixture
def fake_s3(monkeypatch, tmp_path):
    uploads = []

    class Client:
        def upload_file(self, src, bucket, key):
            if key == store._S3_DB_KEY:             # not the dated daily copy
                uploads.append(_companies(src))

    monkeypatch.setattr(store, "_s3_available", lambda: True)
    monkeypatch.setattr(store, "_s3_client", lambda: Client())
    monkeypatch.setattr(store, "_upload_scheduled", False)
    return uploads


def test_a_burst_of_writes_is_uploaded_once_and_completely(wal_db, fake_s3, monkeypatch):
    path, con = wal_db
    monkeypatch.setattr(store, "S3_UPLOAD_DELAY", 0.1)
    for name in ("A", "B", "C", "D", "E"):
        con.execute("INSERT INTO runs (company) VALUES (?)", (name,))
        con.commit()
        store._upload_to_s3()
    deadline = time.time() + 5
    while not fake_s3 and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    assert fake_s3 == [["A", "B", "C", "D", "E"]]


def test_a_pending_upload_is_flushed_at_shutdown(wal_db, fake_s3, monkeypatch):
    path, con = wal_db
    monkeypatch.setattr(store, "S3_UPLOAD_DELAY", 3600)
    con.execute("INSERT INTO runs (company) VALUES ('Celonis')")
    con.commit()
    store._upload_to_s3()
    assert fake_s3 == []
    store._flush_pending_upload()
    assert fake_s3 == [["Celonis"]]


# ----------------------------------------------------------------- a redeploy keeps every stored run

class _ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


@pytest.fixture
def fresh_container(tmp_path, monkeypatch):
    """A new container: no runs.db on disk, S3 reachable through the instance role."""
    path = str(tmp_path / "data" / "runs.db")
    monkeypatch.setattr(store, "DB_PATH", path)
    monkeypatch.setattr(store, "_restored", False)
    monkeypatch.setattr(store, "_upload_scheduled", False)
    monkeypatch.setattr(store, "_backed_up_on", "")
    monkeypatch.setattr(store, "sync_state", {"restore": "pending", "blocked": False,
                                              "last_upload": "", "last_error": ""})
    monkeypatch.setattr(store, "_s3_available", lambda: True)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return path


def _client(monkeypatch, download):
    calls = {"uploads": []}

    class Client:
        def download_file(self, bucket, key, dest):
            download(dest)

        def upload_file(self, src, bucket, key):
            calls["uploads"].append((key, _companies(src)))

    monkeypatch.setattr(store, "_s3_client", lambda: Client())
    return calls


def _stored_db(dest, companies):
    con = sqlite3.connect(dest)
    con.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, company TEXT)")
    con.executemany("INSERT INTO runs (company) VALUES (?)", [(c,) for c in companies])
    con.commit()
    con.close()


def test_a_new_container_comes_back_with_the_stored_runs(fresh_container, monkeypatch):
    _client(monkeypatch, lambda dest: _stored_db(dest, ["Wandelbots", "Acme"]))
    store._restore_from_s3()
    assert _companies(fresh_container) == ["Wandelbots", "Acme"]
    assert store.sync_state["restore"] == "restored" and not store.sync_state["blocked"]


def test_no_copy_in_s3_yet_starts_fresh_and_saves(fresh_container, monkeypatch):
    def missing(dest):
        raise _ClientError("404")
    calls = _client(monkeypatch, missing)
    store._restore_from_s3()
    assert store.sync_state["restore"] == "fresh"
    _stored_db(fresh_container, ["New"])
    store._upload_now()
    assert ("data/runs.db", ["New"]) in calls["uploads"]


def test_a_failed_restore_never_uploads_the_empty_database_over_the_stored_one(fresh_container, monkeypatch):
    """A throttled or denied download is not "no copy": uploading the empty file this container
    then creates would wipe every stored run. Neither worker may upload."""
    tries = []

    def denied(dest):
        tries.append(1)
        raise _ClientError("SlowDown")
    calls = _client(monkeypatch, denied)
    store._restore_from_s3()
    assert len(tries) == 4 and store.sync_state["blocked"]
    _stored_db(fresh_container, [])                  # the schema _conn creates afterwards
    store._upload_to_s3()
    store._upload_now()
    # The second gunicorn worker finds a runs.db on disk, but it is the empty one.
    monkeypatch.setattr(store, "_restored", False)
    monkeypatch.setattr(store, "sync_state", {"restore": "pending", "blocked": False,
                                              "last_upload": "", "last_error": ""})
    store._restore_from_s3()
    store._upload_now()
    assert store.sync_state["blocked"] and calls["uploads"] == []


def test_a_transient_failure_is_retried_before_giving_up(fresh_container, monkeypatch):
    tries = []

    def flaky(dest):
        tries.append(1)
        if len(tries) < 3:
            raise _ClientError("503")
        _stored_db(dest, ["Wandelbots"])
    _client(monkeypatch, flaky)
    store._restore_from_s3()
    assert _companies(fresh_container) == ["Wandelbots"] and not store.sync_state["blocked"]


def test_the_first_upload_each_day_is_also_kept_under_its_date(fresh_container, monkeypatch):
    calls = _client(monkeypatch, lambda dest: _stored_db(dest, ["A"]))
    store._restore_from_s3()
    store._upload_now()
    store._upload_now()
    keys = [k for k, _ in calls["uploads"]]
    assert keys.count("data/runs.db") == 2
    assert len([k for k in keys if k.startswith("data/backups/runs-")]) == 1


def test_s3_uses_the_instance_role_and_this_apps_bucket(monkeypatch):
    from core import s3
    made = {}
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: made.update(kw) or object())
    s3._client()
    assert "aws_access_key_id" not in made and "aws_secret_access_key" not in made
    assert s3.S3_BUCKET == os.getenv("S3_BUCKET", "hydra-data-app-venture-lens-hydra")
    monkeypatch.setenv("S3_SYNC", "0")
    assert s3._available() is False
