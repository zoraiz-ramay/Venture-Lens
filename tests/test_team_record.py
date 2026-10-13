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


def test_a_companys_first_open_searches_once_then_reads_the_store_and_refresh_searches_again(monkeypatch, poll):
    from api import main
    saved, searched, web = [], [], []
    run = {"company": "Teamrec GmbH", "profile": {"website": "https://teamrec.test"},
           "deep_profile": {"founders": [ADA], "programs": [],
                            "commercial": {"investors": [{"name": "Acme VC", "source_url": "https://acme.vc"}]}}}
    monkeypatch.setattr(main, "_run_for", lambda run_id, user: run)
    monkeypatch.setattr("core.profile.search_team_web", lambda company, website, llm: web.append(website) or
                        {"founders": [{**ADA, "role": "CEO and co-founder"}], "programs": [PROG]})
    monkeypatch.setattr(main.store, "latest_enrichment", lambda company, kind, run_id=None: saved[-1] if saved else None)
    monkeypatch.setattr(main.store, "save_enrichment", lambda company, kind, payload, **kw: saved.append(payload))
    monkeypatch.setattr("core.profile.refresh_team", lambda company, row, llm: searched.append(company) or
                        {"founders": [], "programs": [PROG], "investors": []})
    client = TestClient(main.app)
    client.get("/api/auth/login", follow_redirects=False)
    csrf = {"X-CSRF-Token": client.cookies.get("sea_csrf")}

    first = poll(client, "/api/runs/7401/lookup/team", csrf).json()
    assert web == ["https://teamrec.test"] and searched == [] and first["from_store"] is False
    assert [p["name"] for p in first["programs"]] == ["Techstars"]
    again = client.post("/api/runs/7401/lookup/team", headers=csrf).json()     # stored: read, not searched
    assert web == ["https://teamrec.test"] and again["from_store"] and [p["name"] for p in again["programs"]] == ["Techstars"]
    fresh = poll(client, "/api/runs/7401/lookup/team?refresh=true", csrf).json()
    assert searched == ["Teamrec GmbH"] and [p["name"] for p in fresh["programs"]] == ["Techstars"]


def test_a_stored_team_record_is_read_without_searching(monkeypatch):
    run = {"company": "Fullteam GmbH", "deep_profile": {"founders": [ADA], "commercial": {"investors": []}}}
    web = []
    stored = {"founders": [ADA], "programs": [PROG], "retrieved_at": "2026-10-08T10:00:00+00:00"}
    _, client, csrf = _team_client(monkeypatch, run, {"team": stored, "funding": FUNDING}, [],
                                   web_team=lambda *a: web.append(a) or {})
    out = client.post("/api/runs/7404/lookup/team", headers=csrf).json()
    assert web == [] and out["from_store"] and [p["name"] for p in out["programs"]] == ["Techstars"]
    assert [i["name"] for i in out["investors"]][:1] == ["UVC Partners"]


@pytest.mark.parametrize("filler", ["Dr. Andreas Wagner; no prior companies or roles are specified in the results.",
                                    "Dr. Andreas Wagner", "Background not disclosed"])
def test_a_background_that_says_there_is_none_is_shown_as_nothing_and_replaced(filler):
    run = {"founders": [{"name": "Andreas Wagner", "role": "Co-founder", "background": filler}]}
    assert P.merge_team(run)["founders"][0]["background"] == ""
    assert P.merge_team({"founders": [{"name": "A B", "background": "Chemist"}]})["founders"][0]["background"] == "Chemist"
    found = {"founders": [{"name": "Andreas Wagner", "background": "PhD, Cambridge; Systemiq", "background_source": "https://x.test"}]}
    assert P.merge_team(run, found)["founders"][0]["background"] == "PhD, Cambridge; Systemiq"


FUNDING = {"provider": "web", "rounds": [{"date": "March 2025", "amount": "€2.7 million",
                                         "lead_investors": ["UVC Partners"], "investors": ["Jane Angel"],
                                         "sources": [{"title": "t", "url": "https://news.test/round"}]}],
           "investors": [{"name": "UVC Partners", "sources": [{"url": "https://uvc.test"}]},
                         {"name": "Tracxn Only Fund", "sources": [{"title": "Tracxn", "url": ""}]}]}


def _team_client(monkeypatch, run, enrichments, saved, web_team=None):
    from api import main
    monkeypatch.setattr(main, "_run_for", lambda run_id, user: run)
    monkeypatch.setattr("core.profile.search_team_web", web_team or (lambda *a: {}))
    monkeypatch.setattr(main.store, "latest_enrichment",
                        lambda company, kind, run_id=None: enrichments.get(kind))
    monkeypatch.setattr(main.store, "save_enrichment",
                        lambda company, kind, payload, **kw: saved.append(kind) or enrichments.__setitem__(kind, payload))
    client = TestClient(main.app)
    client.get("/api/auth/login", follow_redirects=False)
    return main, client, {"X-CSRF-Token": client.cookies.get("sea_csrf")}


def test_a_run_without_investors_shows_the_funding_lookups_with_their_sources(monkeypatch):
    """Radical Dot's run found no investors; its funding lookup named the lead and the angels."""
    run = {"company": "Noinv GmbH", "deep_profile": {"founders": [{**ADA, "background": "Analytical Engine"}],
                                                     "programs": [PROG, {**PROG, "name": "EIT Manufacturing"}],
                                                     "commercial": {"investors": []}}}
    main, client, csrf = _team_client(monkeypatch, run, {"funding": FUNDING, "team": {"founders": [ADA]}}, [])
    out = client.post("/api/runs/7501/lookup/team", headers=csrf).json()
    assert [(i["name"], i["source_url"]) for i in out["investors"]] == [
        ("UVC Partners", "https://news.test/round"), ("Jane Angel", "https://news.test/round"),
        ("Tracxn Only Fund", "")]
    assert out["investors"][2]["source"] == "Tracxn"
    assert [f["name"] for f in out["founders"]] == ["Ada Lovelace"]


def test_with_no_funding_lookup_stored_it_runs_once_in_the_background_and_is_stored(monkeypatch, poll):
    run = {"company": "Noinv2 GmbH", "profile": {"website": "https://noinv.test"},
           "deep_profile": {"founders": [ADA], "commercial": {"investors": []}}}
    enrichments, saved, searched = {}, [], []
    main, client, csrf = _team_client(monkeypatch, run, enrichments, saved)
    monkeypatch.setattr(main, "tracxn_client_for", lambda user, llm=None: None)
    monkeypatch.setattr("core.traction_lookup.funding_details",
                        lambda company, **kw: searched.append(company) or dict(FUNDING))
    first = client.post("/api/runs/7502/lookup/team", headers=csrf).json()
    assert first == {"pending": True}
    out = poll(client, "/api/runs/7502/lookup/team", csrf).json()
    assert [i["name"] for i in out["investors"]][:2] == ["UVC Partners", "Jane Angel"]
    assert searched == ["Noinv2 GmbH"] and saved == ["funding", "team"]   # Traction reads the same lookup
    client.post("/api/runs/7502/lookup/team", headers=csrf)
    assert searched == ["Noinv2 GmbH"]


def test_a_funding_lookup_that_finds_nothing_is_not_repeated_on_every_open(monkeypatch, poll):
    from api.auth import sessions
    run = {"company": "Noinv3 GmbH", "deep_profile": {"founders": [ADA], "commercial": {"investors": []}}}
    searched = []
    main, client, csrf = _team_client(monkeypatch, run, {}, [])
    sessions().delete("team-funding-tried:noinv3 gmbh")
    monkeypatch.setattr(main, "tracxn_client_for", lambda user, llm=None: None)
    monkeypatch.setattr("core.traction_lookup.funding_details",
                        lambda company, **kw: searched.append(company) or {"provider": "none", "rounds": [], "investors": []})
    poll(client, "/api/runs/7503/lookup/team", csrf)
    out = client.post("/api/runs/7503/lookup/team", headers=csrf).json()
    assert searched == ["Noinv3 GmbH"] and out["investors"] == [] and [f["name"] for f in out["founders"]] == ["Ada Lovelace"]


# ------------------------------------------------- team & ecosystem from the model's web search

class _WebTeam:
    """The model's web search answers with notes; the extraction names the page behind each item."""
    available = True

    def __init__(self, extracted, answers=("notes",)):
        self.extracted, self.answers, self.asked = extracted, list(answers), []

    def web_answer(self, prompt, **kw):
        self.asked.append(prompt)
        text = self.answers.pop(0) if self.answers else "notes"
        return {"text": text, "sources": []} if text else None

    def complete(self, prompt, **kw):
        import json
        return json.dumps(self.extracted)


MENU = "Home About Programs Startups Events News Contact " * 40          # ~1,900 characters of site chrome
PAGES = {
    "https://news.test/round": "Radical Dot raises €2.7M. Co-founder and CEO Dr. Andreas Wagner said the round funds a prototype.",
    "https://hec.test/kremer": "Alumni story: Alexandre Kremer studied chemistry at ESCPE Lyon and UBC, then Sustainability "
                               "and Social Innovation at HEC Paris, and worked at Systemiq.",
    "https://news.test/kremer": "Alexandre Kremer, co-founder and COO of Radical Dot, joined from Systemiq.",
    "https://accelerator.test/news/xpreneurs-batch-15": MENU + "XPRENEURS Batch 15 starts. " + "Team bios. " * 160 + "Radical Dot recycles plastic.",
    "https://list.test/top-startups": "Y Combinator funds many startups. " + "Filler. " * 400 + "Radical Dot is a Munich startup.",
    "https://valley.test/batch6": "Circular Valley Batch 6: Radical Dot turns plastic waste into chemicals.",
    "https://radicaldot.test/about": "Radical Dot is a member of the ChemSPACE community.",
    "https://someone.test/namesake": "Andreas Wagner, a pianist from Vienna, played Mozart.",
}
EXTRACTED = {
    "founders": [
        {"name": "Dr. Andreas Wagner", "role": "Co-founder and CEO", "background": "PhD in Chemistry, University of Cambridge",
         "source_urls": ["https://news.test/round"]},
        {"name": "Alexandre Kremer", "role": "Co-founder and COO", "source_urls": ["https://news.test/kremer", "https://hec.test/kremer"],
         "background": "Chemistry at ESCPE Lyon and UBC; Sustainability and Social Innovation at HEC Paris; Systemiq"},
        {"name": "Andreas Wagner", "role": "Pianist", "source_urls": ["https://someone.test/namesake"]},
        {"name": "Ghost Founder", "role": "CTO", "source_urls": ["https://unreachable.test/x"]}],
    "programs": [
        {"name": "XPRENEURS", "type": "accelerator", "source_urls": ["https://accelerator.test/news/xpreneurs-batch-15"]},
        {"name": "Y Combinator", "type": "accelerator", "source_urls": ["https://list.test/top-startups"]},
        {"name": "Circular Valley", "type": "accelerator", "source_urls": ["https://valley.test/batch6"]},
        {"name": "ChemSPACE", "type": "incubator", "source_urls": ["https://radicaldot.test/about"]}],
    "partners": [{"name": "TUM", "type": "university", "source_urls": ["https://unreachable.test/x"]}],
}


@pytest.fixture
def pages(monkeypatch):
    monkeypatch.setattr(P, "_read_pages", lambda urls: {u: PAGES[u] for u in urls if u in PAGES})


def test_a_founder_is_kept_only_where_a_page_names_them_at_the_company(pages):
    out = P.search_team_web("Radical Dot", "https://radicaldot.test", _WebTeam(EXTRACTED))
    names = [f["name"] for f in out["founders"]]
    assert names == ["Dr. Andreas Wagner", "Alexandre Kremer"]          # not the pianist, not the unreadable page
    wagner, kremer = out["founders"]
    assert wagner["role"] == "Co-founder and CEO" and wagner["source_url"] == "https://news.test/round"
    # "PhD ... Cambridge" is on no page that names him: dropped, not trusted.
    assert wagner["background"] == ""
    # Kremer's background is on his HEC profile, which does not name the startup: kept, linked there.
    assert kremer["background"].startswith("Chemistry at ESCPE") and kremer["background_source"] == "https://hec.test/kremer"
    assert all(f["linkedin"] == "" for f in out["founders"])


def test_a_programme_counts_on_its_own_cohort_page_near_the_company_or_on_the_company_site(pages):
    out = P.search_team_web("Radical Dot", "https://radicaldot.test", _WebTeam(EXTRACTED))
    by_name = {p["name"]: p for p in out["programs"]}
    # XPRENEURS: 1,900 characters of menu and 1,700 of bios from Radical Dot, but it is the cohort's own page.
    assert set(by_name) == {"XPRENEURS", "Circular Valley", "ChemSPACE"}    # not YC, mentioned far away on a list
    assert by_name["XPRENEURS"]["confidence"] == "corroborated"
    assert by_name["ChemSPACE"]["confidence"] == "self_asserted"          # the company's own site claims it
    assert out["partners"] == []                                           # its page could not be read


def test_a_failed_web_search_is_asked_once_more_and_nothing_found_is_empty(pages):
    llm = _WebTeam(EXTRACTED, answers=("", "", "", ""))
    assert P.search_team_web("Radical Dot", "", llm) == {}
    assert len(llm.asked) == 4                                             # two searches, each tried twice


def test_merging_fills_a_known_founders_blanks_and_extends_a_role_without_replacing_it():
    run = {"founders": [{"name": "Andreas Wagner", "role": "Co-founder", "source_url": "https://tum.test"},
                        {"name": "Alexandre Kremer", "role": "COO", "background": "HEC Paris"}]}
    found = {"founders": [{"name": "Dr. Andreas Wagner", "role": "Co-founder and CEO",
                           "background": "PhD, Cambridge", "background_source": "https://cam.test"},
                          {"name": "Alexandre Kremer", "role": "Head of Sales", "background": "Something else"}]}
    out = P.merge_team(run, found)["founders"]
    assert [f["name"] for f in out] == ["Andreas Wagner", "Alexandre Kremer"]      # positions kept, no duplicate
    assert out[0]["role"] == "Co-founder and CEO" and out[0]["background"] == "PhD, Cambridge"
    assert out[0]["background_source"] == "https://cam.test" and out[0]["source_url"] == "https://tum.test"
    assert out[1]["role"] == "COO" and out[1]["background"] == "HEC Paris"         # never overwritten


def test_the_team_record_is_found_by_any_name_the_company_is_reached_by(monkeypatch, tmp_path):
    """An evaluation looks its team record up by the name the reviewer typed (api/main.py), while
    the record is filed under the resolved name. Matching the exact name only, a refresh typed as
    "radical dot" never carried the team the section's own Refresh had stored."""
    from api import store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "runs.db"))
    monkeypatch.setattr(store, "_restore_from_s3", lambda: None)
    monkeypatch.setattr(store, "_upload_to_s3", lambda: None)
    store.save_run({"company": "Radical Dot GmbH", "found": True, "profile": {"website": "https://radicaldot.test"},
                    "deep_profile": {}}, aliases=["radical dot"])
    store.save_enrichment("Radical Dot GmbH", "team", {"founders": [ADA]})
    assert store.latest_enrichment("radical dot", "team")["founders"] == [ADA]
    assert store.latest_enrichment("Radical Dot GmbH", "team")["founders"] == [ADA]
    assert store.latest_enrichment("someone else", "team") is None


def test_a_searched_team_is_really_stored_and_the_next_open_reads_it(monkeypatch, tmp_path, poll):
    """The other tests stub the store; "team" was not a stored kind, so the save was a silent no-op
    and every open searched again."""
    from api import main, store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "runs.db"))
    monkeypatch.setattr(store, "_restore_from_s3", lambda: None)
    monkeypatch.setattr(store, "_upload_to_s3", lambda: None)
    run_id = store.save_run({"company": "Stored Team GmbH", "found": True, "profile": {},
                             "deep_profile": {"founders": [ADA], "commercial": {"investors": []}}})
    web = []
    monkeypatch.setattr(main, "tracxn_client_for", lambda user, llm=None: None)
    monkeypatch.setattr("core.traction_lookup.funding_details", lambda company, **kw: dict(FUNDING))
    monkeypatch.setattr("core.profile.search_team_web", lambda *a: web.append(1) or
                        {"programs": [PROG, {"name": "TUM Venture Labs", "type": "incubator", "source_url": "https://v.test"}],
                         "partners": [{"name": "Techstars", "source_url": "https://t.test"},
                                      {"name": "TUM Venture Labs ChemSPACE", "source_url": "https://v.test/c"},
                                      {"name": "TUM", "type": "university", "source_url": "https://tum.test"}]})
    client = TestClient(main.app)
    client.get("/api/auth/login", follow_redirects=False)
    csrf = {"X-CSRF-Token": client.cookies.get("sea_csrf")}
    first = poll(client, f"/api/runs/{run_id}/lookup/team", csrf).json()
    assert [p["name"] for p in first["partners"]] == ["TUM"]    # Techstars and ChemSPACE are listed as programmes
    again = client.post(f"/api/runs/{run_id}/lookup/team", headers=csrf).json()
    assert web == [1] and again["from_store"] is True
    assert [p["name"] for p in again["programs"]] == ["Techstars", "TUM Venture Labs"]
    assert [i["name"] for i in again["investors"]][0] == "UVC Partners"
    assert store.latest_enrichment("Stored Team GmbH", "team")["partners"][0]["name"] == "TUM"
