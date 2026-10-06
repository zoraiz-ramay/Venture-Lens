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
    assert "reasoning_effort" not in r2.seen      # the Siemens gateway never sees it


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

    src = __import__("inspect").getsource(
        __import__("core.pipeline", fromlist=["evaluate"]))
    assert "copy_context" in src, "pipeline must copy context into its executor"


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


class _Reply:
    """A chat completion whose first replies stop at the token ceiling."""

    def __init__(self, cut_offs=1):
        self.seen, self.cut_offs = [], cut_offs

    def create(self, **kwargs):
        self.seen.append(dict(kwargs))
        cut = len(self.seen) <= self.cut_offs
        msg = type("M", (), {"content": '{"founders": [' if cut else '{"founders": []}'})()
        choice = type("Ch", (), {"message": msg, "finish_reason": "length" if cut else "stop"})()
        return type("R", (), {"choices": [choice], "usage": None})()


def test_truncated_reply_is_reasked_with_room_and_not_cached(monkeypatch):
    """A reply cut at its ceiling is half a JSON object; parse_json rejects it and the profile
    silently fell back to keyword-only. It must be re-asked larger, and the cut copy never cached."""
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    stored = []
    monkeypatch.setattr(llm_mod._web, "_store", lambda *a: stored.append(a))
    monkeypatch.setattr(llm_mod._web, "_cached", lambda *a: None)
    r = _Reply()
    out = _client("openai", r).complete("hi", max_tokens=1200, reasoning="none")
    assert llm_mod.LLMClient.parse_json(out) == {"founders": []}
    assert r.seen[1]["max_completion_tokens"] > r.seen[0]["max_completion_tokens"]
    assert len(stored) == 1

    stored.clear()
    r = _Reply(cut_offs=99)
    _client("openai", r).complete("hi", reasoning="none")
    assert r.seen[-1]["max_completion_tokens"] == llm_mod.LLM_MAX_BUDGET
    assert stored == []


def test_gateway_reasons_unless_the_call_opts_out(monkeypatch):
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    monkeypatch.setattr(llm_mod, "LLM_REASONING_EFFORT", "high")
    r = _Recorder()
    _client("openai", r).complete("hi")
    assert r.seen["reasoning_effort"] == "high"
    assert "temperature" not in r.seen            # gpt-5.x rejects temperature 0 while reasoning
    assert r.seen["timeout"] >= llm_mod.LLM_REASONING_TIMEOUT

    r2 = _Recorder()
    _client("openai", r2).complete("hi", reasoning="none")
    assert "reasoning_effort" not in r2.seen and r2.seen["temperature"] == 0


# ----------------------------------------------------------------- truncation and reasoning

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


def test_the_gateway_reasons_unless_a_call_opts_out(monkeypatch):
    monkeypatch.setattr(llm_mod, "MAX_RETRIES", 1)
    monkeypatch.setattr(llm_mod, "LLM_REASONING_EFFORT", "high")
    r = _Recorder()
    _client("openai", r).complete("hi")
    assert r.seen["reasoning_effort"] == "high"
    assert "temperature" not in r.seen            # gpt-5.x rejects temperature 0 while reasoning
    assert r.seen["timeout"] >= llm_mod.LLM_REASONING_TIMEOUT

    r2 = _Recorder()
    _client("openai", r2).complete("hi", reasoning="none")
    assert "reasoning_effort" not in r2.seen and r2.seen["temperature"] == 0
