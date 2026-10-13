"""Every evaluation is assessed for every department and recommends one for Collaborate.

The reviewer used to choose a department before searching, which asks them to guess the answer to
one of the questions the evaluation exists to answer. Research, Empower and Connect do not depend on
the department; only Collaborate does (it matches the startup against that department's stated
needs), so a run scores Collaborate per department and is headed by the one it answers best.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import assessment, pillar_match  # noqa: E402
from tests.test_evaluate_stream import offline, _run  # noqa: E402,F401  (fixture reuse)

DEPS = [{"id": "di", "label": "Digital Industries", "interests": ["automation"], "demo": True},
        {"id": "si", "label": "Smart Infrastructure", "interests": ["buildings"], "demo": True},
        {"id": "mobility", "label": "Siemens Mobility", "interests": ["rail"], "demo": True}]


def _pillar(name, total, needs=()):
    band = "strong" if total >= 7 else "review" if total >= 4 else "no_match"
    scores = [min(3, max(0, total - 3 * i)) for i in range(3)]            # three criteria, as validated
    return {"pillar": name, "status": "assessed", "total": total, "band": band, "next_step": "",
            "evidence_coverage": 1.0,
            "criteria": [{"id": f"c{i}", "label": f"C{i}", "score": sc, "rationale": "", "evidence": [],
                          "catalog": [{"id": f"need:{n}"} for n in needs] if i == 0 else []}
                         for i, sc in enumerate(scores)]}


def _results(collab: dict):
    """Packaged pillar results per department: shared Empower/Connect, that department's Collaborate."""
    out = {}
    for d in DEPS:
        state = pillar_match.prepare_pillars({}, d, llm=None, do_web=False)
        c = collab.get(d["id"])
        out[d["id"]] = pillar_match.package_pillars(state, {
            "Empower": _pillar("Empower", 5), "Connect": _pillar("Connect", 3),
            "Collaborate": c if c is not None else {"pillar": "Collaborate", "status": "unassessed",
                                                    "total": None, "band": None, "criteria": []}})
    return out


BASE = {"found": True, "company": "Acme", "score": {"dimensions": {}}, "routing": {},
        # Traction is recomputed from its inputs on every read, so the fixture carries inputs.
        "traction_inputs": {"origin": "GlassDollar", "funding": "5000000", "employees_count": "40"}}
TEAM = {"status": "assessed", "score_0_100": 70, "points": 14}
MARKET = {"status": "assessed", "score_0_100": 50}


def test_the_department_whose_needs_it_answers_best_heads_the_run():
    out = assessment.build_all(BASE, DEPS, _results({"di": _pillar("Collaborate", 4, ["a"]),
                                                     "mobility": _pillar("Collaborate", 8, ["x", "y"])}), TEAM)
    block = out["departments"]
    assert block["recommended"] == "mobility" and block["basis"] == "collaborate"
    assert [e["department"]["id"] for e in block["ranked"]] == ["mobility", "di", "si"]
    assert out["department"]["id"] == "mobility"                       # the headline is the recommendation
    assert out["assessment"]["pillars"]["Collaborate"]["total"] == 8


def test_a_tie_goes_to_the_department_with_more_of_its_needs_answered():
    out = assessment.build_all(BASE, DEPS, _results({"di": _pillar("Collaborate", 6, ["a"]),
                                                     "si": _pillar("Collaborate", 6, ["a", "b", "c"])}), TEAM)
    assert out["departments"]["recommended"] == "si"


def test_no_collaborate_assessment_recommends_nothing_rather_than_the_first_department():
    out = assessment.build_all(BASE, DEPS, _results({}), TEAM)
    assert out["departments"]["recommended"] is None
    assert out["departments"]["basis"] == "no_collaborate_assessment"


def test_every_department_at_zero_recommends_none_and_fit_comes_from_the_other_pillars():
    zero = {d["id"]: _pillar("Collaborate", 0, ["a"]) for d in DEPS}
    out = assessment.build_all(BASE, DEPS, _results(zero), TEAM)
    assert out["departments"]["recommended"] is None
    assert out["departments"]["basis"] == "no_collaborate_match"
    fit = out["assessment"]["siemens_fit"]
    assert fit["winner"] == "Empower" and fit["score"] == round(100 * 5 / 9)


def test_a_stored_run_that_named_a_zero_department_reads_as_no_match():
    zero = {d["id"]: _pillar("Collaborate", 0) for d in DEPS}
    out = assessment.build_all(BASE, DEPS, _results(zero), TEAM)
    stored = {**out, "departments": {**out["departments"], "recommended": "di", "basis": "collaborate"}}
    block = assessment.hydrate(stored)["departments"]
    assert block["recommended"] is None and block["basis"] == "no_collaborate_match"


def test_every_department_carries_its_own_total_and_they_differ_only_by_collaborate():
    out = assessment.build_all(BASE, DEPS, _results({"di": _pillar("Collaborate", 9, ["a"])}), TEAM, MARKET)
    by_id = {e["department"]["id"]: e for e in out["departments"]["ranked"]}
    # DI's Collaborate (9/9) beats the shared Empower (5/9), so its Siemens Fit is higher.
    assert by_id["di"]["assessment"]["siemens_fit"]["score"] == 100
    assert by_id["si"]["assessment"]["siemens_fit"]["score"] == round(100 * 5 / 9)
    assert by_id["di"]["score"]["final_score"] != by_id["si"]["score"]["final_score"]


def test_reading_a_stored_run_recomputes_every_departments_total():
    out = assessment.build_all(BASE, DEPS, _results({"di": _pillar("Collaborate", 9, ["a"])}), TEAM, MARKET)
    stale = {**out, "departments": {**out["departments"], "ranked": [
        {**e, "score": {"final_score": -1}} for e in out["departments"]["ranked"]]}}
    fresh = assessment.hydrate(stale)
    assert all(e["score"]["final_score"] != -1 for e in fresh["departments"]["ranked"])


def test_the_cache_key_covers_every_departments_needs():
    cats = {"siemens_tools": {"checksum": "t"}, "xcelerator": {"checksum": "x"}}
    a = assessment.all_departments_key(cats, {"di": {"checksum": "1"}, "si": {"checksum": "2"}})
    b = assessment.all_departments_key(cats, {"di": {"checksum": "1"}, "si": {"checksum": "3"}})
    assert a != b


def test_an_evaluation_for_all_departments_runs_collaborate_once_per_department(offline, monkeypatch):
    from core import pipeline
    df, tools = offline
    calls = []
    real = pipeline._pillar_after

    def spy(prep, pillar, run, llm, department=None):
        calls.append((pillar, (department or {}).get("id")))
        return real(prep, pillar, run, llm, department)

    monkeypatch.setattr(pipeline, "_pillar_after", spy)
    result = _run(df, tools, departments=DEPS)
    # The first department reuses the prepared state as is (None); the others switch catalogs.
    assert sorted(str(d) for p, d in calls if p == "Collaborate") == ["None", "mobility", "si"]
    assert sorted(p for p, _ in calls if p != "Collaborate") == ["Connect", "Empower"]   # once each
    assert [e["department"]["id"] for e in result["departments"]["ranked"]] == ["di", "si", "mobility"]


# ----------------------------------------------------------------- scoring that starts before the trend

def _facts(n):
    return [{"key": f"k{i}", "value": "x" * 380, "source_url": f"https://e{i}.example"} for i in range(n)]


def test_the_trend_cannot_change_evidence_whose_budget_ran_out_before_it():
    run = {"company": "Acme", "summary": "Vision", "facts": _facts(60)}
    assert pillar_match.evidence_settled_without(run, "trend") is True
    # The trend would be read, and so would change the records, whenever the budget lasts that far —
    # including when only the application (visited after the trend) exhausts it.
    assert pillar_match.evidence_settled_without({"company": "Acme", "summary": "Vision"}, "trend") is False
    late = {"company": "Acme", "application": {f"q{i}": "y" * 380 for i in range(60)}}
    assert pillar_match.evidence_settled_without(late, "trend") is False


def test_settled_evidence_is_the_same_records_with_or_without_the_trend():
    run = {"company": "Acme", "summary": "Vision", "facts": _facts(60)}
    with_trend = {**run, "trend": {"summary": "a niche growing fast", "momentum": 70}}
    assert pillar_match._evidence(run) == pillar_match._evidence(with_trend)


def test_team_and_pillars_start_before_the_trend_lands_and_conclude_the_same(offline, monkeypatch):
    """The trend was the last research branch to land in every measured run, and Team, the pillar
    concepts, Collaborate and Empower never read it once the evidence budget is spent before it."""
    import json
    import re
    import threading
    from core import pipeline, team_ecosystem
    df, tools = offline
    team_started, seen = threading.Event(), {}
    real_team, real_trend = team_ecosystem.assess_team, pipeline.analyze_trend

    def team(research, llm):
        team_started.set()
        return real_team(research, llm)

    def trend(*a, **kw):
        seen["team_first"] = team_started.wait(timeout=5)
        return real_trend(*a, **kw)

    monkeypatch.setattr(team_ecosystem, "assess_team", team)
    monkeypatch.setattr(pipeline, "analyze_trend", trend)
    monkeypatch.setattr(pillar_match, "evidence_settled_without", lambda run, *keys: True)
    early = _run(df, tools, departments=DEPS)
    assert seen["team_first"] is True

    monkeypatch.setattr(pipeline, "analyze_trend", real_trend)
    monkeypatch.setattr(pillar_match, "evidence_settled_without", lambda run, *keys: False)
    late = _run(df, tools, departments=DEPS)
    clock = re.compile(r'"retrieved_at": "[^"]*"')
    assert clock.sub("", json.dumps(early, sort_keys=True, default=str)) == \
           clock.sub("", json.dumps(late, sort_keys=True, default=str))


# ----------------------------------------------------------------- scoring that starts before verification

def test_verification_cannot_change_evidence_whose_budget_ran_out_before_it():
    run = {"company": "Acme", "summary": "Vision", "facts": _facts(60)}
    assert pillar_match.evidence_settled_without(run, "verification", "trend") is True
    assert pillar_match.evidence_settled_without(run, "verification") is True
    # Verification is read whenever the budget lasts that far, even if only the trend exhausts it.
    late = {"company": "Acme", "summary": "Vision", "trend": {f"t{i}": "y" * 380 for i in range(60)}}
    assert pillar_match.evidence_settled_without(late, "verification") is False
    assert pillar_match.evidence_settled_without(late, "verification", "trend") is False
    # Nothing outstanding: nothing can change.
    assert pillar_match.evidence_settled_without({"company": "Acme"}) is True


def test_settled_evidence_is_the_same_records_with_or_without_verification():
    run = {"company": "Acme", "summary": "Vision", "facts": _facts(60)}
    full = {**run, "verification": {"claims": [{"field": "hq", "value": "Munich", "status": "verified"}]},
            "trend": {"summary": "a niche growing fast"}}
    assert pillar_match._evidence(run) == pillar_match._evidence(full)


def test_empower_starts_when_enrichment_settles_the_evidence_after_fit_has_landed(offline, monkeypatch):
    """The usual order on a real run: fit (~10-15s) and the profile land, then enrichment's wave
    (~30s), then verification (~45s). Settling happens on enrichment's landing, which is not a
    branch, and Empower must start right there rather than wait for the next branch to land."""
    import threading
    import time
    from core import pipeline
    df, tools = offline
    landed = {"fit": threading.Event(), "profile": threading.Event()}
    empower = threading.Event()
    seen = {}
    real_fit, real_profile, real_combine = pipeline.match_siemens_tools, pipeline.research_profile, pipeline.combine
    real_verify, real_after = pipeline.verify_facts, pipeline._pillar_after

    def fit(*a, **kw):
        out = real_fit(*a, **kw)
        landed["fit"].set()
        return out

    def profile(*a, **kw):
        out = real_profile(*a, **kw)
        landed["profile"].set()
        return out

    def combine(*a, **kw):
        all(e.wait(5) for e in landed.values())
        time.sleep(0.3)                      # the loop takes both branches in before enrichment lands
        return real_combine(*a, **kw)

    def after(prep, pillar, *a, **kw):
        if pillar == "Empower":
            empower.set()
        return real_after(prep, pillar, *a, **kw)

    def verify(*a, **kw):
        seen["empower_first"] = empower.wait(5)
        return real_verify(*a, **kw)

    for attr, fn in (("match_siemens_tools", fit), ("research_profile", profile), ("combine", combine),
                     ("_pillar_after", after), ("verify_facts", verify)):
        monkeypatch.setattr(pipeline, attr, fn)
    monkeypatch.setattr(pillar_match, "evidence_settled_without", lambda run, *keys: True)
    _run(df, tools, departments=DEPS)
    assert seen["empower_first"] is True


def test_team_and_pillars_start_before_verification_lands_and_read_the_same_evidence(offline, monkeypatch):
    """Verification waits for enrichment's whole search wave and then thinks for ~15s, and scoring
    used to wait for it. With the evidence budget spent before verification's records, Team, the
    pillar concepts, Collaborate — and once the trend is in, Market and Connect — start without it,
    on records identical to the ones they read after it. Here verification refuses to finish until
    Team and Market have started: under the old schedule that wait never ends."""
    import json
    import re
    import threading
    from core import pipeline, team_ecosystem, market as market_mod
    from core.provenance import Fact
    df, tools = offline
    started = {"team": threading.Event(), "market": threading.Event()}
    seen: dict = {}
    real_team, real_market, real_prep = team_ecosystem.assess_team, market_mod.assess_market, pillar_match.prepare_pillars
    real_verify, real_profile = pipeline.verify_facts, pipeline.research_profile

    def profile(*a, **kw):
        # Enough sourced facts to spend the 14k budget before verification is reached, as on most
        # real runs (the evidence order is company, summary, profile, deep profile, facts, ...).
        out = real_profile(*a, **kw)
        out["facts"] = list(out["facts"]) + [Fact(key=f"k{i}", value=f"{i} " + "x" * 380, method="ddg_search",
                                                  source_url=f"https://e{i}.example") for i in range(60)]
        return out

    def team(research, llm):
        seen.setdefault("team", []).append(pillar_match._evidence(research))
        started["team"].set()
        return real_team(research, llm)

    def market(research, llm):
        seen.setdefault("market", []).append(pillar_match._evidence(research))
        started["market"].set()
        return real_market(research, llm)

    def prep(run, *a, **kw):
        seen.setdefault("prep", []).append(pillar_match._evidence(run))
        return real_prep(run, *a, **kw)

    def slow_verify(*a, **kw):
        seen.setdefault("overlapped", []).append(all(e.wait(5) for e in started.values()))
        return real_verify(*a, **kw)

    monkeypatch.setattr(pipeline, "research_profile", profile)
    monkeypatch.setattr(team_ecosystem, "assess_team", team)
    monkeypatch.setattr(market_mod, "assess_market", market)
    monkeypatch.setattr(pillar_match, "prepare_pillars", prep)
    monkeypatch.setattr(pipeline, "verify_facts", slow_verify)
    early = _run(df, tools, departments=DEPS)
    assert seen["overlapped"] == [True]

    for e in started.values():
        e.clear()
    monkeypatch.setattr(pipeline, "verify_facts", real_verify)
    monkeypatch.setattr(pillar_match, "evidence_settled_without", lambda run, *keys: False)
    late = _run(df, tools, departments=DEPS)
    for key in ("team", "market", "prep"):
        assert seen[key][0] == seen[key][1], key           # the same records, so the same prompt
    clock = re.compile(r'"retrieved_at": "[^"]*"')
    assert clock.sub("", json.dumps(early, sort_keys=True, default=str)) == \
           clock.sub("", json.dumps(late, sort_keys=True, default=str))
