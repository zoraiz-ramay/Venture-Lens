"""Solve a Problem's wall clock: the market search runs beside the applicants, never changing a prompt.

Every dependency is faked (model, web waves, GlassDollar, Tracxn). Overlap is pinned with events
rather than timings — a stage that must have started before another finishes is made to wait for
it, so a regression to the sequential order hangs (and fails) instead of being merely slower.
What matters most is pinned first: whichever stage lands first, the result and the set of prompts
the model is shown are the same, so a replay from cache stays byte-identical.
"""
from __future__ import annotations

import json
import threading
import time

import pandas as pd
import pytest

from core import catalogs, solve

PROBLEM = "We need to predict failures on legacy PLC lines."


def _catalog():
    return catalogs.applications_catalog_from_df(pd.DataFrame([
        {"startup_id": 1, "company_name": "Sensei Labs", "short_description": "",
         "Your pitch": "Predict failures on PLC lines from machine data."},
        {"startup_id": 2, "company_name": "LineWatch", "short_description": "",
         "Your pitch": "Legacy PLC lines monitored to predict failures early."},
    ]).fillna(""))


class FakeLLM:
    """Answers by prompt kind, deterministically; ``delays`` and ``gates`` shape the timeline."""
    available = True
    last_error = ""

    def __init__(self, delays=None, gates=None):
        self.delays, self.gates = delays or {}, gates or {}
        self.prompts, self.lock = [], threading.Lock()
        self.started = {k: threading.Event() for k in ("interpret", "rank_applicants", "rank_market", "extract")}

    def embedding_model(self):
        return ""

    def embed(self, texts, dims=0, cache=True):
        return None

    @staticmethod
    def kind(prompt):
        if prompt.startswith("A Siemens employee"):
            return "interpret"
        if prompt.startswith("Judge how directly"):
            return "rank_applicants" if "[applications]" in prompt else "rank_market"
        return "extract"

    def complete(self, prompt, **kw):
        kind = self.kind(prompt)
        with self.lock:
            self.prompts.append(prompt)
        self.started[kind].set()
        if kind in self.gates:
            assert self.gates[kind].wait(5), f"{kind} waited for a stage that should run beside it"
        time.sleep(self.delays.get(kind, 0))
        if kind == "interpret":
            return json.dumps({"restatement": "Failure prediction.", "facets": [],
                               "keywords": ["predictive maintenance", "plc analytics", "anomaly detection"]})
        if kind == "extract":
            return json.dumps({"companies": [{"name": f"Web{i}", "description": "predict failures on plc lines",
                                              "website": f"https://web{i}.example"} for i in range(4)]})
        rows = prompt.split("CANDIDATES:\n<<<\n")[1].split("\n>>>")[0].splitlines()
        return json.dumps({"ranked": [{"index": i, "band": "good", "reason": "Predicts failures.",
                                       "quote": "predict failures on plc lines"} for i in range(len(rows))]})


@pytest.fixture(autouse=True)
def offline_sources(monkeypatch):
    monkeypatch.setattr(solve, "save_challenge", lambda *a: None)
    waves = []

    def ddg(queries, max_results=4, **kw):
        waves.append(sorted(queries.values()))
        return {k: [{"title": q, "body": f"{q.split()[0]} predicts failures on plc lines",
                     "href": f"https://{k}.example/"}] for k, q in queries.items()}
    monkeypatch.setattr(solve, "_ddg_many", ddg)
    return waves


def _glassdollar(count, gaps=0):
    return lambda keywords, limit_per_kw=5: [
        {"name": f"GD{i}", "source": "glassdollar", "glassdollar_id": str(i), "website": "",
         "description": "" if i < gaps else "predict failures on plc lines"} for i in range(count)]


def _shape(result):
    return json.dumps({k: v for k, v in result.items() if k != "timings"}, sort_keys=True)


@pytest.mark.parametrize("glassdollar", [_glassdollar(0), _glassdollar(3, gaps=2), _glassdollar(7, gaps=2)])
def test_the_result_and_the_prompts_do_not_depend_on_which_stage_lands_first(monkeypatch, glassdollar):
    monkeypatch.setattr(solve, "_glassdollar_candidates", glassdollar)
    runs = []
    for delays in ({"interpret": 0.0, "rank_applicants": 0.15}, {"interpret": 0.15, "rank_applicants": 0.0},
                   {"rank_market": 0.15, "extract": 0.0}, {"extract": 0.15}):
        llm = FakeLLM(delays=delays)
        result = solve.solve_problem(PROBLEM, llm=llm, applications=_catalog())
        runs.append((_shape(result), sorted(llm.prompts)))
    assert all(run == runs[0] for run in runs)
    assert json.loads(runs[0][0])["market"]


def test_tracxn_is_asked_while_the_applicants_are_still_being_ranked():
    tracxn_called = threading.Event()

    class Tracxn:
        def search(self, problem):
            tracxn_called.set()
            return [{"name": f"TX{i}", "source": "tracxn", "description": "predict failures on plc lines",
                     "website": ""} for i in range(6)]
    # The applicants' ranking cannot finish until Tracxn has been asked: sequential code hangs here.
    llm = FakeLLM(gates={"rank_applicants": tracxn_called})
    result = solve.solve_problem(PROBLEM, llm=llm, applications=_catalog(), tracxn=Tracxn())
    assert [c["source"] for c in result["market"]] == ["tracxn"] * 6
    assert result["sources"][1] == {"provider": "tracxn", "status": "used"}


def test_a_web_search_certain_to_be_needed_runs_beside_the_markets_ranking(monkeypatch):
    monkeypatch.setattr(solve, "_glassdollar_candidates", _glassdollar(2))
    llm = FakeLLM()
    # Two GlassDollar candidates can never make five, so the web's extraction must not wait for
    # their ranking: the ranking is held until the extraction has started.
    llm.gates["rank_market"] = llm.started["extract"]
    result = solve.solve_problem(PROBLEM, llm=llm, applications=_catalog())
    assert {c["source"] for c in result["market"]} == {"glassdollar", "web"}
    assert {s["provider"]: s["status"] for s in result["sources"]}["web"] == "used"


def test_the_web_is_not_searched_on_speculation(monkeypatch, offline_sources):
    monkeypatch.setattr(solve, "_glassdollar_candidates", _glassdollar(6))
    result = solve.solve_problem(PROBLEM, llm=FakeLLM(), applications=_catalog())
    assert offline_sources == []                                  # six ranked: no web wave at all
    assert {s["provider"]: s["status"] for s in result["sources"]}["web"] == "skipped"


def test_the_page_hears_each_source_as_it_resolves_and_the_first_ranked_market(monkeypatch):
    monkeypatch.setattr(solve, "_glassdollar_candidates", _glassdollar(2))
    partials = []
    llm = FakeLLM(delays={"extract": 0.2})
    result = solve.solve_problem(PROBLEM, llm=llm, applications=_catalog(),
                                 on_partial=lambda section, data: partials.append((section, json.loads(json.dumps(data)))))
    market = [d for s, d in partials if s == "market"]
    states = [{x["provider"]: x["status"] for x in d["sources"]} for d in market]
    assert any(st.get("glassdollar") == "searching" for st in states)
    assert any(st.get("web") == "searching" for st in states)
    assert states[-1]["web"] == "used"
    preview = next(d for d in market if "market" in d)
    assert preview["market_complete"] is False and {c["source"] for c in preview["market"]} == {"glassdollar"}
    assert set(result["timings"]) >= {"interpretation", "applicants_found", "applicants_ranked", "market"}
