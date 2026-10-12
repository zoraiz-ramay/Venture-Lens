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


# ----------------------------------------------------------------- search deadline and hedging

def _answers(*plan):
    """ask() that sleeps then answers per call, in order: [(seconds, answer), ...]."""
    import threading
    import time
    calls, lock = [], threading.Lock()

    def ask():
        with lock:
            i = len(calls)
            calls.append(i)
        delay, answer = plan[min(i, len(plan) - 1)]
        time.sleep(delay)
        return answer
    return ask, calls


def test_a_search_past_its_deadline_counts_as_empty(monkeypatch):
    """One slow gateway search held a whole wave for 20s; now it stops at the deadline."""
    import time
    monkeypatch.setattr(web, "GROUNDED_SEARCH_TIMEOUT", 0.3)
    monkeypatch.setattr(web, "GROUNDED_SEARCH_HEDGE", 0.0)
    ask, calls = _answers((2.0, {"pages": [{"url": "https://slow.example"}]}))
    started = time.monotonic()
    assert web._first_answer(ask) is None
    assert time.monotonic() - started < 1.0 and calls == [0]          # no duplicate without hedging

    ask, calls = _answers((0.01, {"pages": [{"url": "https://fast.example"}]}))
    assert web._first_answer(ask)["pages"][0]["url"] == "https://fast.example"


def test_a_hedged_duplicate_wins_over_a_slow_first_request(monkeypatch):
    monkeypatch.setattr(web, "GROUNDED_SEARCH_TIMEOUT", 2.0)
    monkeypatch.setattr(web, "GROUNDED_SEARCH_HEDGE", 0.1)
    ask, calls = _answers((1.5, {"pages": [{"url": "https://slow.example"}]}),
                          (0.05, {"pages": [{"url": "https://second.example"}]}))
    assert web._first_answer(ask)["pages"][0]["url"] == "https://second.example"
    assert calls == [0, 1]


def test_a_search_cut_off_while_reading_pages_is_not_cached(gemini, monkeypatch):
    """Pages still loading at the deadline are dropped, and that partial result is not cached."""
    import time
    monkeypatch.setattr(web, "GROUNDED_FETCH_TIMEOUT", 0.1)

    def fetch(url, timeout=8.0):
        if "slow" in url:
            time.sleep(2.0)
        return PAGE, url
    _, stored = gemini([{"title": "fast", "url": "https://fast.example/a"},
                     {"title": "slow", "url": "https://slow.example/b"}], fetch)
    started = time.monotonic()
    hits = web.ddg_search("Acme Robotics accelerator", 5)
    assert [h["href"] for h in hits] == ["https://fast.example/a"]
    assert time.monotonic() - started < 1.5
    assert stored == {}


# ----------------------------------------------------------------- one search per topic per evaluation

@pytest.mark.parametrize("a, b", [
    ("Acme funding round amount raised investors", "Acme crunchbase founded funding stage pre-seed seed series"),
    ("Acme founders team background", "Acme founder previous company career university"),
    ("Acme number of employees headcount", "Acme acme.com number of employees company size linkedin"),
    ("Acme linkedin employees company size over time", "Acme crunchbase employee count history"),
    ("Acme customers clients case study", "Acme customer case study deployment client announcement"),
    ("Acme partnership collaboration strategic partner", "Acme strategic partner alliance network"),
])
def test_searches_asking_for_the_same_fact_share_a_signature(a, b):
    assert web.search_signature(a, "Acme") == web.search_signature(b, "Acme")


@pytest.mark.parametrize("a, b", [
    ('"Ada Lovelace" Acme founder background LinkedIn', '"Grace Hopper" Acme founder background LinkedIn'),
    ("Acme Siemens partnership OR pilot", "Acme Siemens integration Teamcenter OR Simcenter"),
    ("Acme number of employees headcount", "Acme linkedin employees company size over time"),   # now vs history
    ("Acme funding round raised investors", "robotics startup funding round 2025"),            # company vs market
    ("Acme pricing plans license cost", "Acme API developer documentation SDK"),
    ("Acme partnership collaboration strategic partner", "Acme marketplace listing reseller partner program"),
    # Market searches each ask about a different slice: sharing them cost Connect its market signals.
    ("robotic software market size billion CAGR report", "industrial robotics market size growth leading companies"),
    ("robotics software startup funding 2025", "industrial robot programming startup raised seed series A funding round"),
])
def test_searches_asking_different_things_stay_apart(a, b):
    assert web.search_signature(a, "Acme") != web.search_signature(b, "Acme")


TEAM_PAGE = ("Header. " * 40 + "Acme had 40 employees in 2021 and 120 employees in 2024, LinkedIn shows. "
             + "Filler text. " * 60 + "Acme raised EUR 9M led by NotARealFund in its Series A round. " + "Footer. " * 40)


def test_one_evaluation_pays_for_each_topic_once_but_reads_it_for_each_query(gemini, monkeypatch):
    """Same topic, two wordings: one billed search, and each query's passages are cut for its own
    words — the history query must still see the years."""
    client, _ = gemini([{"title": "p1", "url": "https://acme.example/team"}], lambda url, **kw: (TEAM_PAGE, url))
    fetched = []
    real_read = web._fetch_with_url
    monkeypatch.setattr(web, "_fetch_with_url", lambda url, **kw: fetched.append(url) or real_read(url, **kw))
    token, state = web.share_searches("Acme")
    try:
        now = web.ddg_search("Acme linkedin employees company size over time", 5)
        history = web.ddg_search("Acme number of employees 2021 2022 2023 2024 headcount growth", 5)
        funding = web.ddg_search("Acme funding round raised investors", 5)
    finally:
        web.stop_sharing_searches(token)
    assert len(client.asked) == 2 and state["reused"] == 1            # history reused the first page list
    assert "2021" in history[0]["body"] and "2024" in history[0]["body"]
    assert "NotARealFund" in funding[0]["body"]
    assert fetched.count("https://acme.example/team") == 1             # one fetch per page per evaluation
    web.ddg_search("Acme number of employees 2021 2022 headcount growth", 5)   # outside an evaluation
    assert len(client.asked) == 3


def test_an_empty_shared_search_does_not_stand_in_for_the_next_one(gemini):
    client, _ = gemini([], lambda url, **kw: (PAGE, url))
    token, state = web.share_searches("Acme")
    try:
        assert web.ddg_search("Acme customers clients case study", 5) == []
        web.ddg_search("Acme customer case study deployment", 5)
    finally:
        web.stop_sharing_searches(token)
    assert len(client.asked) == 2 and state["reused"] == 0


def test_after_an_empty_search_the_re_search_is_the_one_later_queries_share(gemini):
    client, _ = gemini([], lambda url, **kw: (PAGE, url))
    token, state = web.share_searches("Acme")
    try:
        web.ddg_search("Acme customers clients case study", 5)
        client.pages = [{"title": "p", "url": "https://acme.example/customers"}]
        web.ddg_search("Acme customer case study deployment", 5)
        web.ddg_search("Acme client announcement case studies", 5)
    finally:
        web.stop_sharing_searches(token)
    assert len(client.asked) == 2 and state["reused"] == 1


class _SlowGateway(_Gemini):
    """The first search answers after its deadline, as one in a burst does."""

    def __init__(self, pages, delay):
        super().__init__(pages)
        self.delay = delay

    def web_answer(self, prompt, **kw):
        import time
        first = not self.asked
        self.asked.append(prompt)
        if first:
            time.sleep(self.delay)
        return {"text": ANSWER, "sources": [], "queries": [], "pages": self.pages}


def test_a_search_that_answers_late_still_serves_its_topic(gemini, monkeypatch):
    """A timed-out search is billed anyway: its late pages must answer the next query on the topic,
    and a query asking while it is still in flight must not pay for a second one."""
    import threading
    import time
    _, _ = gemini([], lambda url, **kw: (TEAM_PAGE, url))
    client = _SlowGateway([{"title": "p1", "url": "https://acme.example/team"}], delay=0.6)
    monkeypatch.setattr(web, "_grounding_client", lambda: client)
    monkeypatch.setattr(web, "GROUNDED_SEARCH_TIMEOUT", 0.3)
    token, state = web.share_searches("Acme")
    try:
        first, waiting = [], []
        t = threading.Thread(target=__import__("contextvars").copy_context().run,
                             args=(lambda: first.extend(web.ddg_search("Acme founders team background", 5)),))
        t.start()
        time.sleep(0.05)
        waiting = web.ddg_search("Acme founders co-founder CEO CTO LinkedIn", 5)   # gives up at its deadline
        t.join()
        time.sleep(0.5)                                                             # the late answer lands
        later = web.ddg_search("Acme founder previous company career university", 5)
    finally:
        web.stop_sharing_searches(token)
    assert first == [] and waiting == []
    assert len(client.asked) == 1 and state["reused"] == 1
    assert later and later[0]["href"] == "https://acme.example/team"


def test_a_late_answer_is_handed_on_once_and_a_failed_one_as_none():
    import time
    got = []
    web_timeout = web.GROUNDED_SEARCH_TIMEOUT
    try:
        web.GROUNDED_SEARCH_TIMEOUT = 0.1
        assert web._first_answer(lambda: time.sleep(0.3) or {"pages": [1]}, got.append) is None
        time.sleep(0.4)
        assert got == [{"pages": [1]}]
        got.clear()

        def boom():
            time.sleep(0.3)
            raise RuntimeError("gateway 500")
        assert web._first_answer(boom, got.append) is None
        time.sleep(0.4)
        assert got == [None]
        got.clear()
        assert web._first_answer(lambda: {"pages": [2]}, got.append) == {"pages": [2]} and got == [{"pages": [2]}]
    finally:
        web.GROUNDED_SEARCH_TIMEOUT = web_timeout
