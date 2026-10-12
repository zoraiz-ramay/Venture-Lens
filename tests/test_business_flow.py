"""core/business_flow.py: five plain sentences about how a startup's business works.

The model only rephrases the run's own research. What is pinned is the gate every sentence has to
pass before it is shown: real citations, words carried by what it cites, no number the research
does not state — and a story too thin to tell is reported as such, not padded.
"""
import json
from unittest.mock import Mock

import pytest

from core import business_flow as B
from core.llm import LLMClient

search_steps = B._search_steps      # the real one; the fixture below stubs it for every other test

RUN = {"company": "Radical Dot", "found": True,
       "summary": "Radical Dot converts mixed, unrecyclable plastic waste into acetic acid through its Oxolysis process.",
       "profile": {"Business model": "Today, revenue comes from R&D partnerships with chemical companies."}}


def model(reply: dict):
    llm = Mock(available=True)
    llm.complete.return_value = json.dumps(reply)
    return llm


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(B.web, "_cached", lambda *a, **k: None)
    monkeypatch.setattr(B.web, "_store", lambda *a, **k: None)
    # No site and no search unless a test supplies them: the missing-step search is pinned below.
    monkeypatch.setattr(B.web, "fetch_site_text", lambda host, *a, **k: {})
    monkeypatch.setattr(B, "_search_steps", lambda company, site, missing, llm: [])


def ids():
    by_source = {r["source"]: r["id"] for r in B._evidence(RUN)}
    return by_source["summary"], by_source["profile.Business model"]


def test_sentences_carried_by_their_sources_are_kept_in_story_order():
    s, m = ids()
    out = B.business_flow(RUN, model({
        "problem": {"text": "Mixed plastic waste cannot be recycled today.", "citations": [s]},
        "how": {"text": "Its Oxolysis process converts plastic waste into acetic acid.", "citations": [s]},
        "money": {"text": "It earns from R&D partnerships with chemical companies.", "citations": [m]}}))
    assert out["status"] == "ok"
    assert [x["id"] for x in out["steps"]] == ["problem", "how", "money"]
    assert out["steps"][2]["label"] == "How it makes money"
    assert out["steps"][1]["sources"][0]["quote"].startswith("Radical Dot converts")


def test_a_sentence_its_sources_do_not_carry_or_with_an_invented_number_is_dropped():
    s, m = ids()
    out = B.business_flow(RUN, model({
        "problem": {"text": "Mixed plastic waste cannot be recycled today.", "citations": [s]},
        "offer": {"text": "Satellite imaging for insurance underwriters.", "citations": [s]},           # not in the record
        "how": {"text": "Oxolysis converts 90% of plastic waste into acetic acid.", "citations": [s]},   # 90 is invented
        "customers": {"text": "Chemical companies partner with it.", "citations": ["E999"]},           # cites nothing real
        "money": {"text": "It earns from R&D partnerships with chemical companies.", "citations": [m]}}))
    assert [x["id"] for x in out["steps"]] == ["problem", "money"]


def test_a_story_of_fewer_than_two_steps_is_reported_as_too_thin():
    s, _ = ids()
    out = B.business_flow(RUN, model({"problem": {"text": "Mixed plastic waste cannot be recycled today.", "citations": [s]}}))
    assert out["status"] == "insufficient" and out["steps"] == []


def test_no_model_means_no_story():
    assert B.business_flow(RUN, Mock(available=False))["status"] == "unavailable"


def test_a_reply_with_a_stray_trailing_brace_still_parses():
    assert LLMClient.parse_json('```json\n{"a":{"b":1}}}\n```') == {"a": {"b": 1}}


def test_two_first_views_at_once_make_one_model_call(monkeypatch, poll):
    """Both used to miss the store and both paid for the model; the second now reads the first's."""
    import threading
    import time
    from fastapi.testclient import TestClient
    from api import main
    saved, calls = [], []

    def slow_flow(res, llm):
        calls.append(1)
        time.sleep(0.3)
        return {"status": "ok", "steps": [], "version": B.VERSION}

    monkeypatch.setattr("core.business_flow.business_flow", slow_flow)
    monkeypatch.setattr(main, "_run_for", lambda run_id, user: {"company": "Flowlock GmbH"})
    monkeypatch.setattr(main.store, "latest_enrichment", lambda company, kind, run_id=None: saved[-1] if saved else None)
    monkeypatch.setattr(main.store, "save_enrichment", lambda company, kind, payload, **kw: saved.append(payload))
    client = TestClient(main.app)
    client.get("/api/auth/login", follow_redirects=False)
    csrf = {"X-CSRF-Token": client.cookies.get("sea_csrf")}
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(
        poll(client, "/api/runs/7302/business-flow", csrf).status_code)) for _ in range(2)]  # secret-scan:allow
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [200, 200] and len(calls) == 1 and len(saved) == 1


# ----------------------------------------------------------------- a missing step is searched for

THIN = {"company": "Radical Dot", "found": True, "profile": {"website": "https://radicaldot.test/"},
        "summary": "Radical Dot converts hard-to-recycle plastic waste into chemical building blocks that "
                   "manufacturers can use as feedstocks."}
SITE = {"/about": "Our team in Munich. " * 20 + "Radical Dot sells its chemical building blocks to manufacturers "
                  "under long-term offtake supply contracts. " + "Join us. " * 30}


class _Model:
    """First call: only what the summary carries. Second call: the money step from the site."""

    def __init__(self):
        self.available, self.prompts = True, []

    def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return json.dumps({"problem": {"text": "Hard-to-recycle plastic waste is hard to use.", "citations": ["E1"]},
                               "offer": {"text": "It converts plastic waste into chemical building blocks.", "citations": ["E1"]}})
        site = next(line for line in prompt.splitlines() if line.startswith("[")) and "S1"
        return json.dumps({"money": {"text": "It sells its chemical building blocks to manufacturers under "
                                             "long-term offtake supply contracts.", "citations": [site]}})


def test_a_step_the_run_leaves_out_is_found_on_the_startups_own_site(monkeypatch):
    searched = []
    monkeypatch.setattr(B.web, "fetch_site_text", lambda host, *a, **k: SITE if host == "radicaldot.test" else {})
    monkeypatch.setattr(B, "_search_steps", lambda company, site, missing, llm: searched.append(list(missing)) or [])
    llm = _Model()
    out = B.business_flow(THIN, llm)
    assert [x["id"] for x in out["steps"]] == ["problem", "offer", "money"]
    money = out["steps"][2]
    assert money["sources"][0]["url"] == "https://radicaldot.test/about"
    assert "offtake supply" in money["sources"][0]["quote"]
    # Only the steps still missing are searched for and asked for again.
    assert searched == [["how", "customers", "money"]]
    assert "- money:" in llm.prompts[1] and "- problem:" not in llm.prompts[1]


def test_a_found_passage_still_has_to_carry_the_sentence(monkeypatch):
    monkeypatch.setattr(B.web, "fetch_site_text", lambda host, *a, **k: SITE)

    class Inventive(_Model):
        def complete(self, prompt, **kw):
            if self.prompts:
                self.prompts.append(prompt)
                return json.dumps({"money": {"text": "It charges a 12% royalty on every licensed plant.", "citations": ["S1"]}})
            return super().complete(prompt, **kw)
    out = B.business_flow(THIN, Inventive())
    assert [x["id"] for x in out["steps"]] == ["problem", "offer"]


def test_a_complete_story_searches_nothing(monkeypatch):
    s, m = ids()
    calls = []
    monkeypatch.setattr(B, "_search_steps", lambda *a: calls.append(a) or [])
    full = {"problem": {"text": "Mixed plastic waste cannot be recycled today.", "citations": [s]},
            "offer": {"text": "Radical Dot converts plastic waste into acetic acid.", "citations": [s]},
            "how": {"text": "Its Oxolysis process converts plastic waste into acetic acid.", "citations": [s]},
            "customers": {"text": "It works with chemical companies.", "citations": [m]},
            "money": {"text": "It earns from R&D partnerships with chemical companies.", "citations": [m]}}
    llm = model(full)
    out = B.business_flow(RUN, llm)
    assert len(out["steps"]) == 5 and calls == [] and llm.complete.call_count == 1


class _Searcher:
    available = True

    def __init__(self, text, sources=()):
        self.text, self.sources = text, list(sources)

    def web_answer(self, prompt, **kw):
        return {"text": self.text, "sources": self.sources, "queries": []}


ANSWER = """Radical Dot lists its business model as B2B and sells chemical building blocks to manufacturers. [1]

It was founded on the Moon by astronauts. [2]

**Sources**

[1] https://startups.test/radical-dot
[2] https://gossip.test/story"""


def test_a_searched_sentence_counts_only_when_its_page_holds_it(monkeypatch):
    pages = {"https://startups.test/radical-dot": "Radical Dot. Business model: B2B. Radical Dot sells chemical "
                                                  "building blocks to manufacturers in the plastics industry.",
             "https://gossip.test/story": "Radical Dot is a chemical recycling startup in Munich."}
    monkeypatch.setattr(B, "_read_pages", lambda urls: {u: pages[u] for u in urls})
    found = search_steps("Radical Dot", "", ["money"], _Searcher(ANSWER))
    assert found == [("https://startups.test/radical-dot",
                      "Radical Dot lists its business model as B2B and sells chemical building blocks to manufacturers.")]


def test_an_unreadable_page_counts_only_when_the_provider_cited_it(monkeypatch):
    monkeypatch.setattr(B, "_read_pages", lambda urls: {})
    assert search_steps("Radical Dot", "", ["money"], _Searcher(ANSWER)) == []
    cited = _Searcher(ANSWER, [{"title": "t", "url": "https://startups.test/radical-dot/"}])
    assert [u for u, _ in search_steps("Radical Dot", "", ["money"], cited)] == ["https://startups.test/radical-dot"]
