"""SEARCH_BACKEND=grounded: the model's web search finds the pages; the engine reads the pages.

The grounded answer is prose the model wrote. Every grounding gate asks whether independent text
supports a claim, so that prose must never reach a result's body — otherwise the model would be
checked against itself. These pin that, that the DuckDuckGo path is untouched, and which engine
"auto" picks.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import web  # noqa: E402

ANSWER = "Acme Robotics is a member of the Founders Program and raised EUR 9M from NotARealFund."
PAGE = ("Welcome to our blog. " * 30 + "Acme Robotics joined the EIT Manufacturing accelerator cohort in 2023, "
        "the programme said on Tuesday. " + "Unrelated footer text. " * 40)


class _Gemini:
    available, provider = True, "gemini"

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def web_answer(self, prompt, **kw):
        self.asked.append(prompt)
        return {"text": ANSWER, "sources": [], "queries": [], "pages": self.pages}


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(web, "SEARCH_BACKEND", "gemini")
    stored = {}
    monkeypatch.setattr(web, "_cached", lambda kind, key, meta=None, shared=False: stored.get(key))
    monkeypatch.setattr(web, "_store", lambda kind, key, payload: stored.__setitem__(key, payload))

    def use(pages, fetch):
        client = _Gemini(pages)
        monkeypatch.setattr(web, "_grounding_client", lambda: client)
        monkeypatch.setattr(web, "_resolve_grounding_redirect", lambda url: url.replace("redirect/", "real/"))
        monkeypatch.setattr(web, "_fetch_with_url", fetch)
        return client, stored
    return use


def test_hits_are_read_off_the_page_never_the_answer(gemini):
    gemini([{"title": "eit.example", "url": "https://g.example/redirect/1"}],
           lambda url, **kw: (PAGE, url + "#landed"))
    (hit,) = web.ddg_search("Acme Robotics accelerator cohort", 5)
    assert hit["href"] == "https://g.example/real/1#landed"           # the page, not Google's redirect
    assert "EIT Manufacturing accelerator cohort" in hit["body"]
    assert "Founders Program" not in hit["body"] and "NotARealFund" not in hit["body"]
    # Snippet-sized: a whole page as one result would make "co-occur in one result" trivially true.
    assert len(hit["body"]) <= 2 * web._GROUNDED_WINDOW + 5


def test_a_page_that_cannot_be_read_is_dropped_not_described(gemini):
    gemini([{"title": "a.example", "url": "https://a.example/x"}, {"title": "b.example", "url": "https://b.example/y"}],
           lambda url, **kw: (PAGE, url) if "a.example" in url else ("", ""))
    hits = web.ddg_search("Acme Robotics accelerator", 5)
    assert [h["href"] for h in hits] == ["https://a.example/x"]


def test_nothing_readable_is_not_cached_so_a_failure_does_not_stick(gemini):
    _, stored = gemini([{"title": "a.example", "url": "https://a.example/x"}], lambda url, **kw: ("", ""))
    assert web.ddg_search("Acme Robotics", 5) == [] and stored == {}


def test_the_two_engines_never_share_a_cache_entry(gemini):
    _, stored = gemini([{"title": "a.example", "url": "https://a.example/x"}], lambda url, **kw: (PAGE, url))
    web.ddg_search("Acme Robotics accelerator", 5)
    assert web._cache_key("ddg", "Acme Robotics accelerator", 5) not in stored
    assert web._cache_key("ddg", "gemini", "Acme Robotics accelerator", 5) in stored


def test_only_googles_grounding_redirect_is_followed_off_site(monkeypatch):
    calls = []

    class Resp:
        status_code, headers = 302, {"Location": "https://www.acme.example/about"}

        def close(self):
            pass

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append(url) or Resp())
    monkeypatch.setattr(web, "_is_public_host", lambda host: host != "10.0.0.1")
    google = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    assert web._resolve_grounding_redirect(google) == "https://www.acme.example/about"
    assert web._resolve_grounding_redirect("https://www.acme.example/x") == "https://www.acme.example/x"
    assert web._resolve_grounding_redirect("http://vertexaisearch.cloud.google.com/grounding-api-redirect/a") == ""
    assert web._resolve_grounding_redirect("https://vertexaisearch.cloud.google.com/other/a") == ""
    Resp.headers = {"Location": "http://10.0.0.1/admin"}
    assert web._resolve_grounding_redirect(google) == ""                 # an internal target is refused
    assert calls == [google, google]


def test_ddg_engine_never_asks_the_model(monkeypatch):
    monkeypatch.setattr(web, "SEARCH_BACKEND", "ddg")
    monkeypatch.setattr(web, "_grounding_client", lambda: pytest.fail("the model was asked to search"))
    monkeypatch.setattr(web, "_cached", lambda kind, key, meta=None, shared=False: [{"href": "cached"}])
    assert web.ddg_search("anything", 5) == [{"href": "cached"}]


class _Client:
    def __init__(self, provider, available=True):
        self.provider, self.available = provider, available


@pytest.mark.parametrize("client, engine", [
    (_Client("openai"), "grounded"),              # the Siemens gateway: DuckDuckGo is unreachable there
    (_Client("gemini"), "ddg"),                   # a laptop with a Gemini key keeps DuckDuckGo
    (_Client("openai", available=False), "ddg"),  # no model configured
])
def test_auto_searches_through_the_gateway_only(monkeypatch, client, engine):
    monkeypatch.setattr(web, "SEARCH_BACKEND", "auto")
    monkeypatch.setattr(web, "_grounding_client", lambda: client)
    assert web.search_backend() == engine


def test_the_gateway_lists_every_searched_page_not_only_the_cited_ones(monkeypatch):
    """Under SEARCH_BACKEND=grounded on the gateway, the pages come from the web_search call's
    sources, and a listing-only search (thinking_budget=0) asks gpt-6 not to reason."""
    from types import SimpleNamespace as NS
    from core import llm as llm_mod
    seen = {}

    def create(**kw):
        seen.update(kw)
        call = NS(type="web_search_call", action=NS(sources=[NS(url="https://a.example/x"), NS(url="https://b.example/y"),
                                                             NS(url="https://a.example/x")]))
        msg = NS(type="message", content=[NS(type="output_text", text="a.example/x", annotations=[])])
        return NS(status="completed", output=[call, msg], output_text="", usage=None)

    client = NS(provider="openai", model="gpt-6-luna", _client=NS(responses=NS(create=create)))
    out = llm_mod._openai_web_search(client, "Search the web for: acme", "", 256, thinking_budget=0)
    assert [p["url"] for p in out["pages"]] == ["https://a.example/x", "https://b.example/y"]
    assert seen["include"] == ["web_search_call.action.sources"]
    assert seen["reasoning"] == {"effort": "none"}

    client.model = "gpt-5-mini"
    llm_mod._openai_web_search(client, "Search the web for: acme", "", 256, thinking_budget=0)
    assert seen["reasoning"] == {"effort": "low"}             # the gateway refuses web_search at "minimal"
