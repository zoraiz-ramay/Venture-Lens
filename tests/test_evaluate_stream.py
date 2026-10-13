"""Progressive delivery of an evaluation (core/pipeline.py::on_partial, /api/evaluate/stream).

A fresh run takes a minute or two and all of it used to arrive at once, so the page held a skeleton
until routing finished even though the company profile had been ready for most of that time.

The property that matters is not that partials arrive — it is that they change NOTHING. The
returned result must be byte-identical to a run with no callback, because two code paths producing
two different answers for the same company is a far worse failure than a slow page. The partials
are an addition; the result is the contract.
"""
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import pipeline  # noqa: E402


# --------------------------------------------------------------------------- fakes

class _OfflineLLM:
    """No key configured: every stage takes its offline branch, so the pipeline runs end to end
    with no network and no model, deterministically."""

    available = False

    def complete(self, *a, **kw):
        return ""

    @staticmethod
    def parse_json(_):
        return None


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """A one-company frame and a stubbed web, so `evaluate` is pure local computation."""
    df = pd.DataFrame([{
        "company_name": "Acme Vision", "hq": "Munich, DE", "founded_year": "2021",
        "employees_count": "40", "funding": "$5M seed", "customers": "Bosch",
        "website": "https://acme.example", "domain": "acme.example",
        "Your pitch": "Machine vision for production lines",
        "Business model": "SaaS", "Development stage of your solution": "Prototype",
        "has_pdf": "", "linkedin_url": "", "crunchbase_url": "",
    }])
    monkeypatch.setattr(pipeline, "LLMClient", lambda *a, **kw: _OfflineLLM())
    monkeypatch.setattr(pipeline.web, "ddg_search", lambda *a, **kw: [])
    monkeypatch.setattr(pipeline.web, "_ddg_many", lambda q, **kw: {k: [] for k in q})
    monkeypatch.setattr(pipeline.web, "fetch_site_text", lambda *a, **kw: {})
    tools = tmp_path / "tools.csv"
    tools.write_text("product,category,division,description\nNX,CAD,DI SW,Design\n",
                     encoding="utf-8")
    return df, str(tools)


def _run(df, tools, **kw):
    return pipeline.evaluate("Acme Vision", None, tools, do_web=False, df=df, **kw)


# --------------------------------------------------------------------------- the contract

def test_streaming_does_not_change_the_answer(offline):
    """The whole point. A callback must not be able to alter what the engine concludes."""
    df, tools = offline
    plain = _run(df, tools)
    streamed = _run(df, tools, on_partial=lambda s, d: None)
    # run_id is added by the API layer, not here. Each Fact is stamped with the second it was
    # gathered, so two runs straddling a second differ there and only there: that is the clock,
    # not the answer, and comparing it made this test fail whenever the runs crossed a second.
    import re
    clock = re.compile(r'"retrieved_at": "[^"]*"')
    assert clock.sub("", json.dumps(plain, sort_keys=True, default=str)) == \
           clock.sub("", json.dumps(streamed, sort_keys=True, default=str))


def test_it_emits_the_profile_before_the_score(offline):
    """The ordering the feature exists for: something readable arrives before the verdict."""
    df, tools = offline
    seen = []
    _run(df, tools, on_partial=lambda s, d: seen.append(s))
    assert "profile" in seen and "score" in seen
    assert seen.index("profile") < seen.index("score")
    # And the verdict is last of all, because everything else feeds it.
    assert seen[-1] == "routing"


def test_the_traction_rubric_arrives_before_the_model_score(offline):
    """The rubric needs no model, so it goes out the moment the research joins — a reviewer can
    read the points breakdown while the scoring completion is still running."""
    df, tools = offline
    seen = []
    _run(df, tools, on_partial=lambda s, d: seen.append(s))
    assert seen.count("traction") == 1
    assert seen.index("traction") < seen.index("score")


def test_the_profile_is_emitted_before_the_research_that_deepens_it(offline):
    """The measurement that forced this: on a real run the deep-profile branch returned at 112s of
    118s. A page waiting for it waits for the whole evaluation, so the row-level profile goes out
    as soon as enrichment finishes and the researched one replaces it later."""
    df, tools = offline
    seen = []
    _run(df, tools, on_partial=lambda s, d: seen.append(s))
    # Row-only, researched-before-the-recall-nets, final.
    assert seen.count("profile") == 3
    # The early one lands before any of the five concurrent branches report.
    assert seen.index("profile") < min(seen.index(s) for s in ("fit", "trend", "verification"))


def test_the_researched_profile_is_shown_before_the_recall_nets_finish(offline, monkeypatch):
    """The recall nets take as long again as the extraction before them, and the header facts are
    usually settled by then, so the researched profile goes out first and the nets refine it."""
    import importlib
    profile_mod, enrich_mod, trend_mod = (importlib.import_module(f"core.{m}") for m in ("profile", "enrich", "trend"))
    df, tools = offline
    # The recall nets only run on a web run; every search is stubbed empty, so nothing leaves.
    for mod in (profile_mod, enrich_mod, trend_mod):
        monkeypatch.setattr(mod, "_ddg_many", lambda q, **kw: {k: [] for k in q})
    monkeypatch.setattr(enrich_mod, "fetch_site_text", lambda *a, **kw: {})
    seen = []
    real = profile_mod._recover_headline_facts

    def headline_net(*a, **kw):
        seen.append("recall_nets")
        return real(*a, **kw)
    monkeypatch.setattr(profile_mod, "_recover_headline_facts", headline_net)
    result = pipeline.evaluate("Acme Vision", None, tools, do_web=True, df=df,
                               on_partial=lambda s, d: seen.append(("profile", d["deep_profile"]))
                               if s == "profile" else None)
    researched = [i for i, e in enumerate(seen) if isinstance(e, tuple) and e[1]]
    assert seen.index("recall_nets") > researched[0]
    # ...and the last emission is the finished profile the result stores.
    assert seen[researched[-1]][1] == result["deep_profile"]


def test_the_early_profile_only_ever_gains_fields_never_changes_them(offline):
    """Showing a value at 20s that reads differently at 112s would be worse than showing nothing.
    Safe only because `backfill_profile` fills blanks and never overwrites — pinned here, because
    that is the property the early emission rests on."""
    df, tools = offline
    profiles = []
    _run(df, tools, on_partial=lambda s, d: profiles.append(d) if s == "profile" else None)
    early, late = profiles[0]["profile"], profiles[-1]["profile"]
    for key, value in early.items():
        if str(value).strip():
            assert late.get(key) == value, key


def test_the_company_is_named_before_anything_is_known_about_it(offline):
    # Lets the page put up a header with the RESOLVED name rather than whatever was typed.
    df, tools = offline
    seen = []
    _run(df, tools, on_partial=lambda s, d: seen.append(s))
    assert seen[0] == "identity"


def test_the_profile_partial_carries_what_the_overview_needs(offline):
    df, tools = offline
    captured = {}
    _run(df, tools, on_partial=lambda s, d: captured.setdefault(s, d))
    profile = captured["profile"]
    # All three keys together: the Overview reads the header profile, its provenance map and the
    # researched deep profile, and any one of them arriving alone renders a half-empty page.
    assert set(profile) == {"profile", "profile_sources", "deep_profile", "profile_researching"}
    assert profile["profile"]["company_name"] == "Acme Vision"


def test_only_the_finished_profile_stops_saying_it_is_still_researching(offline):
    """An empty founders list at 20s means "not found yet", at the end "not found". The page can
    only tell the two apart if the partial says which it is."""
    df, tools = offline
    flags = []
    result = _run(df, tools, on_partial=lambda s, d: flags.append(d["profile_researching"]) if s == "profile" else None)
    assert flags == [True, True, False]
    assert "profile_researching" not in result


def test_every_partial_matches_the_final_result(offline):
    """A partial that later disagrees with the result is worse than no partial: the reviewer read
    a number that the stored evaluation does not contain."""
    df, tools = offline
    captured = {}
    result = _run(df, tools, on_partial=lambda s, d: captured.__setitem__(s, d))
    for section in ("score", "routing", "trend", "fit", "verification", "summary", "traction"):
        assert json.dumps(captured[section], sort_keys=True, default=str) == \
               json.dumps(result[section], sort_keys=True, default=str), section


def test_a_consumer_that_throws_does_not_break_the_evaluation(offline):
    """A browser that hangs up mid-run must cost the stream, never the result — the run still
    finishes and is still saved."""
    df, tools = offline
    def _boom(section, data):
        raise RuntimeError("client went away")

    result = _run(df, tools, on_partial=_boom)
    assert result["found"] and result["score"]["status"] == "unavailable"
    assert result["score"]["final_score"] is None


def test_no_callback_is_still_the_supported_case(offline):
    df, tools = offline
    assert _run(df, tools)["found"]


def test_scoring_that_never_reads_fit_starts_while_fit_is_still_running(offline, monkeypatch):
    """Fit's match call takes ~35s and used to sit in front of all scoring. Here fit refuses to
    finish until Team & Ecosystem and Market have started: under the old schedule (score only
    after all five branches) that is a wait that never ends, so the run comes back without them."""
    import threading
    from core import team_ecosystem, market as market_mod
    df, tools = offline
    started = {"team": threading.Event(), "market": threading.Event()}
    real_fit, real_team, real_market = pipeline.match_siemens_tools, team_ecosystem.assess_team, market_mod.assess_market

    def slow_fit(*a, **kw):
        overlapped = all(e.wait(5) for e in started.values())
        out = real_fit(*a, **kw)
        out["_overlapped"] = overlapped
        return out

    def team(*a, **kw):
        started["team"].set()
        return real_team(*a, **kw)

    def market(*a, **kw):
        started["market"].set()
        return real_market(*a, **kw)

    monkeypatch.setattr(pipeline, "match_siemens_tools", slow_fit)
    monkeypatch.setattr(team_ecosystem, "assess_team", team)
    monkeypatch.setattr(market_mod, "assess_market", market)
    dep = {"id": "di", "label": "Digital Industries", "interests": ["machine vision"], "demo": True}
    result = _run(df, tools, department=dep)
    assert result["fit"]["_overlapped"] is True


def test_a_streamed_run_that_scores_before_verification_lands_is_the_same_run(offline, monkeypatch):
    """Starting Team & Ecosystem before verification is only allowed when it cannot change a
    prompt; the streamed result must then be byte-identical to a run that waited."""
    import re
    import threading
    from core import pillar_match, team_ecosystem
    df, tools = offline
    dep = {"id": "di", "label": "Digital Industries", "interests": ["machine vision"], "demo": True}
    team_started = threading.Event()
    real_team, real_verify = team_ecosystem.assess_team, pipeline.verify_facts

    def team(*a, **kw):
        team_started.set()
        return real_team(*a, **kw)

    def slow_verify(*a, **kw):
        assert team_started.wait(5)
        return real_verify(*a, **kw)

    monkeypatch.setattr(team_ecosystem, "assess_team", team)
    monkeypatch.setattr(pipeline, "verify_facts", slow_verify)
    monkeypatch.setattr(pillar_match, "evidence_settled_without", lambda run, *keys: True)
    seen = []
    early = _run(df, tools, department=dep, on_partial=lambda s, d: seen.append(s))
    monkeypatch.setattr(pipeline, "verify_facts", real_verify)
    monkeypatch.setattr(pillar_match, "evidence_settled_without", lambda run, *keys: False)
    late = _run(df, tools, department=dep)
    clock = re.compile(r'"retrieved_at": "[^"]*"')
    assert "verification" in seen
    assert clock.sub("", json.dumps(early, sort_keys=True, default=str)) == \
           clock.sub("", json.dumps(late, sort_keys=True, default=str))
