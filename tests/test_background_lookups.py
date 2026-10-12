"""Profile lookups run in the background and the page polls: a minute-long market-signals search
must not depend on one HTTP request surviving the browser's and the proxy's timeouts."""
import os
import sys
import threading
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api import jobs  # noqa: E402
from api.auth import sessions  # noqa: E402


def _until_done(key, compute, **kw):
    deadline = time.time() + 5
    while True:
        out = jobs.in_background(key, compute, **kw)
        if not out.get("pending"):
            return out
        assert time.time() < deadline
        time.sleep(0.01)


def test_one_lookup_per_key_however_many_polls():
    calls, gate = [], threading.Event()

    def compute():
        calls.append(1)
        gate.wait(2)
        return {"provider": "web", "signals": [1, 2]}
    key = f"t:{time.time()}"
    assert jobs.in_background(key, compute) == {"pending": True}
    assert jobs.in_background(key, compute) == {"pending": True}
    gate.set()
    assert _until_done(key, compute) == {"provider": "web", "signals": [1, 2]}
    assert calls == [1]


def test_a_stored_answer_is_served_without_starting_a_lookup():
    started = []
    assert jobs.in_background(f"t:{time.time()}", lambda: started.append(1), ready=lambda: {"stored": True}) == {"stored": True}
    assert started == []


def test_the_poll_that_collects_a_lookup_gets_its_answer_not_the_stored_copy():
    key = f"t:{time.time()}"
    jobs.in_background(key, lambda: {"fresh": True})
    assert _until_done(key, lambda: {"fresh": True}, ready=lambda: {"stored": True}) == {"fresh": True}


def test_a_failed_lookup_is_reported_once_without_its_detail():
    key = f"t:{time.time()}"

    def boom():
        raise RuntimeError("vendor said: key sk-not-a-real-key is invalid")
    jobs.in_background(key, boom)
    with pytest.raises(HTTPException) as err:
        _until_done(key, boom)
    assert err.value.status_code == 502 and "sk-" not in err.value.detail
    assert jobs.in_background(key, lambda: {"ok": True}) == {"pending": True}     # a retry starts afresh


def test_a_lookup_left_running_past_its_ttl_is_started_again(monkeypatch):
    key = f"t:{time.time()}"
    sessions().put(f"lookup:{key}", {"status": "running", "started": time.time() - jobs.LOOKUP_TTL - 1}, 60)
    assert _until_done(key, lambda: {"again": True}) == {"again": True}
