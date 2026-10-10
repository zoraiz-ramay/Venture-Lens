"""The team record: founders, programmes and investors are searched once and reused.

A full refresh re-ran the founder, founder-background and programme searches every time, though
earlier runs had already found them with sources. Now a full evaluation reuses them (carried in by
carry_forward with their last-confirmed dates), and the team section's own Refresh is where they
are searched again; what it finds is stored and carried into the next evaluation."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core import carry_forward
from core import profile as P

ADA = {"name": "Ada Lovelace", "role": "CEO", "source_url": "https://ada.test/about"}
PROG = {"name": "Techstars", "type": "accelerator", "source_url": "https://techstars.test/x"}


@pytest.mark.parametrize("known, searched", [
    ({}, {"founders", "deepen", "programs"}),
    ({"founders": True, "programs": True}, set()),
    ({"founders": True}, {"programs"}),
])
def test_a_full_evaluation_skips_the_searches_for_what_is_already_known(monkeypatch, known, searched):
    calls = set()
    monkeypatch.setattr(P, "_ddg_many", lambda *a, **k: {})
    monkeypatch.setattr(P, "_recover_founders", lambda *a, **k: calls.add("founders"))
    monkeypatch.setattr(P, "_deepen_founders", lambda *a, **k: calls.add("deepen"))
    monkeypatch.setattr(P, "_recheck_programs", lambda *a, **k: calls.add("programs"))

    class NoModel:
        available = False

    P.research_profile(pd.Series({"company_name": "Acme"}), NoModel(), do_web=True, known_team=known)
    assert calls == searched


def test_known_team_needs_a_sourced_entry():
    runs = [{"deep_profile": {"founders": [{"name": "Ada"}], "programs": [PROG]}}]
    assert carry_forward.known_team(runs) == {"founders": False, "programs": True}   # Ada has no source


def test_the_merge_keeps_the_runs_entries_first_and_marks_what_the_refresh_added():
    run = {"founders": [ADA], "programs": [PROG], "investors": []}
    fresh = {"founders": [{"name": "Dr. Ada Lovelace", "source_url": "https://x.test"},          # same person
                          {"name": "Grace Hopper", "role": "CTO", "source_url": "https://grace.test"}],
             "programs": [{"name": "TechStars", "source_url": "https://t.test"}],               # same programme
             "investors": [{"name": "Point Nine", "source_url": "https://p9.test"}]}
    out = P.merge_team(run, {}, fresh)
    assert [f["name"] for f in out["founders"]] == ["Ada Lovelace", "Grace Hopper"]
    assert [f.get("new", False) for f in out["founders"]] == [False, True]
    assert [p["name"] for p in out["programs"]] == ["Techstars"]
    assert out["investors"] == [{"name": "Point Nine", "source_url": "https://p9.test", "new": True}]


def test_a_stored_refresh_is_carried_into_the_next_evaluation_without_its_display_flag():
    record = {"founders": [{**ADA, "new": True}], "investors": [{"name": "Point Nine", "source_url": "https://p9.test"}],
              "retrieved_at": "2026-10-09T00:00:00+00:00"}
    run = carry_forward.team_run(record)
    assert run["deep_profile"]["founders"] == [ADA] and run["created_at"].startswith("2026-10-09")
    merged = carry_forward.merge_profile({"profile": {"founders": [], "commercial": {"investors": []}}, "facts": []},
                                         [run], "2026-10-10", {})
    assert [f["name"] for f in merged["profile"]["founders"]] == ["Ada Lovelace"]
    assert merged["profile"]["founders"][0]["last_confirmed_at"].startswith("2026-10-09")


def test_the_team_route_reads_without_searching_and_searches_only_on_refresh(monkeypatch):
    from api import main
    saved, searched = [], []
    run = {"company": "Teamrec GmbH", "profile": {"website": "https://teamrec.test"},
           "deep_profile": {"founders": [ADA], "programs": [], "commercial": {"investors": []}}}
    monkeypatch.setattr(main, "_run_for", lambda run_id, user: run)
    monkeypatch.setattr(main.store, "latest_enrichment", lambda company, kind, run_id=None: saved[-1] if saved else None)
    monkeypatch.setattr(main.store, "save_enrichment", lambda company, kind, payload, **kw: saved.append(payload))
    monkeypatch.setattr("core.profile.refresh_team", lambda company, row, llm: searched.append(company) or
                        {"founders": [], "programs": [PROG], "investors": []})
    client = TestClient(main.app)
    client.get("/api/auth/login", follow_redirects=False)
    csrf = {"X-CSRF-Token": client.cookies.get("sea_csrf")}

    first = client.post("/api/runs/7401/lookup/team", headers=csrf).json()
    assert searched == [] and first["from_store"] is False and [f["name"] for f in first["founders"]] == ["Ada Lovelace"]
    fresh = client.post("/api/runs/7401/lookup/team?refresh=true", headers=csrf).json()
    assert searched == ["Teamrec GmbH"] and [p["name"] for p in fresh["programs"]] == ["Techstars"] and fresh["programs"][0]["new"]
    again = client.post("/api/runs/7401/lookup/team", headers=csrf).json()
    assert searched == ["Teamrec GmbH"] and again["from_store"] and [p["name"] for p in again["programs"]] == ["Techstars"]
