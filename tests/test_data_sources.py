"""api/sources.py: the Databases page's read-only view of the files the engine reads.

The point of these tests is what never leaves the server. The applications workbook carries an
application's internal `status`, its contact person (`contact_name`, `contact_email`,
`submission_title` = "Information provided by <name>") and a free-text `Referral` that names
Siemens staff; the product owner asked that status is never shown, and a contact is personal
data. Each source is served through an allow-list, so the tests check both the shipped files and a
planted workbook whose sensitive cells — and an unknown new column — must not reach a response.
"""
import json
import os
import pathlib
import re
import sys
import tempfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api import auth, sources, store  # noqa: E402
import api.main as main  # noqa: E402
from api.main import app  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBOOK = ROOT / "data" / "glassdollar_applications.xlsx"
SENSITIVE_KEYS = {"status", "contact_name", "contact_email", "Referral", "submission_title",
                  "pdf_local_path", "archived_at", "asked_by", "reviewer", "reviewer_oid"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
INTL_PHONE = re.compile(r"(?<![\w+])(?:\+|00)\d[\d ().\-/]{6,}\d")


@pytest.fixture()
def signed_in(monkeypatch) -> TestClient:
    path = pathlib.Path(tempfile.mkdtemp()) / "runs.db"
    monkeypatch.setattr(store, "DB_PATH", str(path))
    monkeypatch.setattr(store, "_restore_from_s3", lambda: None)
    monkeypatch.setattr(store, "_upload_to_s3", lambda: None)
    monkeypatch.setattr(auth, "_seed_admins_from_file", lambda: frozenset())
    sources._ROWS.clear()
    client = TestClient(app)
    client.get("/api/auth/login", follow_redirects=False)
    return client


def _all_rows(client, source_id: str) -> tuple[dict, list[dict]]:
    """Every row of a source, paged as the UI pages, plus the first page's envelope."""
    first = client.get(f"/api/sources/{source_id}", params={"limit": 100}).json()
    rows, offset = list(first["items"]), 100
    while offset < first["total"]:
        rows += client.get(f"/api/sources/{source_id}", params={"limit": 100, "offset": offset}).json()["items"]
        offset += 100
    return first, rows


def test_sources_are_behind_the_session_guard():
    client = TestClient(app)
    assert client.get("/api/sources").status_code == 401
    assert client.get("/api/sources/applications").status_code == 401


def test_every_source_says_what_it_is_and_identifies_its_file(signed_in):
    listed = {s["id"]: s for s in signed_in.get("/api/sources").json()["sources"]}
    assert set(listed) == {"applications", "tools", "xcelerator", "needs", "challenges"}
    assert listed["applications"]["file"] == "glassdollar_applications.xlsx"
    assert listed["tools"]["file"] == "siemens_tools.csv"
    assert listed["xcelerator"]["file"] == "siemens_xcelerator_data.xlsx"
    assert listed["needs"]["file"] == "Startup_Evaluator_Departmental_Requirements.xlsx"
    assert listed["challenges"]["file"] == "challenges.json"
    for s in listed.values():
        assert s["available"] and s["about"] and s["feeds"] and s["modified"]
        assert s["rows"] > 0 and "items" not in s          # the list is metadata, never rows
    # The catalogs' own checksums, the ones every assessment records; the library has none.
    for sid in ("applications", "tools", "xcelerator", "needs"):
        assert re.fullmatch(r"[0-9a-f]{64}", listed[sid]["checksum"])
    assert listed["challenges"]["checksum"] is None
    assert listed["applications"]["rows"] == 428            # 429 rows, one with no company name


def test_the_applications_source_says_whether_the_live_api_is_in_use(signed_in, monkeypatch):
    monkeypatch.setattr(main, "_gd_key", lambda: False)
    offline = signed_in.get("/api/sources/applications", params={"limit": 1}).json()
    assert offline["live_api"] is False and "only startup database" in offline["note"]
    monkeypatch.setattr(main, "_gd_key", lambda: True)
    live = signed_in.get("/api/sources/applications", params={"limit": 1}).json()
    assert live["live_api"] is True and "live GlassDollar API" in live["note"]


def test_no_shipped_source_serves_a_sensitive_column_or_value(signed_in):
    raw = pd.read_excel(WORKBOOK).fillna("")
    contact_emails = {str(v).strip().lower() for v in raw["contact_email"] if str(v).strip()}
    referrals = {str(v).strip().lower() for v in raw["Referral"] if len(str(v).strip()) > 40}
    contact_names = {str(v).strip().lower() for v in raw["contact_name"] if len(str(v).split()) >= 2}
    for sid in sources.SOURCES:
        envelope, rows = _all_rows(signed_in, sid)
        allowed = {c["key"] for c in envelope["columns"]}
        assert not allowed & SENSITIVE_KEYS, sid
        assert len(rows) == envelope["total"]
        for row in rows:
            assert set(row) == allowed, sid
        text = json.dumps(rows, ensure_ascii=False).lower()
        assert not EMAIL.search(text), (sid, EMAIL.search(text))
        assert not INTL_PHONE.search(text), (sid, INTL_PHONE.search(text))
        assert "information provided by" not in text
        assert not [e for e in contact_emails if e in text], sid
        assert not [r for r in referrals if r in text], sid
        assert not [n for n in contact_names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text)], sid


def _planted_workbook(tmp_path: pathlib.Path) -> str:
    path = tmp_path / "applications.xlsx"
    pd.DataFrame([{
        "startup_id": 1, "submission_title": "Information provided by Jane Q Planted",
        "status": "secret-status-xyz", "contact_name": "Jane Q Planted",
        "contact_email": "jane@planted-startup.io", "Referral": "Referred by Hans Insider of Siemens",
        "company_name": "Planted Robotics", "website": "https://planted.example",
        "Your pitch": "We grade welds. Call Jane Q Planted on +44 20 7946 0958, Tel: 0171 2345678, "
                      "or jane@planted-startup.io. Built on IEC 60870-5-104.",
        "Business model": "SaaS", "secret_new_column": "should-not-leak",
    }]).to_excel(path, index=False)
    return str(path)


def test_a_planted_workbook_leaks_nothing_but_its_allow_listed_columns(signed_in, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_LOCAL_XLSX", _planted_workbook(tmp_path))
    page = signed_in.get("/api/sources/applications").json()
    text = json.dumps(page, ensure_ascii=False)
    for leaked in ("secret-status-xyz", "Jane Q Planted", "planted-startup.io", "Hans Insider",
                   "should-not-leak", "secret_new_column", "7946", "2345678"):
        assert leaked not in text, leaked
    (row,) = page["items"]
    assert row["company"] == "Planted Robotics" and row["business_model"] == "SaaS"
    # Redacted in place, not dropped: the rest of the pitch is the useful part.
    assert row["pitch"].startswith("We grade welds. Call [contact removed] on [phone removed]")
    assert "Tel: [phone removed]" in row["pitch"] and "[email removed]" in row["pitch"]
    assert "IEC 60870-5-104" in row["pitch"]                   # a standard, not a phone number


def test_the_problem_library_hides_who_asked_and_its_approval_status(signed_in, monkeypatch, tmp_path):
    import core.solve as solve
    path = tmp_path / "challenges.json"
    path.write_text(json.dumps([{"problem": "Predict transformer failures", "keywords": ["grid", "failure"],
                                 "asked_by": "asker@siemens.com", "status": "rejected",
                                 "reviewer": "Rita Reviewer", "reviewer_oid": "oid-123",
                                 "created_at": "2026-07-21T09:53:40+00:00"}]), encoding="utf-8")
    monkeypatch.setattr(solve, "CHALLENGES_PATH", str(path))
    page = signed_in.get("/api/sources/challenges").json()
    assert page["items"] == [{"problem": "Predict transformer failures", "keywords": "grid; failure",
                              "created_at": "2026-07-21T09:53:40+00:00"}]
    text = json.dumps(page)
    assert "asker@siemens.com" not in text and "Rita Reviewer" not in text
    assert "rejected" not in text and "oid-123" not in text


def test_search_and_paging_are_server_side_and_capped(signed_in):
    everything = signed_in.get("/api/sources/tools", params={"limit": 1}).json()
    found = signed_in.get("/api/sources/tools", params={"q": "teamcenter plm", "limit": 100}).json()
    assert 0 < found["total"] < everything["total"]
    assert all("teamcenter" in " ".join(r.values()).lower() and "plm" in " ".join(r.values()).lower()
               for r in found["items"])
    capped = signed_in.get("/api/sources/tools", params={"limit": 100000}).json()
    assert capped["limit"] == sources.PAGE_MAX and len(capped["items"]) == sources.PAGE_MAX
    second = signed_in.get("/api/sources/tools", params={"limit": 5, "offset": 5}).json()
    first = signed_in.get("/api/sources/tools", params={"limit": 10}).json()
    assert second["items"] == first["items"][5:10]
    past_end = signed_in.get("/api/sources/tools", params={"offset": 10 ** 6}).json()
    assert past_end["items"] == [] and past_end["total"] == everything["total"]
    assert signed_in.get("/api/sources/runs.db").status_code == 404


def test_a_missing_file_is_reported_unavailable_not_as_an_error(signed_in, monkeypatch):
    from core import catalogs
    monkeypatch.setattr(catalogs, "XCELERATOR_XLSX", str(ROOT / "data" / "no-such.xlsx"))
    monkeypatch.setattr(catalogs, "xcelerator_catalog",
                        lambda path=catalogs.XCELERATOR_XLSX: catalogs._unavailable("xcelerator", "Xcelerator workbook not found"))
    page = signed_in.get("/api/sources/xcelerator").json()
    assert page["available"] is False and page["reason"] == "Xcelerator workbook not found"
    assert page["items"] == [] and page["total"] == 0


def test_scrub_removes_contact_details_but_keeps_standards_dates_and_ids():
    scrub = sources._scrub
    assert scrub("M. 07486657357") == "M. [phone removed]"
    assert scrub("Mob 582081674") == "Mob [phone removed]"
    assert scrub("Telefon: +49 (0)175 8765712") == "Telefon: [phone removed]"
    assert scrub("[ info@health-book.net / 00201032301455 ]") == "[ [email removed] / [phone removed] ]"
    for kept in ("IEC 60870-5-104", "retrieved 2026-06-25", "ISO 9001-2015", "(1990-2009)",
                 "Zeitlimit bis 30.07.2026", "+20 years", "PIIS2666-5247(24)00265-9"):
        assert scrub(kept) == kept
