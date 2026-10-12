"""Solve a Problem never fails as a whole, and never reports a failure as "no matches".

A market error used to fail the job and take the already-shown applicants with it; a web wave that
timed out read as "no matches"; a ranking reply that could not be read silently became a
shared-words ranking. Each is pinned here with fakes: the search finishes, and the source or the
candidate says what happened.
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import pandas as pd
import pytest

from core import catalogs, solve

PROBLEM = "We need to predict failures on legacy PLC lines."


def _catalog():
    return catalogs.applications_catalog_from_df(pd.DataFrame([
        {"startup_id": 1, "company_name": "LineWatch", "short_description": "",
         "Your pitch": "Legacy PLC lines monitored to predict failures early."}]).fillna(""))


def _status(result):
    return {s["provider"]: s for s in result["sources"]}


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(solve, "save_challenge", lambda *a: None)


def _web(monkeypatch, timed_out=0, hits=True):
    def ddg(queries, max_results=4, stats=None, **kw):
        if stats is not None:
            stats.update(requested=len(queries), timed_out=timed_out)
        return {k: ([{"title": "t", "body": "PlcCo predicts failures on plc lines", "href": f"https://plcco{k}.example/"}]
                    if hits else []) for k in queries}
    monkeypatch.setattr(solve, "_ddg_many", ddg)


def test_a_glassdollar_crash_is_reported_and_the_web_still_searched(monkeypatch):
    _web(monkeypatch)
    monkeypatch.setattr(solve, "_glassdollar_candidates", Mock(side_effect=KeyError("id")))
    result = solve.solve_problem(PROBLEM, llm=Mock(available=False), applications=_catalog())
    assert _status(result)["glassdollar"]["status"] == "unavailable" and _status(result)["glassdollar"]["reason"]
    assert _status(result)["web"]["status"] == "used" and result["market"]
    assert [c["name"] for c in result["applicants"]] == ["LineWatch"]


def test_a_market_that_fails_outright_keeps_the_applicants(monkeypatch):
    _web(monkeypatch)
    monkeypatch.setattr(solve, "_glassdollar_candidates", lambda kws: [{"name": "GD", "source": "glassdollar",
                                                                        "description": "plc failures"}])
    monkeypatch.setattr(solve, "_merge", Mock(side_effect=RuntimeError("bug")))
    result = solve.solve_problem(PROBLEM, llm=Mock(available=False), applications=_catalog())
    assert [c["name"] for c in result["applicants"]] == ["LineWatch"] and result["market"] == []
    assert _status(result)["web"] == {"provider": "web", "status": "unavailable", "reason": "the market search failed"}


def test_web_searches_that_timed_out_are_not_reported_as_no_matches(monkeypatch):
    _web(monkeypatch, timed_out=3, hits=False)
    result = solve.solve_problem(PROBLEM, llm=Mock(available=False), use_glassdollar=False, applications=_catalog())
    assert _status(result)["web"] == {"provider": "web", "status": "unavailable",
                                      "reason": "3 of 3 web searches timed out"}


def test_a_web_wave_that_partly_timed_out_says_so_beside_what_it_found(monkeypatch):
    _web(monkeypatch, timed_out=1)
    result = solve.solve_problem(PROBLEM, llm=Mock(available=False), use_glassdollar=False, applications=_catalog())
    assert _status(result)["web"]["status"] == "used"
    assert _status(result)["web"]["reason"] == "1 of 3 web searches timed out"


def test_an_unreadable_web_extraction_is_noted(monkeypatch):
    _web(monkeypatch)
    llm = Mock(available=True)
    llm.complete.return_value = ""                       # every model call fails
    llm.embedding_model.return_value = ""
    result = solve.solve_problem(PROBLEM, llm=llm, use_glassdollar=False, applications=_catalog())
    assert "named from their web addresses" in _status(result)["web"]["reason"]


def test_each_candidate_says_whether_a_model_or_shared_words_judged_it():
    cands = [{"name": "A", "source": "web", "description": "predict failures on legacy plc lines"}]
    model = Mock(available=True)
    model.complete.return_value = json.dumps({"ranked": [{"index": 0, "band": "strong", "reason": "r", "quote": ""}]})
    assert solve.rank(PROBLEM, cands, model)[0]["judged_by"] == "model"
    model.complete.return_value = "not json"
    assert solve.rank(PROBLEM, cands, model)[0]["judged_by"] == "words"


def test_failing_interpretation_and_applications_search_do_not_fail_the_search(monkeypatch):
    _web(monkeypatch)
    monkeypatch.setattr(solve, "interpret", Mock(side_effect=RuntimeError("bug")))
    monkeypatch.setattr(solve, "match_applications", Mock(side_effect=RuntimeError("bug")))
    result = solve.solve_problem(PROBLEM, llm=Mock(available=False), use_glassdollar=False, applications=_catalog())
    assert result["interpretation"]["method"] == "offline" and result["keywords"]
    assert result["applicants"] == [] and _status(result)["applications"]["status"] == "unavailable"
    assert result["retrieval"]["method"] == "none" and result["market"]
