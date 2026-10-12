"""Solve a Problem searches a reviewer can come back to.

What is pinned: a finished search is kept once and returned as kept (so a refinement or a shortlist
is not undone by the job's older copy); it can still be sharpened and questioned after its job has
expired; the shortlist only ever names startups the search found, and survives a refinement that
hides one; follow-up questions are kept with the search; and none of it is readable by anyone else.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import auth, jobs, routes_solve, store
from api.auth import Principal, sessions
from core import catalogs, solve

PROBLEM = "We need to predict failures on legacy PLC lines."
SENSEI = {"id": "app:1", "name": "Sensei Labs", "source": "applications", "band": "strong",
          "description": "Anomaly detection for machines.", "reason": "Fits."}
VISION = {"id": "web:visionco", "name": "VisionCo", "source": "web", "band": "possible",
          "description": "Camera inspection.", "website": "https://vision.example"}
RESULT = {"problem": PROBLEM, "applicants": [SENSEI], "market": [VISION],
          "interpretation": {"facets": [{"id": "deployment", "label": "Deployment", "options": ["On-prem", "Cloud"]}]}}


@pytest.fixture(autouse=True)
def db(monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(pathlib.Path(tempfile.mkdtemp()) / "runs.db"))
    monkeypatch.setattr(store, "_restore_from_s3", lambda: None)
    monkeypatch.setattr(store, "_upload_to_s3", lambda: None)
    monkeypatch.setattr(routes_solve.store, "run_ref_for", lambda name: None)
    monkeypatch.setattr(solve, "save_challenge", lambda *a: None)


def _user(oid):
    return Principal(oid=oid, name=oid, email=oid + "@test", upn=oid + "@test", tid="test")


def _job(oid, job_id, result=RESULT, status="complete"):
    sessions().put(jobs._key(oid, job_id), {"id": job_id, "kind": "solve", "query": PROBLEM, "status": status,
                                            "result": result, "updated_at": 0}, 60)


def _expire(oid, job_id):
    sessions().delete(jobs._key(oid, job_id))


def test_a_finished_search_is_kept_once_and_returned_as_kept():
    user = _user("keep-owner")
    _job(user.oid, "s1")
    kept = routes_solve.keep_search("s1", user)
    assert kept["problem"] == PROBLEM and kept["shortlist"] == [] and kept["thread"] == []
    routes_solve.set_shortlist("s1", routes_solve.ShortlistBody(ids=["web:visionco"]), user)
    # The page calls keep every time it shows the search; the job's copy must not undo the shortlist.
    assert routes_solve.keep_search("s1", user)["shortlist"] == ["web:visionco"]
    listed = routes_solve.my_solve_searches(user)["searches"]
    assert [(s["id"], s["applicants"], s["market"], s["shortlisted"]) for s in listed] == [("s1", 1, 1, 1)]


def test_a_search_still_running_is_not_kept():
    user = _user("keep-running")
    _job(user.oid, "s2", None, status="running")
    with pytest.raises(HTTPException) as running:
        routes_solve.keep_search("s2", user)
    assert running.value.status_code == 404
    assert routes_solve.my_solve_searches(user)["searches"] == []


def test_the_shortlist_only_names_startups_the_search_found():
    user = _user("keep-shortlist")
    _job(user.oid, "s3")
    out = routes_solve.set_shortlist("s3", routes_solve.ShortlistBody(ids=["app:1", "web:invented", "app:1"]), user)
    assert out["shortlist"] == ["app:1"]


def test_a_kept_search_can_be_sharpened_after_its_job_expired(monkeypatch):
    import api.main as main
    user = _user("keep-expired")
    _job(user.oid, "s4")
    routes_solve.keep_search("s4", user)
    _expire(user.oid, "s4")
    monkeypatch.setattr(main, "applications_catalog", lambda: catalogs.applications_catalog_from_df(None))
    monkeypatch.setattr(solve, "rank", lambda p, cs, llm: [c for c in cs if c["id"] == "web:visionco"])
    out = routes_solve.refine_search("s4", routes_solve.RefineBody(answers={"deployment": "Cloud"}), user)
    assert [c["name"] for c in out["market"]] == ["VisionCo"]
    # Reopening shows the refined result, and clearing the choices restores what was found.
    reopened = routes_solve.keep_search("s4", user)
    assert reopened["result"]["refinement"]["answers"] == {"deployment": "Cloud"}
    assert routes_solve.refine_search("s4", routes_solve.RefineBody(), user)["applicants"] == [SENSEI]


def test_a_shortlisted_startup_a_refinement_hid_stays_kept_and_is_asked_about(monkeypatch):
    import api.main as main
    user = _user("keep-hidden")
    _job(user.oid, "s5")
    monkeypatch.setattr(main, "applications_catalog", lambda: catalogs.applications_catalog_from_df(None))
    monkeypatch.setattr(solve, "rank", lambda p, cs, llm: [])           # the refinement drops everyone
    routes_solve.refine_search("s5", routes_solve.RefineBody(answers={"deployment": "Cloud"}), user)
    assert routes_solve.set_shortlist("s5", routes_solve.ShortlistBody(ids=["web:visionco"]), user)["shortlist"] \
        == ["web:visionco"]
    seen = {}

    def answer(question, **kw):
        seen.update(kw)
        return {"answer": "VisionCo inspects with cameras [1].", "provider": "run", "source": "This shortlist",
                "evidence": [], "note": ""}
    monkeypatch.setattr(routes_solve.core, "chat_assistant", answer)
    monkeypatch.setattr(routes_solve, "tracxn_client_for", lambda user, llm: None)
    routes_solve.ask_shortlist("s5", routes_solve.ShortlistAskBody(question="What does it do?", ids=["web:visionco"]), user)
    assert any(f["text"].startswith("VisionCo") for f in seen["run_facts"])
    assert "VisionCo" in seen["facts_subject"]
    # The question and its answer come back with the search.
    thread = routes_solve.keep_search("s5", user)["thread"]
    assert [m["role"] for m in thread] == ["user", "assistant"] and thread[1]["source"] == "This shortlist"


def test_kept_searches_are_private_to_the_reviewer():
    owner, other = _user("keep-private-owner"), _user("keep-private-other")
    _job(owner.oid, "s6")
    routes_solve.keep_search("s6", owner)
    assert routes_solve.my_solve_searches(other)["searches"] == []
    for call in (lambda: routes_solve.keep_search("s6", other),
                 lambda: routes_solve.set_shortlist("s6", routes_solve.ShortlistBody(ids=["app:1"]), other),
                 lambda: routes_solve.refine_search("s6", routes_solve.RefineBody(), other),
                 lambda: routes_solve.forget_search("s6", other)):
        with pytest.raises(HTTPException) as denied:
            call()
        assert denied.value.status_code == 404
    assert routes_solve.forget_search("s6", owner) == {"deleted": "s6"}
    assert routes_solve.my_solve_searches(owner)["searches"] == []


def test_a_kept_search_carries_no_application_status_contact_or_referral():
    import pandas as pd
    df = pd.DataFrame([{"startup_id": 1, "company_name": "Sensei Labs", "status": "rejected",
                        "contact_email": "ceo@sensei.example", "Referral": "Referred by Jane Roe",
                        "short_description": "", "Your pitch": "We detect anomalies in machine data."}]).fillna("")
    applicant = solve._applicant(catalogs.applications_catalog_from_df(df)["entries"][0], {"anomalies"})
    user = _user("keep-status")
    _job(user.oid, "s7", {**RESULT, "applicants": [applicant]})
    stored = str(routes_solve.keep_search("s7", user))
    for secret in ("rejected", "ceo@sensei.example", "Jane Roe"):
        assert secret not in stored


def test_the_routes_answer_over_http_and_keep_their_order(monkeypatch):
    monkeypatch.setattr(auth, "_seed_admins_from_file", lambda: frozenset())
    from api.main import app
    client = TestClient(app)
    client.get("/api/auth/login", follow_redirects=False)
    csrf = {"X-CSRF-Token": client.cookies.get("sea_csrf") or ""}
    oid = client.get("/api/auth/me").json()["user"]["oid"]
    _job(oid, "abc123")
    assert client.get("/api/solve/searches").json() == {"searches": []}
    kept = client.post("/api/solve/searches/abc123", headers=csrf)
    assert kept.status_code == 200 and kept.json()["id"] == "abc123"
    put = client.put("/api/solve/searches/abc123/shortlist", json={"ids": ["app:1"]}, headers=csrf)
    assert put.json() == {"shortlist": ["app:1"]}
    assert client.get("/api/solve/searches").json()["searches"][0]["shortlisted"] == 1
    assert client.delete("/api/solve/searches/abc123", headers=csrf).status_code == 200
    assert client.post("/api/solve/searches/abc123", headers={}).status_code == 403     # CSRF still guards it
