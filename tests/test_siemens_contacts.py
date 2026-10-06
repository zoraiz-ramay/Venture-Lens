"""Relevant Siemens contacts under Empower's Tool fit (core/directory_api.py, core/siemens_contacts.py).

These tests use a fake HTTP session for the Directory 7.0.0 OpenAPI contract: apikey header,
bracketed filters and pagination, JSON:API resources, and only three contact fields out.
What is pinned is what a reviewer relies on:
the department is shown as supplied, a broad one is ranked rather than dumped, every contact is
labelled an inference, and a missing key says "not configured" instead of "nobody".
"""
import pytest
from fastapi.testclient import TestClient

from core import config, siemens_contacts as SC, web
from core.directory_api import DirectoryClient, DirectoryError


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Session:
    """Answers each GET from ``pages`` in turn and records the requests."""

    def __init__(self, *pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]


def person(name, dept, email=None, **extra):
    return {"commonName": name, "department": dept, "email": email or f"{name.split()[0].lower()}@siemens.test", **extra}


def jsonapi_page(people, cursor=None):
    return {"data": [{"type": "person", "attributes": entry} for entry in people],
            "meta": {"page": {"size": len(people), "nextCursor": cursor}}}


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(web, "_cached", lambda *a, **k: None)
    monkeypatch.setattr(web, "_store", lambda *a, **k: None)


TOOL = {"id": "tool:teamcenter", "name": "Teamcenter", "category": "PLM / Collaboration", "division": "DI SW PLM"}


def test_the_client_sends_the_key_header_and_the_department_filter_and_returns_three_fields():
    session = _Session(_Resp(200, jsonapi_page([person("Ada Lovelace", "DI SW PLM", gid="Z001", phone="+49 1")])))
    out = DirectoryClient(api_key="k", session=session).people_in("DI SW PLM")
    assert out == [{"name": "Ada Lovelace", "department": "DI SW PLM", "email": "ada@siemens.test"}]
    call = session.calls[0]
    assert call["url"].endswith("/people") and call["headers"] == {"apikey": "k"}
    assert call["params"] == {"filter[department]": "DI SW PLM", "page[limit]": config.SIEMENS_DIRECTORY_LIMIT}


def test_a_null_field_stays_empty_and_a_person_with_neither_name_nor_email_is_dropped():
    session = _Session(_Resp(200, jsonapi_page([
        {"commonName": "Grace Hopper", "department": None, "email": None},
        {"commonName": None, "department": "DI", "email": None}])))
    assert DirectoryClient(api_key="k", session=session).people_in("DI") == [
        {"name": "Grace Hopper", "department": None, "email": None}]


def test_pages_are_followed_by_cursor_up_to_the_cap():
    session = _Session(_Resp(200, jsonapi_page([person("A One", "DI")], "c2")),
                       _Resp(200, jsonapi_page([person("B Two", "DI")])))
    out = DirectoryClient(api_key="k", session=session).people_in("DI")
    assert [p["name"] for p in out] == ["A One", "B Two"]
    assert session.calls[1]["params"]["page[cursor]"] == "c2"
    assert session.calls[1]["params"]["filter[department]"] == "DI"
    assert "page[cursor]" not in session.calls[0]["params"]


def test_an_invalid_cursor_restarts_the_search_from_the_beginning():
    session = _Session(_Resp(200, jsonapi_page([person("A One", "DI")], "stale")),
                       _Resp(400, text="invalid cursor"),
                       _Resp(200, jsonapi_page([person("A One", "DI")], "c2")),
                       _Resp(200, jsonapi_page([person("B Two", "DI")])))
    out = DirectoryClient(api_key="k", session=session).people_in("DI")
    assert [p["name"] for p in out] == ["A One", "B Two"]                    # no duplicate, nothing missed
    assert config.SIEMENS_DIRECTORY_CURSOR_PARAM not in session.calls[2]["params"]


def test_jsonapi_pagination_stops_at_the_configured_cap(monkeypatch):
    monkeypatch.setattr(config, "SIEMENS_DIRECTORY_MAX_PAGES", 2)
    session = _Session(_Resp(200, jsonapi_page([person("A One", "DI")], "c2")),
                       _Resp(200, jsonapi_page([person("B Two", "DI")], "c3")),
                       _Resp(200, jsonapi_page([person("C Three", "DI")])))
    assert len(DirectoryClient(api_key="k", session=session).people_in("DI")) == 2
    assert len(session.calls) == 2


@pytest.mark.parametrize("limit, expected", [(0, 1), (101, 100)])
def test_page_limit_respects_openapi_bounds(monkeypatch, limit, expected):
    monkeypatch.setattr(config, "SIEMENS_DIRECTORY_LIMIT", limit)
    session = _Session(_Resp(200, jsonapi_page([])))
    DirectoryClient(api_key="k", session=session).people_in("DI")
    assert session.calls[0]["params"]["page[limit]"] == expected


def test_legacy_flat_responses_remain_readable():
    session = _Session(_Resp(200, {"people": [person("Ada Lovelace", "DI")]}))
    assert DirectoryClient(api_key="k", session=session).people_in("DI")[0]["name"] == "Ada Lovelace"


def test_null_jsonapi_attributes_do_not_crash_contact_lookup():
    session = _Session(_Resp(200, {"data": [{"type": "person", "attributes": None}],
                                  "meta": {"page": {"nextCursor": None}}}))
    assert DirectoryClient(api_key="k", session=session).people_in("DI") == []


def test_a_failed_request_is_an_error_not_an_empty_department():
    with pytest.raises(DirectoryError):
        DirectoryClient(api_key="k", session=_Session(_Resp(503))).people_in("DI")


@pytest.mark.parametrize("status, expected", [(401, "authentication failed"), (403, "access denied")])
def test_authentication_and_authorisation_failures_are_actionable_without_exposing_secrets(status, expected):
    session = _Session(_Resp(status, {"message": "You cannot consume this service"}, text="sensitive response"))
    with pytest.raises(DirectoryError) as failure:
        DirectoryClient(api_key="secret-test-key", session=session).people_in("DI")  # secret-scan:allow (placeholder test value)
    message = str(failure.value)
    assert expected in message and str(status) in message
    assert "secret-test-key" not in message and "sensitive response" not in message
    assert len(session.calls) == 1


def test_denied_directory_access_is_visible_in_tool_contact_lookup_and_not_cached(monkeypatch):
    cached = []
    monkeypatch.setattr(web, "_store", lambda *args, **kwargs: cached.append(args))
    client = DirectoryClient(api_key="k", session=_Session(_Resp(403)))
    result = SC.contacts([TOOL], client)["tools"][0]
    assert result["contacts"] == []
    assert "access denied (403)" in result["note"] and "subscription" in result["note"]
    assert "Nobody" not in result["note"] and cached == []


def test_a_broad_department_is_ranked_by_how_closely_each_person_matches_the_tool():
    people = [{"name": "Gen Eral", "department": "DI", "email": "g@s"},
              {"name": "Plm Person", "department": "DI SW PLM", "email": "p@s"},
              {"name": "Other Team", "department": "DI FA", "email": "o@s"},
              {"name": "Plm Deep", "department": "DI SW PLM PD", "email": "d@s"},
              {"name": "Plm Word", "department": "DI SW CS PLM", "email": "w@s"}]
    ranked = SC.rank(people, {**TOOL, "division": "DI"}, "DI")
    assert [p["name"] for p in ranked] == ["Plm Deep", "Plm Word", "Plm Person"]
    assert len(ranked) == config.SIEMENS_CONTACTS_PER_TOOL


def test_the_department_is_kept_as_supplied_and_a_translated_one_says_so():
    class Fake:
        def people_in(self, dept):
            return [{"name": "Ann", "department": dept + " X", "email": "a@s"}]

    row = SC.contacts_for_tool({**TOOL, "division": "Digital Industries Software"}, Fake())
    assert row["department"] == "Digital Industries Software"
    assert row["department_query"] == "DI SW" and row["translated"] is True
    plain = SC.contacts_for_tool({**TOOL, "division": "FT"}, Fake())
    assert plain["department"] == plain["department_query"] == "FT" and plain["translated"] is False


def test_every_answer_carries_the_inference_label_and_no_key_reads_not_configured(monkeypatch):
    monkeypatch.delenv("SIEMENS_DIRECTORY_API_KEY", raising=False)
    assert SC.contacts([TOOL])["status"] == "not_configured"
    assert SC.contacts([])["status"] == "no_tools"

    class Fake:
        def people_in(self, dept):
            return []

    out = SC.contacts([TOOL], Fake())
    assert out["status"] == "ok" and "not tool ownership" in out["note"]
    assert out["tools"][0]["note"] == "Nobody is listed under DI SW PLM."


def test_a_directory_failure_is_reported_on_the_tool_not_raised():
    class Down:
        def people_in(self, dept):
            raise DirectoryError("timed out")

    row = SC.contacts([TOOL], Down())["tools"][0]
    assert row["contacts"] == [] and "could not be searched" in row["note"]


@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    from api import store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "runs.db"))
    monkeypatch.setattr(store, "_restore_from_s3", lambda: None)
    monkeypatch.setattr(store, "_upload_to_s3", lambda: None)
    return store


def test_the_endpoint_reads_the_tools_tool_fit_cites_and_needs_a_session(monkeypatch, temp_db):
    from api import main, store
    run = {"company": "Contacts GmbH", "found": True, "assessment": {"pillars": {"Empower": {"criteria": [
        {"id": "tool_fit", "catalog": [TOOL, {"id": "need:x", "name": "Not a tool"}]}]}}}}
    run_id = store.save_run(run)
    seen = {}
    monkeypatch.setattr(SC, "contacts", lambda tools, refresh=False: seen.update(tools=tools) or {"status": "ok"})
    client = TestClient(main.app)
    assert client.post(f"/api/runs/{run_id}/lookup/contacts").status_code in (401, 403)
    client.get("/api/auth/login", follow_redirects=False)
    headers = {"X-CSRF-Token": client.cookies.get("sea_csrf")}
    assert client.post(f"/api/runs/{run_id}/lookup/contacts", headers=headers).json() == {"status": "ok"}
    assert [t["name"] for t in seen["tools"]] == ["Teamcenter"]


@pytest.mark.parametrize("tool_count", [4, 7])
def test_the_endpoint_looks_up_all_recommended_tools_up_to_five(monkeypatch, temp_db, tool_count):
    from api import main, store
    tools = [{"id": f"tool:recommended-{index}", "name": f"Recommended {index}",
              "division": "DI SW", "category": "Engineering"} for index in range(tool_count)]
    run = {"company": "Recommended Contacts GmbH", "found": True, "assessment": {"pillars": {"Empower": {
        "recommended_tools": tools,
        "criteria": [{"id": "tool_fit", "catalog": [tools[0]]}]}}}}
    run_id = store.save_run(run)
    seen = {}
    monkeypatch.setattr(SC, "contacts", lambda tools, refresh=False:
                        seen.update(tools=tools, refresh=refresh) or {"status": "ok"})
    client = TestClient(main.app)
    client.get("/api/auth/login", follow_redirects=False)
    headers = {"X-CSRF-Token": client.cookies.get("sea_csrf")}
    response = client.post(f"/api/runs/{run_id}/lookup/contacts?refresh=true", headers=headers)
    assert response.status_code == 200
    assert seen["tools"] == tools[:5]
    assert seen["refresh"] is True


def test_the_endpoint_deduplicates_tools_and_excludes_non_tool_entries(monkeypatch, temp_db):
    from api import main, store
    recommended = [{**TOOL, "name": " TEAMCENTER "}, None, {"id": "need:x", "name": "Not a tool"},
                   {"id": "tool:blank", "name": " "},
                   {"id": "tool:nx", "name": "NX", "division": "DI SW"}]
    run = {"company": "Distinct Contacts GmbH", "found": True, "assessment": {"pillars": {"Empower": {
        "recommended_tools": recommended,
        "criteria": [{"id": "tool_fit", "catalog": [TOOL]}]}}}}
    run_id = store.save_run(run)
    seen = {}
    monkeypatch.setattr(SC, "contacts", lambda tools, refresh=False: seen.update(tools=tools) or {"status": "ok"})
    client = TestClient(main.app)
    client.get("/api/auth/login", follow_redirects=False)
    headers = {"X-CSRF-Token": client.cookies.get("sea_csrf")}
    assert client.post(f"/api/runs/{run_id}/lookup/contacts", headers=headers).status_code == 200
    assert seen["tools"] == [recommended[0], recommended[-1]]
