"""Tests for the speed / determinism work: LLM tuning knobs and the result cache.

Two evaluations of the same startup used to disagree, because DuckDuckGo returns a different
mix of results each run AND the model sampled at temperature 1.0. These pin down the fixes:
deterministic sampling, reasoning disabled for extraction (measured faster *and* more accurate
than thinking), and a cache that lets a re-run replay the exact same evidence.

web.py is loaded standalone by file path — core/__init__.py pulls in pandas, and these tests
must stay dependency-light and network-free.
"""
import importlib.util
import os
import sys

import pytest

_WEB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core", "web.py"))
_spec = importlib.util.spec_from_file_location("core_web_cache_standalone", _WEB_PATH)
web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(web)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import llm as llm_mod  # noqa: E402


# ----------------------------------------------------------------- LLM tuning knobs

class _Recorder:
    """Captures the kwargs of the last create() call; optionally fails on a named parameter."""

    def __init__(self, reject=None):
        self.seen, self.calls, self._reject = {}, 0, reject

    def create(self, **kwargs):
        self.calls += 1
        self.seen = dict(kwargs)
        if self._reject and self._reject in kwargs:
            raise RuntimeError(f"Error code: 400 - Unsupported parameter: {self._reject}")
        raise RuntimeError("stop — the request itself is not under test")


def _client(provider, recorder):
    c = llm_mod.LLMClient.__new__(llm_mod.LLMClient)
    c.available, c.model, c.last_error, c.provider = True, "m", "", provider
    c._client = type("C", (), {"chat": type("Ch", (), {"completions": recorder})()})()
    return c


def test_temperature_is_zero_by_default(monkeypatch):
    """Unset, Gemini samples at 1.0 — four identical extraction calls returned three different
    JSON spellings, so re-evaluating a startup never reproduced exactly."""
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    r = _Recorder()
    _client("gemini", r).complete("hi")
    assert r.seen["temperature"] == 0


def test_reasoning_effort_only_reaches_reasoning_providers(monkeypatch):
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    r = _Recorder()
    _client("gemini", r).complete("hi", reasoning="none")
    assert r.seen["reasoning_effort"] == "none"

    r2 = _Recorder()
    _client("openai", r2).complete("hi", reasoning="none")
    assert "reasoning_effort" not in r2.seen      # a non-reasoning gateway model never sees it


def test_gateway_reasoning_model_gets_effort_headroom_and_valid_temperature(monkeypatch):
    """gpt-5.x bills reasoning against the cap and rejects temperature 0 unless reasoning is off."""
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    monkeypatch.setattr(llm_mod, "LLM_REASONING_EFFORT", "")
    r = _Recorder()
    c = _client("openai", r)
    c.model = "gpt-5.4"
    c.complete("hi", max_tokens=900, reasoning="none")
    assert r.seen["reasoning_effort"] == "none"
    assert r.seen["temperature"] == 0
    assert r.seen["max_completion_tokens"] == max(900, llm_mod.LLM_MIN_BUDGET) + llm_mod.LLM_THINKING_HEADROOM

    r2 = _Recorder()
    c._client = type("C", (), {"chat": type("Ch", (), {"completions": r2})()})()
    c.complete("hi", max_tokens=900)
    assert "reasoning_effort" not in r2.seen
    assert "temperature" not in r2.seen


@pytest.mark.parametrize("provider, model", [("gemini", "gemini-3.5-flash-lite"),
                                              ("openai", "google/gemini-3.5-flash-lite")])
def test_gemini_3_gets_minimal_thinking_where_a_call_asks_for_none(monkeypatch, provider, model):
    """Gemini 3 rejects reasoning_effort "none" with a 400 naming no parameter, so it was never
    dropped and retried: every evidence-to-JSON call failed. Its floor is "minimal"."""
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    r = _Recorder()
    c = _client(provider, r)
    c.model = model
    c.complete("hi", max_tokens=900, reasoning="none")
    assert r.seen["reasoning_effort"] == "minimal"
    assert r.seen["temperature"] == 0                      # only the gateway's gpt-5.x rejects it
    assert r.seen["max_completion_tokens"] == max(900, llm_mod.LLM_MIN_BUDGET) + llm_mod.LLM_THINKING_HEADROOM


def test_gemini_2_5_still_switches_thinking_off(monkeypatch):
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    r = _Recorder()
    c = _client("gemini", r)
    c.model = "gemini-2.5-flash"
    c.complete("hi", reasoning="none")
    assert r.seen["reasoning_effort"] == "none"


def test_headroom_applies_even_with_reasoning_off(monkeypatch):
    """The ceiling must stay generous regardless of reasoning.

    Trimming it to the "no thought tokens needed" figure truncated the biggest profile
    extractions mid-JSON, silently dropping them back to keyword-only — the per-call
    max_tokens values were never sized for a full reply on their own, and
    max_completion_tokens is a cap rather than a charge.
    """
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    for kwargs in ({"reasoning": "none"}, {}):
        r = _Recorder()
        _client("gemini", r).complete("hi", max_tokens=1200, **kwargs)
        assert r.seen["max_completion_tokens"] == max(1200, llm_mod.LLM_MIN_BUDGET) + llm_mod.LLM_THINKING_HEADROOM


def test_rejected_parameter_is_dropped_and_retried(monkeypatch):
    """A gateway that rejects a tuning parameter must not silently kill every LLM call."""
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 3)
    monkeypatch.setattr(llm_mod, "RETRY_BACKOFF", 0)
    r = _Recorder(reject="reasoning_effort")
    _client("gemini", r).complete("hi", reasoning="none")
    assert r.calls >= 2
    assert "reasoning_effort" not in r.seen       # retried without it
    # Dropping reasoning puts the thinking headroom back, since the model will now think.
    assert r.seen["max_completion_tokens"] > 1200


def test_ordinary_errors_still_use_the_retry_path(monkeypatch):
    """A timeout must not be mistaken for a parameter complaint and strip the request."""
    assert llm_mod._unsupported_param(RuntimeError("Read timed out"), {"temperature": 0}) == ""
    assert llm_mod._unsupported_param(RuntimeError("500 server error"), {"temperature": 0}) == ""
    assert llm_mod._unsupported_param(
        RuntimeError("400 invalid: temperature"), {"temperature": 0}) == "temperature"


# ----------------------------------------------------------------------- result cache

def _install_dict_cache():
    store = {}
    web.install_cache(lambda kind, key: store.get((kind, key)),
                      lambda kind, key, payload: store.__setitem__((kind, key), payload))
    return store


def _uninstall_cache():
    web.install_cache(None, None)


def test_repeat_search_is_served_from_cache(monkeypatch):
    """The second identical query must not touch the network — this is both the speed win
    and the reason a re-run reproduces the same evidence."""
    store = _install_dict_cache()
    try:
        calls = []
        monkeypatch.setattr(web, "ddg_search", web.ddg_search)   # keep the real function

        class _DDGS:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def text(self, q, max_results=5):
                calls.append(q)
                return [{"title": "hit", "href": "https://e/1"}]

        monkeypatch.setitem(sys.modules, "ddgs",
                            type("m", (), {"DDGS": _DDGS}))
        first = web.ddg_search("acme founders", max_results=4)
        second = web.ddg_search("acme founders", max_results=4)
        assert first == second
        assert len(calls) == 1                       # served from cache the second time
        assert len(store) == 1
    finally:
        _uninstall_cache()


def test_cache_bypass_forces_a_fresh_search(monkeypatch):
    """'Re-evaluate' must re-search rather than replay week-old results."""
    _install_dict_cache()
    try:
        calls = []

        class _DDGS:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def text(self, q, max_results=5):
                calls.append(q)
                return [{"title": "hit", "href": "https://e/1"}]

        monkeypatch.setitem(sys.modules, "ddgs", type("m", (), {"DDGS": _DDGS}))
        web.ddg_search("acme", max_results=4)
        token = web.set_cache_enabled(False)
        try:
            web.ddg_search("acme", max_results=4)
        finally:
            web.reset_cache_enabled(token)
        assert len(calls) == 2                       # the bypass really hit the network
    finally:
        _uninstall_cache()


def test_bypass_flag_reaches_the_search_worker_threads(monkeypatch):
    """_ddg_many runs its searches in fresh threads, which start with an EMPTY context.

    Without copying the caller's context into each worker the ContextVar falls back to its
    default (True) and a forced refresh quietly reads from the cache anyway.
    """
    seen = []
    # **kw, not a fixed arity: _ddg_many's worker swallows every exception from ddg_search, so a
    # stub whose signature has fallen behind the real one fails as an empty result rather than as
    # a TypeError — which reads exactly like the bug this test exists to catch.
    monkeypatch.setattr(web, "ddg_search",
                        lambda q, n=4, **kw: seen.append(web._cache_enabled.get()) or [])
    token = web.set_cache_enabled(False)
    try:
        web._ddg_many({"a": "one", "b": "two"}, overall_timeout=5)
    finally:
        web.reset_cache_enabled(token)
    assert seen and all(flag is False for flag in seen)


def test_pipeline_executor_carries_the_bypass_flag(monkeypatch):
    """pipeline.evaluate fans its stages out through a ThreadPoolExecutor.

    Those threads start with an empty context too, so submitting plainly lost the bypass flag
    for the profile and trend research — most of a run — and a forced refresh quietly replayed
    cached searches and completions.
    """
    import concurrent.futures
    import contextvars
    import functools

    seen = []

    def stage():
        seen.append(web._cache_enabled.get())

    token = web.set_cache_enabled(False)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            plain = ex.submit(stage)
            copied = ex.submit(contextvars.copy_context().run, functools.partial(stage))
            plain.result(), copied.result()
    finally:
        web.reset_cache_enabled(token)
    # The plain submit sees the default; only the context-copying one sees the real flag.
    assert True in seen and False in seen


def test_failed_search_is_not_cached(monkeypatch):
    """A transient throttle must not be pinned in place for the whole TTL."""
    store = _install_dict_cache()
    try:
        class _DDGS:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def text(self, q, max_results=5):
                raise RuntimeError("throttled")

        monkeypatch.setitem(sys.modules, "ddgs", type("m", (), {"DDGS": _DDGS}))
        assert web.ddg_search("acme", max_results=4, attempts=1) == []
        assert store == {}                           # nothing cached
    finally:
        _uninstall_cache()


def test_cache_faults_never_break_an_evaluation(monkeypatch):
    """The cache is an optimisation; a broken backend must degrade, not raise."""
    def boom(*a, **k):
        raise RuntimeError("cache backend down")

    web.install_cache(boom, boom)
    try:
        class _DDGS:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def text(self, q, max_results=5):
                return [{"title": "hit"}]

        monkeypatch.setitem(sys.modules, "ddgs", type("m", (), {"DDGS": _DDGS}))
        assert web.ddg_search("acme", max_results=4) == [{"title": "hit"}]
    finally:
        _uninstall_cache()


def test_site_fetch_cache_preserves_the_resolved_url(monkeypatch):
    """fetch_site_text derives the locale prefix from the RESOLVED url.

    Caching only the text would lose it, and a cached run would then follow a different set of
    paths than the live one — the opposite of reproducible.
    """
    _install_dict_cache()
    try:
        hits = []
        monkeypatch.setattr(web, "_fetch_with_url",
                            lambda url, **k: hits.append(url) or ("body", "https://a.com/en"))
        assert web._fetch_cached("https://a.com") == ("body", "https://a.com/en")
        assert web._fetch_cached("https://a.com") == ("body", "https://a.com/en")
        assert len(hits) == 1
    finally:
        _uninstall_cache()


# ----------------------------------------------------------------- one search limit per process

def _slow_search(state, delay):
    import threading
    import time
    lock = threading.Lock()

    def search(query, max_results=5, meta=None):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(delay)
        with lock:
            state["now"] -= 1
        return [{"title": query, "href": "https://x.example", "body": query}]
    return search


def test_overlapping_waves_share_one_limit_on_searches_in_flight(monkeypatch):
    """Enrichment, profile and trend now search at the same time, and so do several reviewers'
    runs. Each wave capping only itself let those add up to bursts the engines answer with 429s."""
    import threading
    state = {"now": 0, "peak": 0}
    monkeypatch.setattr(web, "_ddg_slots", threading.BoundedSemaphore(3))
    monkeypatch.setattr(web, "ddg_search", _slow_search(state, 0.05))
    results = []
    waves = [threading.Thread(target=lambda i=i: results.append(
        web._ddg_many({f"q{i}_{j}": f"wave {i} query {j}" for j in range(4)}, overall_timeout=10)))
        for i in range(3)]
    for t in waves:
        t.start()
    for t in waves:
        t.join()
    assert state["peak"] == 3
    assert all(all(hits for hits in r.values()) for r in results)   # nothing abandoned


def test_time_queued_behind_another_wave_is_not_charged_to_the_deadline(monkeypatch):
    """A wave's deadline exists to cut off throttled searches. A search still waiting for a slot
    has not been throttled — it has not run — so abandoning it would invent a missing field."""
    import threading
    import time
    monkeypatch.setattr(web, "_ddg_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(web, "ddg_search", _slow_search({"now": 0, "peak": 0}, 0.01))
    web._ddg_slots.acquire()                       # another wave's search holds the only slot
    threading.Timer(0.3, web._ddg_slots.release).start()
    stats: dict = {}
    started = time.time()
    out = web._ddg_many({"a": "only query"}, overall_timeout=0.2, stats=stats)
    assert out["a"] and stats["timed_out"] == 0
    assert time.time() - started >= 0.3


# ----------------------------------------------------------------- rate limits and spending caps

class _Limited:
    """create() raises a provider 429 with the given message, counting calls."""

    def __init__(self, message):
        self.calls, self.message = 0, message

    def create(self, **kwargs):
        self.calls += 1
        err = RuntimeError(f"Error code: 429 - {self.message}")
        err.status_code = 429
        raise err


def test_a_spending_cap_fails_at_once_instead_of_spending_the_run_on_retries(monkeypatch):
    """A monthly cap does not lift inside a run; three attempts were ~13s per call, every call."""
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    r = _Limited("Your project has exceeded its monthly spending cap. RESOURCE_EXHAUSTED")
    assert _client("gemini", r).complete("hi") == ""
    assert r.calls == 1


def test_a_per_minute_limit_is_waited_out_across_a_quota_window(monkeypatch):
    waits = []
    monkeypatch.setattr(llm_mod.time, "sleep", waits.append)
    r = _Limited("Rate limit reached for requests per minute. RESOURCE_EXHAUSTED")
    assert _client("gemini", r).complete("hi") == ""
    assert r.calls == llm_mod.RATE_LIMIT_ATTEMPTS > llm_mod.MAX_RETRIES
    assert sum(waits) >= 30                                  # long enough for a minute window to turn


def test_a_caller_that_asked_for_one_attempt_still_gets_one(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    r = _Limited("Rate limit reached for requests per minute.")
    _client("gemini", r).complete("hi", max_attempts=1)
    assert r.calls == 1


# ----------------------------------------------------------------- run timeline

def test_a_search_wave_is_recorded_on_the_run_timeline(monkeypatch):
    """Search waves are most of the profile branch's time and were invisible: the token log only
    holds model calls. Each wave now leaves one timeline event with its size and its losses."""
    monkeypatch.setattr(web, "ddg_search", lambda q, n, meta=None: [{"href": "https://a.example", "body": q}])
    token, timeline = web.collect_timeline()
    try:
        web._ddg_many({"a": "first", "b": "second"}, stats={})
    finally:
        web.stop_collecting_timeline(token)
    (wave,) = timeline["events"]
    assert wave["kind"] == "search_wave"
    assert (wave["queries"], wave["timed_out"], wave["cached"]) == (2, 0, 0)
    assert wave["start_ms"] >= 0 and wave["duration_ms"] >= 0 and wave["queued_ms"] >= 0


def test_no_timeline_is_kept_outside_a_run(monkeypatch):
    monkeypatch.setattr(web, "ddg_search", lambda q, n, meta=None: [])
    web._ddg_many({"a": "query"})                  # must not raise, and has nowhere to record
    assert web.run_offset_ms(0.0) is None


# ----------------------------------------------------------------- hedged thinking calls

class _Raced:
    """Answers each create() with the next (delay, text-or-exception) in turn."""

    def __init__(self, *script):
        import threading
        self.script, self.calls, self._lock = list(script), 0, threading.Lock()

    def create(self, **kwargs):
        import time
        with self._lock:
            delay, out = self.script[self.calls]
            self.calls += 1
        time.sleep(delay)
        if isinstance(out, Exception):
            raise out
        usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                               "completion_tokens_details": None})()
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": out})()})()],
                              "usage": usage})()


def _logged(monkeypatch):
    monkeypatch.setattr(llm_mod, "LLM_CACHE", False)
    token, log = llm_mod.collect_usage()
    return token, log


def test_a_slow_thinking_call_is_raced_by_a_duplicate_and_the_first_answer_wins(monkeypatch):
    """Waiting out a slow request and then retrying from scratch threw away the whole wait. A
    duplicate sent at hedge_after races it, and both are logged because both are billed."""
    import time
    token, log = _logged(monkeypatch)
    try:
        r = _Raced((0.6, "slow"), (0.0, "fast"))
        started = time.time()
        assert _client("gemini", r).complete("p", hedge_after=0.05) == "fast"
        assert time.time() - started < 0.5
        time.sleep(0.7)                              # the loser lands and is logged
    finally:
        llm_mod.stop_collecting_usage(token)
    assert r.calls == 2
    winner, loser = log
    assert winner.get("hedged") is True and winner["ok"] and winner["total_tokens"] == 15
    assert loser["reason"] == "hedge_loser" and loser["total_tokens"] == 15


def test_no_duplicate_is_sent_when_the_first_answer_arrives_in_time(monkeypatch):
    token, log = _logged(monkeypatch)
    try:
        r = _Raced((0.0, "quick"))
        assert _client("gemini", r).complete("p", hedge_after=1.0) == "quick"
    finally:
        llm_mod.stop_collecting_usage(token)
    assert r.calls == 1 and "hedged" not in log[0]


def test_a_request_that_fails_fast_takes_the_retry_path_not_the_hedge(monkeypatch):
    """A request's own timeout is an error to retry, not a slow answer to race."""
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    token, log = _logged(monkeypatch)
    try:
        r = _Raced((0.0, TimeoutError("Request timed out.")), (0.0, "second attempt"))
        assert _client("gemini", r).complete("p", hedge_after=1.0, max_attempts=2) == "second attempt"
    finally:
        llm_mod.stop_collecting_usage(token)
    assert r.calls == 2 and log[-1]["attempts"] == 2 and "hedged" not in log[-1]


def test_when_both_racers_fail_the_call_fails_like_a_single_request(monkeypatch):
    token, log = _logged(monkeypatch)
    try:
        r = _Raced((0.2, RuntimeError("boom")), (0.0, RuntimeError("boom")))
        assert _client("gemini", r).complete("p", hedge_after=0.05, max_attempts=1) == ""
    finally:
        llm_mod.stop_collecting_usage(token)
    assert r.calls == 2 and log[-1]["ok"] is False


# ----------------------------------------------------------------- truncation

class _Answers:
    """Answers each create() from ``replies`` (finish_reason, text) in turn, recording the kwargs."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def create(self, **kwargs):
        from types import SimpleNamespace as NS
        self.seen.append(dict(kwargs))
        finish, text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return NS(choices=[NS(finish_reason=finish, message=NS(content=text))], usage=None)


def test_a_reply_cut_off_at_its_ceiling_is_asked_again_with_room_and_not_cached(monkeypatch):
    """gpt-5.4 stopped the profile extraction mid-JSON at its ceiling; parse_json rejected the
    half object and the profile fell back to keyword-only — no founders, team or customers."""
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    stored = []
    monkeypatch.setattr(llm_mod._web, "_cached", lambda *a, **k: None)
    monkeypatch.setattr(llm_mod._web, "_store", lambda *a, **k: stored.append(a))
    a = _Answers(("length", '{"founders": [{"na'), ("stop", '{"founders": []}'))
    out = _client("openai", a).complete("hi", max_tokens=1200, reasoning="none")
    assert out == '{"founders": []}'
    assert a.seen[1]["max_completion_tokens"] > a.seen[0]["max_completion_tokens"]
    assert len(stored) == 1                       # only the complete reply

    stored.clear()
    a = _Answers(("length", '{"cut'))
    _client("openai", a).complete("hi", reasoning="none")
    assert a.seen[-1]["max_completion_tokens"] == llm_mod.LLM_MAX_BUDGET
    assert stored == []                           # a reply still cut off is never cached


def test_a_refused_effort_teaches_the_client_the_models_floor(monkeypatch):
    """gpt-6.1-sol refuses reasoning_effort "none" and names what it accepts. The client learns the
    floor from the first refusal and every later call uses it, so nothing is sent twice."""
    from types import SimpleNamespace
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 2)
    monkeypatch.setattr(llm_mod, "LLM_CACHE", False)
    monkeypatch.setattr(llm_mod, "_EFFORTS", {})
    sent = []

    class Sol:
        def create(self, **kw):
            sent.append(dict(kw))
            if kw.get("reasoning_effort") == "none":
                raise RuntimeError("Error code: 400 - {'error': {'message': \"Unsupported value: 'reasoning_effort' does not "
                                   "support 'none' with this model. Supported values are: 'low', 'medium', 'high', and "
                                   "'xhigh'.\", 'type': 'invalid_request_error', 'param': 'reasoning_effort'}}")
            msg = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=None)

    c = _client("openai", Sol())
    c.model = "gpt-6.1-sol"
    assert c.complete("first", reasoning="none") == "ok"
    assert [s["reasoning_effort"] for s in sent] == ["none", "low"] and "temperature" not in sent[1]
    sent.clear()
    assert c.complete("second", reasoning="none") == "ok"
    assert [s["reasoning_effort"] for s in sent] == ["low"]                       # straight to the floor
