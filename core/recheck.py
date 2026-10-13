"""Re-read the pages earlier runs cited, and flag a carried fact its own source no longer states.

Carry-forward keeps every grounded fact an earlier run found, so search luck can no longer drop
one. The cost is that a fact can outlive its evidence — an advisor who left, a programme page
rewritten. Each run therefore re-reads up to RECHECK_LIMIT of the cited pages behind CARRIED facts
(plain HTTP through the page cache, no model, no tokens), alongside the research branches, and
reports per fact:

- ``supported``   the page still states it (the same window checks the gates use: a person's
  name with the company, a programme within reach of the company, an investor with the company);
- ``unsupported`` the page was fetched and no longer states it;
- ``unreachable`` the page could not be read (LinkedIn and Crunchbase refuse; a timeout; the
  re-check's own time budget ran out). Never counted against the fact.

A fact is ``unconfirmed`` after two fetched misses in a row, and nothing is ever removed: the
result goes to ``result["fact_status"]``, which is not scorer evidence, and the UI labels it.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import os
import time

from . import web
from .profile import _names_in, _tied

RECHECK_LIMIT = int(os.getenv("RECHECK_LIMIT", "24"))
RECHECK_BUDGET_S = float(os.getenv("RECHECK_BUDGET_S", "8"))
_UNREADABLE = ("linkedin.", "crunchbase.", "pitchbook.", "facebook.", "x.com", "twitter.")
# (kind, path in deep_profile)
_KINDS = (("founder", ("founders",)), ("key_team", ("key_team",)), ("advisor", ("advisors",)),
          ("program", ("programs",)), ("investor", ("commercial", "investors")))


def _get(d, path):
    for key in path:
        d = d.get(key) if isinstance(d, dict) else None
    return d


def candidates(deep_profile: dict, limit: int = RECHECK_LIMIT) -> list[dict]:
    """The carried facts with a readable cited page, in a fixed order."""
    out = []
    for kind, path in _KINDS:
        for item in _get(deep_profile or {}, path) or []:
            url = str((item or {}).get("source_url") or "") if isinstance(item, dict) else ""
            if not item.get("last_confirmed_at") or not url.startswith(("http://", "https://")) \
                    or any(h in url.lower() for h in _UNREADABLE):
                continue
            out.append({"kind": kind, "name": str(item.get("name") or ""), "source_url": url})
    out.sort(key=lambda c: (c["kind"], c["name"].casefold(), c["source_url"]))
    return out[:limit]


def states(fact: dict, company: str, page: str) -> bool:
    """Whether the page still states the fact, by the same window checks the extraction gates use."""
    text, name, firm = page.casefold(), fact["name"], str(company or "").casefold()
    if not name or not text:
        return False
    if fact["kind"] == "program":
        return _tied(name, firm, text, fact["source_url"])
    names_firm = firm in text or firm.replace(" ", "") in text.replace(" ", "")
    if fact["kind"] == "investor":
        return names_firm and name.casefold() in text
    return names_firm and _names_in(name, page)


def recheck(deep_profile: dict, company: str, previous: list | None = None,
            budget_s: float = RECHECK_BUDGET_S, fetch=None) -> list[dict]:
    """One status per carried fact (see the module docstring). ``previous`` is the newest earlier
    run's ``fact_status``, for the consecutive-miss count."""
    facts = candidates(deep_profile)
    if not facts:
        return []
    fetch = fetch or (lambda url: web._fetch_cached(url, timeout=min(6.0, budget_s))[0])
    misses_before = {(p.get("kind"), p.get("name"), p.get("source_url")): int(p.get("misses") or 0)
                     for p in previous or [] if isinstance(p, dict)}
    pages: dict = {}
    urls = sorted({f["source_url"] for f in facts})
    deadline = time.monotonic() + budget_s
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(urls)))
    try:
        futures = {pool.submit(contextvars.copy_context().run, fetch, u): u for u in urls}
        for fut in concurrent.futures.as_completed(futures, timeout=max(0.0, deadline - time.monotonic())):
            try:
                pages[futures[fut]] = fut.result() or ""
            except Exception:
                pages[futures[fut]] = ""
    except concurrent.futures.TimeoutError:
        pass                                        # what has not landed is unreachable this run
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    out = []
    for fact in facts:
        page = pages.get(fact["source_url"], "")
        key = (fact["kind"], fact["name"], fact["source_url"])
        if not page:
            status, misses = "unreachable", misses_before.get(key, 0)
        elif states(fact, company, page):
            status, misses = "supported", 0
        else:
            misses = misses_before.get(key, 0) + 1
            status = "unconfirmed" if misses >= 2 else "unsupported"
        out.append({**fact, "status": status, "misses": misses})
    return out
