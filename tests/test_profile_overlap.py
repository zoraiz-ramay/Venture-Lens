"""The profile branch starts what depends on nothing before the main extraction.

Measured on a cold run, the headcount and Siemens search waves queued behind the main extraction
and enrichment's whole search wave, though both read nothing but the company's name. They now
start beside the base wave; the queries, results and prompts are the same.
"""
import os
import sys
import threading

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import profile  # noqa: E402


class _LLM:
    available = True

    def complete(self, *a, **kw):
        return ""


def test_headcount_and_siemens_waves_do_not_wait_for_the_main_extraction(monkeypatch):
    both_started = threading.Event()
    seen: set = set()
    lock = threading.Lock()

    def fake_many(queries, **kw):
        with lock:
            if set(queries) == {"h1", "h2", "h3"}:
                seen.add("headcount")
            if set(queries) == set(profile._SIEMENS_QUERIES):
                seen.add("siemens")
            if {"headcount", "siemens"} <= seen:
                both_started.set()
        return {k: [] for k in queries}

    waited = {}

    def extraction_that_waits(company, row, results, llm):
        waited["both"] = both_started.wait(timeout=5)
        return None

    monkeypatch.setattr(profile, "_ddg_many", fake_many)
    monkeypatch.setattr(profile, "_llm_extract", extraction_that_waits)
    row = pd.Series({"company_name": "Acme Robotics", "founded_year": "2015", "website": "https://acme.example"})
    out = profile.research_profile(row, _LLM(), do_web=True, site={}, web={})
    assert waited["both"] is True
    # Nothing found anywhere: the headcount status still says why, as before.
    assert out["profile"]["employees_history_status"] == "not_found"


def test_a_company_founded_this_year_still_skips_the_headcount_wave(monkeypatch):
    import datetime
    asked: list = []
    monkeypatch.setattr(profile, "_ddg_many", lambda q, **kw: asked.append(set(q)) or {k: [] for k in q})
    row = pd.Series({"company_name": "Newco", "founded_year": str(datetime.date.today().year)})
    out = profile.research_profile(row, _LLM(), do_web=True, site={}, web={})
    assert {"h1", "h2", "h3"} not in asked
    assert out["profile"]["employees_history_status"] == "too_young"
