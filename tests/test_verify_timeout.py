"""Fact verification gets a timeout sized for the call it is, not the 30s default.

Measured at 18-25s on a cold run, it crossed 30s under load, was killed and retried from scratch
three times (96s on the critical path) and the run fell back to heuristics, marked degraded."""
import pandas as pd

from core import verify


class _LLM:
    available = True

    def __init__(self):
        self.kwargs = None

    def complete(self, prompt, **kwargs):
        self.kwargs = kwargs
        return '{"claims": [], "red_flags": []}'


def test_verification_waits_for_a_slow_answer_instead_of_retrying_it():
    llm = _LLM()
    row = pd.Series({"company_name": "Acme", "funding": "$2M seed"})
    enrichment = {"web": {"funding_web": [{"title": "Acme raises", "href": "https://x.example", "body": "seed"}]}}
    out = verify.verify_facts(row, enrichment, llm)
    assert out["method"] == "llm"
    assert llm.kwargs["timeout"] >= 60
    assert llm.kwargs["max_attempts"] == 2      # three at 90s would pass gunicorn's 300s limit


def test_trend_momentum_waits_for_a_slow_answer_instead_of_retrying_it(monkeypatch):
    """The momentum call thinks for ~23s cold; with twelve evaluations at once it crossed 30s, was
    retried from scratch three times and the run lost its trend (and Market's figures)."""
    from core import trend
    seen = []

    class LLM:
        available = True

        def complete(self, prompt, **kwargs):
            seen.append(kwargs)
            return ""
    monkeypatch.setattr(trend, "_ddg_many", lambda q, **kw: {k: [{"title": "t", "href": "https://x.example", "body": "b"}] for k in q})
    monkeypatch.setattr(trend, "_read_size_pages", lambda *a, **kw: [])
    trend.analyze_trend(pd.Series({"company_name": "Acme", "Your pitch": "Machine vision"}), "", [], LLM(), True)
    momentum = [k for k in seen if k.get("max_tokens") == 800]
    assert momentum and momentum[0]["timeout"] >= 60 and momentum[0]["max_attempts"] == 2
