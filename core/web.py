"""Free web enrichment via DuckDuckGo (no paid data APIs)."""
from __future__ import annotations

import contextvars
import functools
import hashlib
import os
import re
import threading
import time

from opentelemetry import trace

# ddgs speaks HTTP through its own Rust client, which no instrumentation sees, so without this
# span a trace shows the completions around a search wave and a silent gap where the wave was.
_tracer = trace.get_tracer(__name__)

# ------------------------------------------------------------------------------- result cache
# Searches and site fetches are the slow, NON-REPRODUCIBLE part of an evaluation: DuckDuckGo
# returns a different mix of results run to run, so two evaluations of the same startup used to
# disagree. Caching both makes a re-run fast and byte-reproducible.
#
# The backend is injected rather than imported: nothing in core/ depends on api/, and keeping it
# that way lets the engine run uncached from tests, scripts and Streamlit. api/main.py installs
# the SQLite-backed store at startup via install_cache().
_cache_get = None
_cache_put = None
# Optional: (kind, key) -> (payload, created_at). When the backend supplies it, a cached result
# carries the timestamp of the search that produced it, so evidence replayed on a re-run is dated
# honestly instead of inheriting the re-run's own clock.
_cache_entry = None

# Set False for a run that must hit the network — "Re-evaluate" has to re-search, otherwise it
# would just replay week-old results. A ContextVar rather than a parameter so the flag follows
# the request through _ddg_many's worker threads without touching every signature in between.
_cache_private: contextvars.ContextVar = contextvars.ContextVar("web_cache_private", default=False)
_cache_enabled: contextvars.ContextVar = contextvars.ContextVar("web_cache_enabled", default=True)


def install_cache(getter, putter, entry_getter=None) -> None:
    """Wire up the cache backend: ``getter(kind, key)`` and ``putter(kind, key, payload)``.

    ``entry_getter(kind, key) -> (payload, created_at) | None`` is optional. Supplying it is what
    makes evidence age truthful; without it everything behaves exactly as before.
    """
    global _cache_get, _cache_put, _cache_entry
    _cache_get, _cache_put, _cache_entry = getter, putter, entry_getter


def set_cache_private(private: bool):
    """Disable shared cache reads AND writes for licensed, user-specific research."""
    return _cache_private.set(private)


def reset_cache_private(token):
    _cache_private.reset(token)


def set_cache_enabled(enabled: bool):
    """Enable/disable cache reads for the current context; returns the ContextVar token."""
    return _cache_enabled.set(bool(enabled))


def reset_cache_enabled(token) -> None:
    _cache_enabled.reset(token)


# ---------------------------------------------------------------------------- run timeline
# Where an evaluation's wall time went. The token log records how long each model call took but
# not when it started, and search waves not at all — so a slow run could not be read as a chain:
# the profile branch's ~100s turned out to be mostly search waves queued behind each other, which
# no per-call duration shows. scripts/waterfall.py draws a stored run from these events.
# Lives here, not in llm.py, because llm imports web: both record into the same timeline.
_timeline: contextvars.ContextVar = contextvars.ContextVar("run_timeline", default=None)


def collect_timeline():
    """Start a run's timeline. Returns (token, timeline) — timeline["events"] fills as it runs."""
    timeline = {"t0": time.time(), "events": []}
    return _timeline.set(timeline), timeline


def stop_collecting_timeline(token) -> None:
    _timeline.reset(token)


def run_offset_ms(t: float):
    """Milliseconds from the run's start to ``t``, or None outside a run."""
    timeline = _timeline.get()
    return None if timeline is None else int((t - timeline["t0"]) * 1000)


# ---------------------------------------------------------------------------- shared searches
# The search waves were written for DuckDuckGo, where any one query often came back empty, so each
# stage asks for the same facts in its own words: one evaluation sent four funding searches, five
# headcount searches and four founder searches across enrich, profile and trend. On the gateway a
# search does not fail that way, and each costs ~14k input tokens. Within one evaluation, a search
# on the same topic about the company reuses the page list the first one found, and reads it for its
# own words (_search_pages). Measured on Wandelbots: 54 billed searches before, 36 with market
# searches shared too (which cost Connect its market signals), fewer than 54 with company ones only.
#
# Queries naming something in quotes (a founder, a programme list) and Siemens queries (each lists
# different products) are only shared when worded identically. A shared search that found nothing
# does not stand in for anyone: the later query runs its own.
_shared: contextvars.ContextVar = contextvars.ContextVar("shared_searches", default=None)
_TOPIC = lambda p: re.compile(p, re.I)
# The first that matches names the topic. Only searches about the company itself are shared: market
# searches (no company name in them) each ask about a different slice of the market, and sharing
# them dropped Connect's dated market signals from 2 to 0 on the same run.
_SEARCH_TOPICS = (
    ("funding", _TOPIC(r"\b(fund(ing|ed)|raised?|investors?|series [a-e]|pre-seed|seed|venture capital|funding round)\b")),
    ("headcount_history", _TOPIC(r"\b(employees?|headcount|employee count)\b.*\b(history|over time|growth|20\d\d)\b")),
    ("headcount", _TOPIC(r"\b(employees?|headcount|employee count|company size)\b")),
    ("advisors", _TOPIC(r"\badvis(ors?|ory)\b")),
    ("founders", _TOPIC(r"\b(co-?founders?|founders?)\b")),
    ("customers", _TOPIC(r"\b(customers?|clients?|case stud(y|ies))\b")),
    ("programmes", _TOPIC(r"\b(accelerators?|incubators?|cohort|alumni|backed by)\b")),
    ("founded_hq", _TOPIC(r"\b(headquarters|founded|established|location)\b")),
    ("competitors", _TOPIC(r"\b(competitors?|alternatives?)\b")),
)


# SEARCH_SHARING=0 turns it off: every query is searched, as before.
SEARCH_SHARING = os.getenv("SEARCH_SHARING", "1").strip() != "0"


def share_searches(company: str = ""):
    """Start sharing searches for one evaluation. Returns (token, state); state["reused"] counts."""
    state = {"company": str(company or "").strip(), "searches": {}, "reused": 0, "lock": threading.Lock()}
    return _shared.set(state if SEARCH_SHARING else None), state


def stop_sharing_searches(token) -> None:
    _shared.reset(token)


def set_search_subject(company: str) -> None:
    """The resolved company name: what tells a company search from a market search."""
    state = _shared.get()
    if state is not None and str(company or "").strip():
        state["company"] = str(company).strip()


def search_signature(query: str, company: str = "") -> tuple:
    """What a search asks for: (topic, quoted names) for a company search, else ("exact", text)."""
    q = str(query or "")
    norm = " ".join(re.findall(r"[a-z0-9]+", q.casefold()))
    if "siemens" in norm or not company or company.casefold() not in q.casefold():
        return ("exact", norm)
    quoted = frozenset(p.casefold() for p in re.findall(r'"([^"]+)"', q))
    for topic, pattern in _SEARCH_TOPICS:
        if pattern.search(q):
            return (topic, quoted)
    return ("exact", norm)


def _search_pages(query: str, ask) -> tuple[list, bool]:
    """(pages the search found, whether they came from an earlier search in this evaluation).

    Only the page list is shared — that is what a search is billed for. Each query still reads the
    pages and cuts its own passages around its own words (_passages): sharing the finished hits
    handed the employee-history query passages chosen for "number of employees", with no years in
    them, and the run lost its headcount history and investors."""
    import concurrent.futures as cf
    state = _shared.get()
    if state is None:
        return _pages_of(_first_answer(ask)), False
    sig = search_signature(query, state["company"])
    with state["lock"]:
        future = state["searches"].get(sig)
        owner = future is None
        if owner:
            future = state["searches"][sig] = cf.Future()
    if owner:
        pages = []
        try:
            pages = _pages_of(_first_answer(ask))
        finally:
            future.set_result(pages)
        return pages, False
    try:
        pages = future.result(timeout=GROUNDED_SEARCH_TIMEOUT + 5)
    except cf.TimeoutError:
        pages = []
    if not pages:            # an empty search does not stand in for anyone: search for this query
        return _pages_of(_first_answer(ask)), False
    with state["lock"]:
        state["reused"] += 1
    return pages, True


def _pages_of(answer) -> list:
    return [p for p in (answer or {}).get("pages", []) if p.get("url")]


def _read_page(url: str) -> tuple[str, str]:
    """_fetch_with_url, once per URL per evaluation: queries sharing a page list share the fetch."""
    import concurrent.futures as cf
    state = _shared.get()
    if state is None:
        return _fetch_with_url(url, timeout=GROUNDED_FETCH_TIMEOUT)
    with state["lock"]:
        future = state.setdefault("pages", {}).get(url)
        owner = future is None
        if owner:
            future = state["pages"][url] = cf.Future()
    if owner:
        got = ("", "")
        try:
            got = _fetch_with_url(url, timeout=GROUNDED_FETCH_TIMEOUT)
        finally:
            future.set_result(got)
        return got
    try:
        return future.result(timeout=GROUNDED_FETCH_TIMEOUT + 1.0)
    except cf.TimeoutError:
        return "", ""


def _current_stage() -> str:
    try:
        from . import llm   # llm imports web at module level, so this import waits until call time
    except ImportError:     # web.py loaded on its own, outside the package (the dependency-light tests)
        return ""
    return llm._stage.get() or ""


def mark(kind: str, started: float, **extra) -> None:
    """Record one span of a run that began at ``started`` and ends now. No-op outside a run."""
    timeline = _timeline.get()
    if timeline is None:
        return
    timeline["events"].append({"kind": kind, "stage": extra.pop("stage", None) or _current_stage(),
                               "start_ms": int((started - timeline["t0"]) * 1000),
                               "duration_ms": int((time.time() - started) * 1000), **extra})


def _cache_key(*parts) -> str:
    return hashlib.sha256("\x00".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _cached(kind: str, key: str, meta: dict | None = None, shared: bool = False):
    """Cached payload, or None. ``meta`` receives ``cached_at`` when the backend can supply it.

    ``shared`` is for entries that are not evidence about the company being evaluated (whether a
    Siemens catalog tool exists): a refresh has nothing of the company's to renew in them, and a
    private run has nothing of its own to protect, so neither flag applies."""
    if not shared and (_cache_private.get() or not _cache_enabled.get()):
        return None
    if _cache_entry is not None:
        try:
            entry = _cache_entry(kind, key)
        except Exception:
            return None
        if entry is None:
            return None
        payload, created_at = entry
        if isinstance(meta, dict) and created_at:
            meta["cached_at"] = created_at
        return payload
    if _cache_get is None:
        return None
    try:
        return _cache_get(kind, key)
    except Exception:
        return None


def _store(kind: str, key: str, payload, shared: bool = False) -> None:
    # Written even when reads are disabled, so a forced refresh repopulates the cache.
    if (_cache_private.get() and not shared) or _cache_put is None:
        return
    try:
        _cache_put(kind, key, payload)
    except Exception:
        pass


# Which engine answers ddg_search. "ddg" scrapes DuckDuckGo; "grounded" (or "gemini") has the
# model's own web search — Gemini's Google Search, or the gateway's web_search tool — find the
# pages and then reads the pages itself (_grounded_search). Every caller gets the same
# {title, href, body} hits either way, so no grounding gate changes. "auto", the default, is
# "grounded" on the Siemens gateway and "ddg" elsewhere: from the deployed network DuckDuckGo
# does not answer at all (every request timed out at ~6.5s, three attempts each, and the
# enrichment wave abandoned a third of its queries), while a gateway search lists ~11 pages in
# ~3.5s. A laptop reaches DuckDuckGo, so local runs keep it.
SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "auto").strip().lower()


def search_backend() -> str:
    """The engine ddg_search uses: "grounded" or "ddg"."""
    if SEARCH_BACKEND in ("grounded", "gemini"):
        return "grounded"
    if SEARCH_BACKEND == "ddg":
        return "ddg"
    llm = _grounding_client()
    return "grounded" if llm.available and llm.provider == "openai" else "ddg"


@_tracer.start_as_current_span("web.search")
def ddg_search(query: str, max_results: int = 5,
               attempts: int = 3, base_delay: float = 0.6,
               meta: dict | None = None) -> list[dict]:
    """Single DuckDuckGo text search with light retry + exponential backoff.

    DuckDuckGo rate-limits bursts of free queries and then recovers within a second or
    two, so an empty or failed result is retried a few times (backoff + jitter) before
    giving up. This turns transient throttling — which otherwise surfaces as a false
    'No match ... on the web' for a real but small company — into a brief extra wait.
    Returns [] only if every attempt fails, so callers still degrade gracefully.

    Results are cached (see install_cache): re-running the same query inside the TTL replays
    the same hits, which is what makes a re-evaluation fast AND reproducible."""
    import random
    if search_backend() == "grounded":
        return _grounded_search(query, max_results, meta)
    key = _cache_key("ddg", query, max_results)
    hit = _cached("ddg", key, meta)
    span = trace.get_current_span()
    span.set_attribute("web.search.query", query)
    span.set_attribute("web.search.cache_hit", hit is not None)
    if hit is not None:
        return hit
    try:
        from ddgs import DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS  # older package name
        except Exception:
            return []
    hits: list[dict] = []
    failed = False
    for i in range(max(1, attempts)):
        span.set_attribute("web.search.attempts", i + 1)   # >1 means DuckDuckGo throttled us
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(query, max_results=max_results))
            failed = False
            if hits:
                _store("ddg", key, hits)
                return hits
        except Exception:
            hits, failed = [], True
        if i < attempts - 1:                     # backoff before the next try
            time.sleep(base_delay * (2 ** i) + random.uniform(0, 0.3))
    # A genuinely empty result is worth caching; a run that only ever threw is not — caching
    # that would pin a transient throttle in place for the whole TTL.
    if not failed:
        _store("ddg", key, hits)
    return hits


# Searches actually on the wire, across every wave in this process. Each wave used to cap only
# itself at 10, which held while the waves of one evaluation ran one after another. They now
# overlap — enrichment, profile and trend all start the moment the company is resolved — and
# several reviewers' evaluations overlap with each other, so per-wave caps added up to bursts of
# 40+ that the search engines answer with 429s, i.e. silently empty results. Past this limit a
# search waits for a slot instead.
DDG_CONCURRENCY = max(1, int(os.getenv("DDG_CONCURRENCY", "10")))
_ddg_slots = threading.BoundedSemaphore(DDG_CONCURRENCY)
# The grounded backend is not a scraper the engine throttles, so it does not queue behind the
# DuckDuckGo limit; the model quota (llm's rate gate, LLM_RPM) paces its calls instead. One
# evaluation opens ~47 searches at once, so at 20 its own waves queued behind each other for 5s.
# The gateway answered 40 concurrent searches with no 429 (limit 15,000 requests/min).
SEARCH_CONCURRENCY = max(1, int(os.getenv("SEARCH_CONCURRENCY", "64")))
_search_slots = threading.BoundedSemaphore(SEARCH_CONCURRENCY)


# ----------------------------------------------------------------- grounded search backend
# Gemini's Google Search grounding answers in prose and lists the pages it searched — no snippets.
# Its answer is never used as a result: every grounding gate in the engine (_program_grounded's
# "name and company in ONE result", _ground_customers' phrase window, the landscape's name check,
# _verbatim) asks whether independent text supports a claim, and with the model's own answer as
# that text the model would be checked against itself. So the answer is thrown away, each page
# is fetched, and a result's body is read off the page.
_GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_GROUNDING_REDIRECT_PATH = "/grounding-api-redirect/"
_GROUNDED_WINDOW = 200          # characters per passage
_GROUNDED_PASSAGES = 2          # passages per page
_grounded_llm = None
_grounded_llm_lock = threading.Lock()


def _resolve_grounding_redirect(url: str, timeout: float = 5.0) -> str:
    """The page a grounding link points to, or ''. Only Google's own redirect is followed
    off-site: _fetch_with_url refuses cross-site hops, and that refusal is the SSRF guarantee
    every other fetch keeps. The target must itself be a public http(s) host."""
    from urllib.parse import urlparse, urljoin
    parsed = urlparse(url)
    if parsed.hostname != _GROUNDING_REDIRECT_HOST:
        return url
    if parsed.scheme != "https" or not parsed.path.startswith(_GROUNDING_REDIRECT_PATH):
        return ""
    try:
        import requests
        resp = requests.get(url, timeout=timeout, allow_redirects=False, headers={"User-Agent": _UA})
        resp.close()
    except Exception:
        return ""
    target = urljoin(url, resp.headers.get("Location", "") or "")
    t = urlparse(target)
    if resp.status_code not in (301, 302, 303, 307, 308) or t.scheme not in ("http", "https") \
            or not t.hostname or not _is_public_host(t.hostname):
        return ""
    return t.geturl()


def _passages(text: str, query: str) -> str:
    """The page's passages that share the most words with the query, in page order.

    Snippet-sized on purpose. A whole page as one "result" would make "the program and the company
    co-occur in a single result" true of any long page that mentions both anywhere, which is the
    looseness every grounding gate exists to refuse."""
    import re
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
    if not text or not words:
        return ""
    step = _GROUNDED_WINDOW // 2
    scored = []
    for start in range(0, max(1, len(text) - step), step):
        chunk = text[start:start + _GROUNDED_WINDOW]
        hits = len(words & set(re.findall(r"[a-z0-9]+", chunk.lower())))
        if hits:
            scored.append((hits, start))
    picked: list[int] = []
    for _, start in sorted(scored, key=lambda s: (-s[0], s[1])):
        if all(abs(start - p) >= _GROUNDED_WINDOW for p in picked):
            picked.append(start)
        if len(picked) == _GROUNDED_PASSAGES:
            break
    return " … ".join(text[p:p + _GROUNDED_WINDOW].strip() for p in sorted(picked))


def _grounding_client():
    global _grounded_llm
    with _grounded_llm_lock:
        if _grounded_llm is None:
            from .llm import LLMClient
            _grounded_llm = LLMClient()
        return _grounded_llm


def _grounded_search(query: str, max_results: int = 5, meta: dict | None = None) -> list[dict]:
    """ddg_search's contract — [{title, href, body}] — answered by the model's own web search.

    A page that cannot be fetched is dropped rather than described from the answer: no page text,
    no evidence. Cached like a DuckDuckGo search (own key, so switching engines never replays the
    other's results); a search that found nothing readable is not cached, so a transient failure
    does not stick for the whole TTL."""
    import concurrent.futures as cf
    key = _cache_key("ddg", "gemini", query, max_results)
    hit = _cached("ddg", key, meta)
    if hit is not None:
        return hit
    llm = _grounding_client()
    if not llm.available or llm.provider not in ("gemini", "openai"):
        return []
    found, shared = _search_pages(query, lambda: llm.web_answer(
        f"Search the web for: {query}\nList the pages you found, one per line.",
        max_tokens=256, thinking_budget=0))
    # A query answered from another's page list reads further down it: the first pages were picked
    # for the other wording, and reading a page costs no tokens.
    pages = found[:max_results * 2 if shared else max_results]
    if not pages:
        return []

    def read(page):
        url = _resolve_grounding_redirect(page["url"])
        text, landed = _read_page(url) if url else ("", "")
        body = _passages(text, query)
        return {"title": page.get("title", ""), "href": landed or url, "body": body} if body else None

    # A page that has not arrived by the deadline is dropped like one that failed: a wave waits for
    # its slowest search, so one slow site held every other company field behind it.
    pool = cf.ThreadPoolExecutor(max_workers=len(pages), thread_name_prefix="grounded-read")
    try:
        futures = [pool.submit(contextvars.copy_context().run, read, p) for p in pages]
        done, _ = cf.wait(futures, timeout=GROUNDED_FETCH_TIMEOUT + 1.0)
        hits = [h for f in futures if f in done and not f.exception() and (h := f.result())][:max_results]
        complete = len(done) == len(futures)
    finally:
        pool.shutdown(wait=False)
    # Only a search whose every page settled is cached: a cut-off one must not stand in for the
    # full result for the whole TTL.
    if hits and complete:
        _store("ddg", key, hits)
    return hits


# How long a grounded search may take. Gateway searches measured 3.6s median and 5.4s p90, but
# one in ~20 took 10-20s, and a wave waits for its slowest: the profile's first wave took 20s for
# three queries. Past GROUNDED_SEARCH_TIMEOUT the search counts as empty. GROUNDED_SEARCH_HEDGE
# (off by default) sends one duplicate after that many seconds and takes whichever answers first:
# at 4s it duplicated ~40% of searches, for ~50% more tokens and no measurable gain in two runs.
GROUNDED_SEARCH_HEDGE = float(os.getenv("GROUNDED_SEARCH_HEDGE", "0"))
GROUNDED_SEARCH_TIMEOUT = float(os.getenv("GROUNDED_SEARCH_TIMEOUT", "7"))
GROUNDED_FETCH_TIMEOUT = float(os.getenv("GROUNDED_FETCH_TIMEOUT", "3"))


def _first_answer(ask):
    """``ask()`` within GROUNDED_SEARCH_TIMEOUT, else None; raced by one duplicate when hedging.

    A request past the deadline is not cancelled (the client cannot): it finishes in the background
    and its tokens are still recorded, since they were billed."""
    import concurrent.futures as cf
    deadline = time.monotonic() + GROUNDED_SEARCH_TIMEOUT
    hedge = GROUNDED_SEARCH_HEDGE if 0 < GROUNDED_SEARCH_HEDGE < GROUNDED_SEARCH_TIMEOUT else 0.0
    pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="grounded-search")
    try:
        futures = [pool.submit(contextvars.copy_context().run, ask)]
        if hedge:
            done, _ = cf.wait(futures, timeout=hedge)
            if done and not futures[0].exception() and futures[0].result():
                return futures[0].result()
            futures.append(pool.submit(contextvars.copy_context().run, ask))
        pending = [f for f in futures if not f.done()]
        while pending:
            done, _ = cf.wait(pending, timeout=max(0.0, deadline - time.monotonic()),
                              return_when=cf.FIRST_COMPLETED)
            if not done:
                return None
            for f in done:
                if not f.exception() and f.result():
                    return f.result()
            pending = [f for f in pending if f not in done]
        first = futures[0]
        return first.result() if first.done() and not first.exception() else None
    finally:
        pool.shutdown(wait=False)


def _ddg_many(queries: dict, max_results: int = 4, max_workers: int = 10,
              overall_timeout: float = 40.0, stats: dict | None = None) -> dict:
    """Run several DuckDuckGo searches concurrently and return {key: hits}.

    Web search is network-bound, so issuing the independent enrichment queries in parallel
    (instead of one after another) sharply reduces total wait time. A hard ``overall_timeout``
    bounds the whole wave: if DuckDuckGo is throttling (it rate-limits free use and the client
    retries with backoff), any searches that haven't returned by the deadline are abandoned and
    their key maps to an empty list, so an evaluation degrades to partial/offline results in a
    few seconds instead of hanging for minutes.

    The budget must scale with the wave: abandoning a query is indistinguishable downstream
    from "the web knows nothing about this company", so too tight a deadline silently turns
    throttling into missing profile fields (parent group, programs, funding) that reappear on
    the next run. The defaults leave headroom for the ~12-query enrichment wave plus its
    per-customer verification queries, each of which may internally retry with backoff.

    Pass ``stats`` to receive observability for exactly that failure mode: it is populated with
    ``requested`` / ``returned`` / ``empty`` / ``timed_out``, where ``timed_out`` counts queries
    still in flight at the deadline (silently dropped) as distinct from those that genuinely
    came back with no hits.

    ``stats`` also carries ``cached`` (how many keys were replayed rather than searched) and
    ``cached_at`` (``{key: iso8601}`` for those that were). A run that replays week-old results is
    not the same evidence as a run that searched just now, and the Facts built from it should say
    so rather than inheriting this run's clock.

    Uses daemon threads (capped by a semaphore) so abandoned in-flight searches can never block
    process shutdown or pile up across evaluations.

    The deadline runs from the moment the wave's first search starts, not from the call: time
    spent queued behind other waves for a process-wide slot (``_ddg_slots``) is not throttling,
    and charging it to the wave would abandon queries that never got to run. The queue wait is
    itself bounded by one more ``overall_timeout``.
    """
    called = time.time()
    out: dict = {k: [] for k in (queries or {})}
    items = [(k, q) for k, q in (queries or {}).items() if str(q).strip()]
    if isinstance(stats, dict):
        stats.update({"requested": len(items), "returned": 0, "empty": 0,
                      "timed_out": len(items), "cached": 0, "cached_at": {}})
    if not items:
        return out
    sem = threading.Semaphore(max(1, min(max_workers, len(items))))
    lock = threading.Lock()
    remaining = {"n": len(items)}
    finished: set = set()
    cached_at: dict = {}
    done = threading.Event()
    started = threading.Event()
    first_search: list = []

    def worker(key: str, query: str):
        meta: dict = {}
        with sem:
            try:
                # A cached result needs no slot: replaying it must not queue behind live searches.
                ddg = search_backend() == "ddg"
                hits = (None if not ddg
                        else _cached("ddg", _cache_key("ddg", query, max_results), meta))
                if hits is None:
                    with (_ddg_slots if ddg else _search_slots):
                        if not first_search:
                            first_search.append(time.time())
                        started.set()
                        hits = ddg_search(query, max_results, meta=meta)
            except Exception:
                hits = []
            started.set()
        with lock:
            out[key] = hits
            if meta.get("cached_at"):
                cached_at[key] = meta["cached_at"]
            finished.add(key)
            remaining["n"] -= 1
            if remaining["n"] == 0:
                done.set()

    for k, q in items:
        # A new thread starts with an EMPTY context, so the cache-bypass ContextVar would fall
        # back to its default and a forced refresh would quietly read from the cache anyway.
        # Each worker therefore runs inside its own copy of the caller's context (a Context can
        # only be entered once, so the copy has to be per-thread).
        threading.Thread(target=contextvars.copy_context().run,
                         args=(worker, k, q), daemon=True).start()
    started.wait(timeout=overall_timeout)
    done.wait(timeout=overall_timeout)   # returns early once all finish, else at the deadline
    with lock:
        settled, replayed = set(finished), len(cached_at)
    # queued_ms is the wait for a process-wide search slot: other waves' searches, not this one's.
    mark("search_wave", called, queries=len(items), cached=replayed, timed_out=len(items) - len(settled),
         queued_ms=int(((first_search[0] if first_search else called) - called) * 1000))
    if isinstance(stats, dict):
        # Snapshot under the lock: workers may still be running past the deadline.
        with lock:
            settled = set(finished)
            replayed = dict(cached_at)
        stats["returned"] = len(settled)
        stats["timed_out"] = len(items) - len(settled)
        stats["empty"] = sum(1 for k in settled if not out.get(k))
        stats["cached"] = len(replayed)
        stats["cached_at"] = replayed
    return out


# --------------------------------------------------------------------------- direct site fetch
# DuckDuckGo indexes pages slowly and skips a lot of a company's own site, so ecosystem /
# partnership / customer facts that live only on the startup's website (e.g. an /partners or
# /ecosystem page) are invisible to search. A direct, SSRF-guarded HTTP fetch of the company's
# OWN domain closes that recall gap without any paid data API.

_UA = ("Mozilla/5.0 (compatible; StartupEvalBot/1.0; +https://siemens.com) "
       "AppleWebKit/537.36")
_MAX_BYTES = 800_000            # cap the download so a huge page can't exhaust memory
# The second group carries commercial posture: whether the offering can actually be bought, how it
# deploys, whether it exposes an API, and what it is certified against. Those are the Xcelerator
# Marketplace's own onboarding requirements, so they decide Connect eligibility — and a startup
# publishes them on its own site far more reliably than the open web indexes them. These are fetched
# concurrently alongside the rest and deduped by content fingerprint, so a site that 404s or
# soft-redirects most of them costs almost nothing.
_FETCH_PATHS = ("", "/en", "/about", "/about-us", "/company", "/partners",
                "/partners-and-supporters", "/supporters", "/ecosystem",
                "/customers", "/team",
                "/pricing", "/security", "/trust", "/docs", "/developers",
                "/api", "/integrations", "/product")
_MAX_REDIRECTS = 2              # same-host hops allowed per fetch (see _same_site)
_TAG_RE = None                 # compiled lazily in _strip_html
_ATTR_RE = None                # ditto — alt/title text rescued before tags are dropped


def _registrable(host: str) -> str:
    """Best-effort registrable domain: the last two labels, lowercased.

    Deliberately naive (no public-suffix list, so 'a.co.uk' -> 'co.uk'). It is only ever used
    to decide whether a redirect stays on the SAME site, and the target is independently
    re-checked by ``_is_public_host`` before any request, so the worst case of an over-broad
    match is fetching another public page on a neighbouring domain — never an internal one."""
    parts = [p for p in str(host or "").lower().strip(".").split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else ".".join(parts)


def _same_site(a: str, b: str) -> bool:
    """True when two hosts belong to the same site (exact, www-, or shared registrable domain)."""
    a, b = str(a or "").lower(), str(b or "").lower()
    if not a or not b:
        return False
    if a == b:
        return True
    return _registrable(a) == _registrable(b)


def _is_public_host(host: str) -> bool:
    """SSRF guard: resolve the host and reject loopback/private/link-local/reserved IPs.

    Only public, internet-routable addresses are allowed, so a crafted company domain
    (or a redirect) can never make the server fetch an internal metadata endpoint,
    localhost, or a RFC1918 address."""
    import socket
    import ipaddress
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _strip_html(html: str) -> str:
    """Cheap HTML -> visible text: drop script/style blocks and tags, unescape entities.

    ``alt`` / ``title`` attribute text is lifted into the output BEFORE tags are removed.
    Stripping tags wholesale silently discards it, and on logo-wall sections that attribute
    is the ONLY textual carrier of the fact: phena.tech publishes its NVIDIA Inception and
    Microsoft for Startups memberships purely as <img alt="..."> badges, so the page yielded
    text with zero program names and the ecosystem section came back empty. Order matters —
    the script/style blocks are removed first so an alt= inside inline JS is never harvested.
    """
    import re
    import html as _htmlmod
    global _TAG_RE, _ATTR_RE
    if _TAG_RE is None:
        _TAG_RE = re.compile(r"<[^>]+>")
    if _ATTR_RE is None:
        _ATTR_RE = re.compile(
            r"""(?is)<[^>]*?\b(?:alt|title)\s*=\s*(?:"([^"]*)"|'([^']*)')[^>]*>""")
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = _ATTR_RE.sub(lambda m: f" {m.group(1) or m.group(2) or ''} ", text)
    text = _TAG_RE.sub(" ", text)
    text = _htmlmod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_with_url(url: str, timeout: float = 8.0,
                    max_bytes: int = _MAX_BYTES) -> tuple[str, str]:
    """Fetch a URL and return ``(visible_text, resolved_url)``; ``('', '')`` on any failure.

    Same contract as :func:`fetch_url` (which wraps this), but it also hands back the URL the
    request actually landed on after redirects. ``fetch_site_text`` needs that to notice a
    site whose root redirects into a locale prefix (phena.tech -> /en) and follow the rest of
    its paths there instead of requesting bare paths the SPA answers with its homepage.

    Hardened against SSRF: only http/https, the resolved host must be public
    (``_is_public_host``), the download is size-capped, and any error degrades to '' so a
    bad site never breaks enrichment.

    Redirects are followed MANUALLY (requests' own following stays disabled) for at most
    ``_MAX_REDIRECTS`` hops, and only when the target stays on the same site (``_same_site``)
    AND independently re-passes the public-host check. Refusing 3xx outright — as this did
    originally — is not a safe default but a silent recall bug: a site whose root redirects
    to a locale prefix (phena.tech -> /en) returns '' for EVERY path, so the ecosystem and
    partner pages this fetcher exists to read are never seen. Re-validating each hop keeps
    the SSRF guarantee: an off-site or internal redirect target is still refused."""
    from urllib.parse import urlparse, urljoin
    try:
        import requests
    except Exception:
        return "", ""
    if not url:
        return "", ""
    parsed = urlparse(url if "//" in url else "https://" + url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "", ""
    if not _is_public_host(parsed.hostname):
        return "", ""

    current = parsed.geturl()
    origin_host = parsed.hostname
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            resp = requests.get(
                current, timeout=timeout, allow_redirects=False, stream=True,
                headers={"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"},
            )
        except Exception:
            return "", ""
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                target = urljoin(current, resp.headers.get("Location", "") or "")
                t = urlparse(target)
                if (t.scheme not in ("http", "https") or not t.hostname
                        or not _same_site(origin_host, t.hostname)
                        or not _is_public_host(t.hostname)):
                    return "", ""
                current = t.geturl()
                continue                      # next hop (bounded by the loop)
            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code != 200 or ("html" not in ctype and "text" not in ctype):
                return "", ""
            chunks, total = [], 0
            for chunk in resp.iter_content(chunk_size=16_384, decode_unicode=False):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
            raw = b"".join(chunks)
        except Exception:
            return "", ""
        finally:
            resp.close()
        try:
            html = raw.decode(resp.encoding or "utf-8", errors="ignore")
        except Exception:
            html = raw.decode("utf-8", errors="ignore")
        return _strip_html(html), current
    return "", ""                              # redirect budget exhausted


def _fetch_cached(url: str, timeout: float = 8.0,
                  max_bytes: int = _MAX_BYTES) -> tuple[str, str]:
    """:func:`_fetch_with_url` with the result cache in front of it.

    Caches the resolved URL alongside the text, because fetch_site_text derives the site's
    locale prefix from it — replaying only the text would lose the prefix and send the cached
    run down a different set of paths than the live one."""
    key = _cache_key("site", url, max_bytes)
    hit = _cached("site", key)
    if isinstance(hit, list) and len(hit) == 2:
        return hit[0], hit[1]
    text, resolved = _fetch_with_url(url, timeout=timeout, max_bytes=max_bytes)
    # Only successful fetches are cached: a timeout or a transient 5xx must not pin an empty
    # page in place for the whole TTL.
    if text:
        _store("site", key, [text, resolved])
    return text, resolved


def fetch_url(url: str, timeout: float = 8.0, max_bytes: int = _MAX_BYTES) -> str:
    """Fetch a single URL and return its visible text, or '' on any failure.

    Thin text-only wrapper over :func:`_fetch_with_url` — see that function for the SSRF and
    redirect guarantees."""
    return _fetch_with_url(url, timeout=timeout, max_bytes=max_bytes)[0]


def fetch_site_text(domain: str, paths: tuple = _FETCH_PATHS,
                    per_timeout: float = 6.0, max_chars: int = 12_000) -> dict:
    """Fetch a handful of a company's own pages (home + about/partners/ecosystem/...) and
    return {path: text}. Best-effort: unreachable paths are simply omitted. This surfaces
    ecosystem/partnership/customer facts that live only on the site and never got indexed
    by DuckDuckGo (the phena.tech 'part of X ecosystem' case).

    The root is fetched first so its RESOLVED url can reveal a locale prefix ('/en'), which is
    then applied to the remaining paths. Without this, a localised SPA answers every bare path
    ('/about', '/team') with its homepage: the fingerprint dedup below drops them all as
    duplicates and the genuinely distinct '/en/about' is never requested. Detecting the prefix
    keeps the request count flat instead of trying both spellings of every path."""
    domain = str(domain or "").strip()
    if not domain:
        return {}
    # Normalise to a bare host, then rebuild canonical https URLs per path.
    from urllib.parse import urlparse
    import re
    host = urlparse(domain if "//" in domain else "https://" + domain).hostname or ""
    if not host or not _is_public_host(host):
        return {}
    import concurrent.futures

    # Root first and alone: it is both a wanted page and the probe that reveals the locale
    # prefix the remaining paths need.
    rest = [p for p in paths if p != ""]
    root_text, resolved = _fetch_cached(f"https://{host}", timeout=per_timeout)
    prefix = ""
    if resolved:
        # e.g. https://www.phena.tech/en -> '/en'; ignore anything that is not a locale.
        landed = "/" + urlparse(resolved).path.strip("/").split("/")[0]
        if re.fullmatch(r"/[a-z]{2}(-[A-Za-z]{2})?", landed):
            prefix = landed

    # The rest are independent GETs. Sequentially they cost up to per_timeout each — ~7s of a
    # run for a site where most paths 404 — so they are fetched concurrently and reassembled
    # in the ORIGINAL path order below. Completion order must not decide the result: it would
    # vary run to run and change which duplicate survives the fingerprint check.
    texts: dict = {"": root_text}
    if rest:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(rest))) as ex:
            # copy_context per submission: pool threads do not inherit the caller's context,
            # which the cache-bypass flag lives in.
            futures = {
                p: ex.submit(contextvars.copy_context().run,
                             functools.partial(
                                 _fetch_cached,
                                 f"https://{host}{p if p.startswith(prefix) else prefix + p}",
                                 timeout=per_timeout))
                for p in rest
            }
            for p, fut in futures.items():
                try:
                    texts[p] = fut.result()[0]
                except Exception:
                    texts[p] = ""

    out: dict = {}
    seen: set = set()
    for path in paths:
        text = texts.get(path, "")
        if not text:
            continue
        # Now that same-site redirects are followed, distinct paths routinely resolve to the
        # same page ('/' -> '/en', '/about' -> '/about-us'). Keeping both would duplicate the
        # text in the evidence corpus and crowd out other pages under the prompt's char cap.
        fingerprint = hash(text[:2000])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out[path or "/"] = text[:max_chars]
    return out
