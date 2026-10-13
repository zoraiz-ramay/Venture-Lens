"""A refresh adds evidence; it never quietly loses what an earlier run of the same company found.

Each evaluation is a fresh sample of the web, and the newest one used to replace everything shown.
Across stored runs, Radical Dot's investors went 8 → 8 → 3 → 0 → 2 → 2 → 2 → 4 → 2 → 6 and Phena's
programmes 6 → 2: a reviewer refreshing a company could watch sourced evidence disappear.

This module merges the prior runs of a company into a fresh one. Like core/profile.py it
TRANSCRIBES — no I/O, no model, no judgement — so every rule below is about which evidence to keep,
never about what the evidence means:

- **Lists** (people, programmes, investors, competitors, headcount points) are a union keyed on
  identity. An item this run found again is current; an item only earlier runs found is kept, with
  its own source URL and ``last_confirmed_at`` — the date a run last found it — so the page can say
  so rather than present it as freshly confirmed.
- **Carried items must pass today's grounding**: an http source on the item itself. Runs made under
  older, weaker gates must not resurrect evidence those gates should have dropped.
- **Single values**: a blank or unsourced fresh value never replaces a sourced earlier one; when
  both are sourced the newer wins (fresh retrieval is the later date), and the value it replaced is
  kept in ``history`` with its source. A web value always beats an ``*_origin="llm"`` recall.
- **A market figure must be current** — fresh or carried: its ``as_of`` year within the last two
  calendar years and not in the future (a future-dated figure is a forecast); an undated one counts
  by when its run found it, within 18 months (`market_size_current`).
- **Identity first**: a prior run is used only if it is the same company (same registrable domain,
  or, with no domain on either side, the same normalised name). A namesake's investors are worse
  than none.

Not carried: free-text string lists (``reference_customers``), which carry no per-item source to
re-check, and anything model-judged (fit, pillars, scores), which is recomputed from the merged
evidence.

The pipeline applies `merge_profile` and `merge_trend` as the profile and trend branches land,
before anything is scored or emitted (core/pipeline.py).
"""
from __future__ import annotations

import copy
import datetime as _dt
import re
from urllib.parse import urlparse

from .text import (clean_person_name, founders_first, is_full_name, merge_person, parse_funding_stage, person_key,
                   same_person, short_name_matches)

MARKET_MAX_AGE_DAYS = 548                    # 18 months: an undated figure, by when it was found
MARKET_MAX_AGE_YEARS = 2                     # a dated figure: its year within the last two

# (container path in the profile result, key fields that identify an item)
_PROFILE_LISTS = (
    # People are matched on the person, not the spelling: "Dr. Andreas Wagner" is "Andreas Wagner".
    (("founders",), ("person",)),
    (("key_team",), ("person",)),
    (("advisors",), ("person",)),
    (("programs",), ("name",)),
    (("commercial", "investors"), ("name",)),
    (("commercial", "certifications"), ("name",)),
    # A point is {year, count, source_url}. This was keyed on a "date" field no point has, so every
    # point from one source shared a key and a carried series kept only the last of them.
    (("employees_over_time",), ("year", "source_url")),
    # Carried only with an http source, like every list: an application-only mention earns no bonus
    # on the run that found it either.
    (("siemens_engagements",), ("unit", "type", "product")),
)
# How many entries an open-ended landscape list shows; the rest are held back (see _merge_list).
LANDSCAPE_CAPS = {"competitors": 8, "funded_peers": 6, "active_investors": 8}
_TREND_LISTS = (
    (("landscape", "competitors"), ("name",)),
    (("landscape", "funded_peers"), ("company",)),
    (("landscape", "active_investors"), ("name",)),
)
# Single values and where their source is recorded. "facts" names the fact keys in the same run
# that evidence the value; "source" a sibling *_source field; "origin" a sibling *_origin field.
_SCALARS = (
    (("hq",), {"origin": "hq_origin", "facts": ("hq", "location_research")}),
    (("founded_year",), {"facts": ("founded_year", "founded_year_research")}),
    (("funding",), {"facts": ("funding", "funding_research")}),
    (("employees",), {"facts": ("employees", "employees_research")}),
    (("linkedin_url",), {"origin": "linkedin_url_origin"}),
    (("crunchbase_url",), {"origin": "crunchbase_url_origin"}),
    (("parent_group",), {"facts": ("parent_group",)}),
    (("customer_segment",), {"source": "customer_segment_source"}),
    (("linkedin_size_band",), {"source": "linkedin_size_source"}),
    (("commercial", "deployment"), {"source": "deployment_source"}),
    (("commercial", "has_public_api"), {"source": "api_source", "facts": ("public_api",)}),
    (("commercial", "pricing_public"), {"source": "pricing_source"}),
    (("commercial", "sells_hardware"), {"source": "hardware_source"}),
    (("commercial", "revenue_signal"), {"source": "revenue_source", "facts": ("revenue_signal",)}),
    (("commercial", "funding_stage"), {"facts": ("funding_stage",)}),
    # Values that carry their own source: a statement about the customer base (it scores the
    # traction Customers division when no customer is named) and a quoted revenue figure.
    (("customer_segment_grade",), {"self": True}),
    (("commercial", "revenue"), {"self": True}),
)


# ----------------------------------------------------------------------------------- helpers

def _http(url) -> bool:
    return str(url or "").strip().lower().startswith(("http://", "https://"))


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()


def _get(d: dict, path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _set(d: dict, path, value) -> None:
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def _blank(value) -> bool:
    return value is None or value is False or (isinstance(value, (str, list, dict)) and not value) \
        or str(value).strip().lower() in ("", "nan", "none")


def _domain(url) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    host = urlparse(raw if "//" in raw else "//" + raw).netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _own_domain(domain: str, name: str) -> bool:
    """Whether a domain plausibly belongs to the company rather than a page ABOUT it.

    A run's website field is sometimes a directory listing — Makkook AI's latest run recorded
    clutch.co/profile/makkook-ai — and comparing that against makkook.ai would call one company
    two. A domain counts as the company's own only when its label carries a word of the name.
    """
    label = domain.split(".")[0].replace("-", "")
    return any(len(t) >= 3 and t in label for t in name.split())


def identity(company: str, *urls) -> dict:
    """{name, domain}: the normalised name, and the first of ``urls`` that is the company's own domain."""
    name = _norm(company)
    return {"name": name, "domain": next((d for d in map(_domain, urls) if d and _own_domain(d, name)), "")}


def _run_identity(run: dict) -> dict:
    prof, dp = run.get("profile") or {}, run.get("deep_profile") or {}
    return identity(run.get("company") or prof.get("company_name", ""),
                    prof.get("website"), prof.get("domain"), dp.get("website"))


def same_company(a: dict, b: dict) -> bool:
    """Two own domains must agree; without one on either side, the full normalised name must."""
    if a["domain"] and b["domain"]:
        return a["domain"] == b["domain"]
    return bool(a["name"]) and a["name"] == b["name"]


def _when(run: dict) -> str:
    return str(run.get("run_created_at") or run.get("created_at") or "")


def _name_key(value) -> str:
    """A name's key, singular: "TUM Venture Labs" and "TUM Venture Lab" are one programme."""
    words = _norm(value).split()
    if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def _item_key(item, fields) -> tuple:
    if not isinstance(item, dict):
        return ()
    return tuple(person_key(item.get("name")) if f == "person"
                 else str(item.get(f) or "").strip() if f == "source_url"
                 else _name_key(item.get(f)) if f in ("name", "company")
                 else _norm(item.get(f)) for f in fields)


def _grounded(item) -> bool:
    """Today's bar for a carried list item: it names its own http source."""
    return isinstance(item, dict) and (_http(item.get("source_url")) or _http(item.get("linkedin")))


def _sourced(container: dict, path, spec: dict, facts: list) -> bool:
    if spec.get("self"):
        value = _get(container, path)
        return isinstance(value, dict) and _http(value.get("source_url"))
    parent = _get(container, path[:-1]) if len(path) > 1 else container
    parent = parent if isinstance(parent, dict) else {}
    if spec.get("origin") and parent.get(spec["origin"]) == "web":
        return True
    if spec.get("source") and _http(parent.get(spec["source"])):
        return True
    keys = set(spec.get("facts", ()))
    return any(f.get("key") in keys and _backed(f) for f in facts or ())


def _backed(fact: dict) -> bool:
    """A fact with a source: a web URL, or the database / application form it came from.

    GlassDollar facts carry source_url "GlassDollar", not a URL. They are the most authoritative
    source a run has (the database wins over web extraction), so treating them as unsourced would
    let an older web value overwrite the database.
    """
    return _http(fact.get("source_url")) \
        or str(fact.get("method", "")).startswith(("glassdollar", "tracxn")) \
        or fact.get("source_type") in ("private", "self_reported")


# How far a single value's source is trusted. A value is replaced only by one at least this
# trusted, so a third-party page can never overwrite what the database states, and a model recall
# never overwrites anything sourced.
_DATABASE, _WEB, _RECALL = 3, 1, 0
_STAGE_RANK = {"grant": 0, "pre_seed": 1, "seed": 2, "series_a": 3, "series_b_plus": 4}


def _authority(container: dict, path, spec: dict, facts: list) -> int:
    parent = _get(container, path[:-1]) if len(path) > 1 else container
    parent = parent if isinstance(parent, dict) else {}
    if spec.get("origin") and parent.get(spec["origin"]) == "llm":
        return _RECALL
    keys = set(spec.get("facts", ()))
    if any(f.get("key") in keys and (str(f.get("method", "")).startswith(("glassdollar", "tracxn"))
                                     or f.get("source_type") in ("private", "self_reported"))
           for f in facts or ()):
        return _DATABASE
    return _WEB


def _later_round(path, new, old) -> bool:
    """A funding value naming a later stage than the stored one: the company raised again."""
    if path[-1] not in ("funding", "funding_stage"):
        return False
    rank = lambda v: _STAGE_RANK.get(parse_funding_stage(v), -1)  # noqa: E731
    return rank(new) > rank(old) >= 0


def _facts_of(run_like: dict) -> list:
    return [f if isinstance(f, dict) else f.as_dict() for f in run_like.get("facts") or ()]


# ------------------------------------------------------------------------------- list merging

def _fill_blanks(known: dict, found: dict) -> None:
    for field, value in found.items():
        if field not in ("name", "last_confirmed_at") and not _blank(value) and _blank(known.get(field)):
            known[field] = copy.deepcopy(value)


# Fields that say where a programme membership is evidenced; they travel with its confidence.
_EVIDENCE_FIELDS = ("source_url", "evidence_url", "evidence", "quote")


def _prefer_corroborated(entry: dict, members: list) -> None:
    """A membership someone else's page states beats one only the company's own site claims
    (score.py discounts the claim), whichever run found which: keep the corroborated evidence."""
    if "confidence" not in entry or str(entry.get("confidence") or "").lower() == "corroborated":
        return
    for m in members:
        if str(m["item"].get("confidence") or "").lower() == "corroborated":
            entry["confidence"] = m["item"]["confidence"]
            for f in _EVIDENCE_FIELDS:
                if f in m["item"]:
                    entry[f] = m["item"][f]
            return


def _merge_list(fresh_items, prior_runs, path, fields, getter, report: dict, label: str,
                cap: int = 0, overflow: list | None = None) -> list:
    """One entry per entity across this run and every earlier one, in an order that does not depend
    on which run found what.

    - **One entry**: an entity this run and earlier runs both found is kept once. The earliest
      grounded copy is the base — so a name's spelling does not flip between refreshes — and every
      later copy, then this run's, only fills its blanks. A thin fresh founder therefore never
      replaces a richer carried one, and a richer fresh one still adds what the carried copy lacked.
      A programme keeps corroborated evidence over a self-asserted claim either way round.
    - **People** are one person by ``same_person``; a first name alone joins the one full-name
      person on record it names (``text.short_name_matches``) and is otherwise dropped, as at
      extraction (``text.dedupe_people``). Placeholder names are never kept.
    - **Order**: entities earlier runs knew come first, by the earliest run that had them and then
      by key; entities only this run found follow, by key. The order a model listed them in, or
      which refresh happened to find which, no longer moves anything. A dated series (headcount
      points) is ordered by its key alone.
    - A carried entity (not found by this run) must still name an http source and says when it was
      last found (``last_confirmed_at``).
    """
    people = fields == ("person",)
    groups: list = []                       # [{"key", "full", "members": [{item, when, fresh, grounded}]}]
    loose: list = []                        # fresh items with no identity: kept as they are, last

    def place(item, when, fresh):
        if not isinstance(item, dict):
            if fresh:
                loose.append(item)
            return
        full = True
        if people:
            name = clean_person_name(item.get("name"))
            if not name:
                return
            item, full = {**item, "name": name}, is_full_name(name)
        key = _item_key(item, fields)
        if not key or not any(key):
            if fresh:
                loose.append(item)
            return
        member = {"item": item, "when": when, "fresh": fresh, "grounded": fresh or _grounded(item)}
        if people:
            # A person is matched on more than the name key: an earlier run's "KD Kutadgu Gokalp
            # Demirci" is this run's "Kutadgu Gokalp Demirci", and carrying both listed him twice.
            group = next((g for g in groups if g["full"] == full
                          and any(same_person(m["item"], item) for m in g["members"])), None)
        else:
            group = next((g for g in groups if g["key"] == key), None)
        if group is None:
            group = {"key": key, "full": full, "members": []}
            groups.append(group)
        group["members"].append(member)

    for run in prior_runs:                                  # oldest → newest
        for item in getter(run, path) or []:
            place(item, _when(run), False)
    for item in fresh_items or []:
        place(item, "", True)

    def live(group):
        return any(m["fresh"] or m["grounded"] for m in group["members"])

    if people:
        named = [g for g in groups if g["full"] and live(g)]
        for g in [g for g in groups if not g["full"]]:
            # Unique or nothing: "Alex" beside two Alexanders is neither of them.
            hits = [n for n in named if any(short_name_matches(m["item"], n_m["item"])
                                            for m in g["members"] for n_m in n["members"])]
            if len(hits) == 1:
                hits[0]["members"] += g["members"]
        groups = [g for g in groups if g["full"]]

    newest_when = max((_when(r) for r in prior_runs or ()), default=None)
    entries, reconfirmed, fresh_count = [], 0, 0
    for g in groups:
        if not live(g):
            continue
        data = sorted((m for m in g["members"] if m["grounded"]), key=lambda m: (m["fresh"], m["when"]))
        base = next(m for m in data if not people or is_full_name(m["item"].get("name")))
        entry = copy.deepcopy(base["item"])
        entry.pop("last_confirmed_at", None)
        for m in data:
            if m is not base:
                (merge_person if people else _fill_blanks)(entry, m["item"])
        _prefer_corroborated(entry, data)
        seen = [m["when"] for m in g["members"] if not m["fresh"]]
        found_now = any(m["fresh"] for m in g["members"])
        if found_now:
            fresh_count += 1
            reconfirmed += bool(seen)
        else:
            newest = data[-1]
            entry["last_confirmed_at"] = newest["item"].get("last_confirmed_at") or newest["when"]
        sort_key = person_key(entry.get("name")) if people else "\x1f".join(_item_key(entry, fields))
        # How many runs found it themselves (a carried copy in a later run's record is not a find).
        found_by = len({m["when"] for m in g["members"]
                        if not m["fresh"] and not m["item"].get("last_confirmed_at")}) + found_now
        # Shown (not held back) by the newest earlier run: it keeps its slot unless a challenger
        # has clearly more finds, so near-equal counts do not swap entries back and forth.
        shown_before = any(not m["fresh"] and m["when"] == newest_when and not m["item"].get("_held")
                           for m in g["members"])
        entry.pop("_held", None)
        entries.append((min(seen) if seen else None, sort_key, entry, found_by + shown_before))
    if cap and len(entries) > cap:
        # Open-ended lists (competitors, peers) gain a name or two on every refresh, so the union
        # never settled: Radical Dot's competitors went 7 -> 9 -> 13. Show the `cap` entities the
        # most runs found (earliest first-seen breaks ties); hold the rest back, still counted, so
        # a name one search turned up once cannot displace established ones.
        ranked = sorted(entries, key=lambda e: (-e[3], e[0] or "￿", e[1]))
        if overflow is not None:
            overflow.extend(e[2] for e in sorted(ranked[cap:], key=lambda e: e[1]))
        entries = ranked[:cap]
    if "year" in fields:
        ordered = sorted(entries, key=lambda e: e[1])
    else:
        ordered = sorted((e for e in entries if e[0] is not None), key=lambda e: (e[0], e[1])) \
            + sorted((e for e in entries if e[0] is None), key=lambda e: e[1])
    report[label] = {"new": fresh_count - reconfirmed, "reconfirmed": reconfirmed,
                     "carried": len(entries) - fresh_count}
    return [e[2] for e in ordered] + loose


def _profile_of(run: dict) -> dict:
    return run.get("deep_profile") or {}


def team_run(record: dict | None) -> dict | None:
    """The team section's stored refresh (api ``lookup/team``) as one more prior run, so what it
    found is carried into a full evaluation exactly like what an earlier evaluation found."""
    if not record:
        return None
    # `new` marks what one refresh added, for the page that ran it; it is not part of the evidence.
    items = lambda k: [{f: v for f, v in i.items() if f != "new"} for i in record.get(k) or [] if isinstance(i, dict)]  # noqa: E731
    dp = {k: items(k) for k in ("founders", "key_team", "advisors", "programs")}
    dp["commercial"] = {"investors": items("investors")}
    # Marked so merge_profile does not read it as the newest evaluation: it holds lists, no single values.
    return {"deep_profile": dp, "created_at": str(record.get("retrieved_at") or record.get("stored_at") or ""),
            "team_record": True}


def known_team(prior_runs: list) -> dict:
    """Whether earlier runs already found sourced founders / programmes. A full evaluation does not
    search for them again; they come in through merge_profile like every carried list."""
    def found(path):
        return any(_grounded(i) for run in prior_runs or [] for i in _get(_profile_of(run), path) or [])
    return {"founders": found(("founders",)), "programs": found(("programs",))}


# ----------------------------------------------------------------------------------- public API

def degraded(run: dict) -> bool:
    """A run in which most model calls failed.

    Its profile is the keyword fallback and its trend, fit and verification are heuristics, so it
    is no evidence that a fact went missing or changed. Read from the run's own call log
    (``token_usage``: failed_calls == calls): that is attached to every run that called the model,
    and unlike ``degraded`` — which names a stage when any one call fails — it says *all* failed.
    Radical Dot's first two stored runs (a dead Gemini key, 19 of 19 calls failed) are such runs.
    A run made with no model configured makes no calls and is not degraded; it never claimed more.
    """
    usage = run.get("token_usage") if isinstance(run, dict) else None
    if not isinstance(usage, dict):
        return False
    try:
        calls, failed = int(usage.get("calls") or 0), int(usage.get("failed_calls") or 0)
    except (TypeError, ValueError):
        return False
    # Half or more, not only all: a run that lost its credits mid-way (Radical Dot, October 2026:
    # 63 of 78 calls failed on insufficient_quota) has no trend, no pillars and no total, and as
    # an earlier run it would vouch that its missing competitors were never found.
    return calls > 0 and failed * 2 >= calls


def pinned_website(name: str, prior_runs) -> str:
    """The company's own website as its newest usable earlier run recorded it, or ''.

    A company outside the database is re-resolved from a web search on every run, and the website
    that resolution picks can change (a directory page one time, the company's domain the next).
    Every domain-pinned query and cache key follows the website, and identity matching follows the
    domain, so a changed guess re-ran different searches and could orphan every earlier run.
    Only a website whose domain is the company's own (``_own_domain``) is pinned.
    """
    norm = _norm(name)
    for run in reversed([r for r in prior_runs or () if isinstance(r, dict) and not degraded(r)]):
        ident = _run_identity(run)
        if ident["domain"] and ident["name"] == norm:
            prof = run.get("profile") or {}
            site = prof.get("website") or (run.get("deep_profile") or {}).get("website") or ""
            if _domain(site) == ident["domain"]:
                return str(site)
    return ""


def usable_priors(prior_runs, fresh_identity: dict) -> list:
    """The prior runs that are the same company and not degraded, oldest first."""
    runs = [r for r in prior_runs or () if isinstance(r, dict) and not degraded(r)
            and same_company(_run_identity(r), fresh_identity)]
    return sorted(runs, key=_when)


def merge_profile(fresh: dict, prior_runs: list, now: str, report: dict) -> dict:
    """``fresh`` is research_profile's result: {"profile": deep_profile, "facts": [...], ...}."""
    out = dict(fresh)
    dp = copy.deepcopy(fresh.get("profile") or {})
    fresh_facts = _facts_of(fresh)
    # Lists are put in their stable order even on a company's first run; otherwise run 1 showed
    # the model's listing order and run 2 the merged order, so the first refresh reordered them.
    for path, fields in _PROFILE_LISTS:
        merged = _merge_list(_get(dp, path), prior_runs, path, fields,
                             lambda run, p: _get(_profile_of(run), p), report, ".".join(path))
        if merged or _get(dp, path) is not None:
            _set(dp, path, merged)
    founders_first(dp)
    if not prior_runs:
        out["profile"] = dp
        return out
    history = dp["history"] if isinstance(dp.get("history"), dict) else {}
    evaluations = [r for r in prior_runs if not r.get("team_record")]
    for path, spec in _SCALARS:
        fresh_val = _get(dp, path)
        fresh_ok = not _blank(fresh_val) and _sourced(dp, path, spec, fresh_facts)
        best, best_auth = None, -1                           # most trusted, then newest, sourced prior value
        for run in prior_runs:
            pdp, val = _profile_of(run), _get(_profile_of(run), path)
            if not _blank(val) and _sourced(pdp, path, spec, _facts_of(run)):
                auth = _authority(pdp, path, spec, _facts_of(run))
                if auth >= best_auth:
                    best, best_auth = (val, run, pdp), auth
        if best is None:
            continue
        val, run, pdp = best
        name = ".".join(path)
        if fresh_ok:
            if _norm(val) == _norm(fresh_val):
                continue
            # Two sourced values disagree. The stored one stands unless the fresh one is at least
            # as trusted AND either more trusted, a later funding round, or what the newest earlier
            # run also found. One lucky (or unlucky) search no longer flips a headline value; a
            # real change shows on the next refresh, when it is seen twice.
            fresh_auth = _authority(dp, path, spec, fresh_facts)
            stored_auth = _authority(pdp, path, spec, _facts_of(run))
            newest = _get(_profile_of(evaluations[-1]), path) if evaluations else None
            adopt = fresh_auth >= stored_auth and (
                fresh_auth > stored_auth or _later_round(path, fresh_val, val)
                or _norm(newest) == _norm(fresh_val))
            if adopt:
                history.setdefault(name, []).append({"value": val, "replaced_at": now,
                                                     "found_at": _when(run)})
                continue
            history.setdefault(name, []).append({"value": fresh_val, "replaced_at": now,
                                                 "reason": "candidate: seen once"})
            # Falls through: the stored sourced value is kept, like a blank fresh one.
        # Blank, unsourced, an *_origin="llm" recall, or a candidate not yet adopted: the sourced
        # value stands.
        if not _blank(fresh_val) and not fresh_ok:
            history.setdefault(name, []).append({"value": fresh_val, "replaced_at": now,
                                                 "reason": "unsourced on this run"})
        _set(dp, path, copy.deepcopy(val))
        for side in ("origin", "source"):
            if spec.get(side):
                sib = path[:-1] + (spec[side],)
                _set(dp, sib, _get(pdp, sib))
        dp.setdefault("carried_fields", {})[name] = _when(run)
        report.setdefault("fields_carried", []).append(name)
    if history:
        dp["history"] = history
    _merge_customers(dp, prior_runs, report)
    out["profile"] = dp
    out["facts"] = list(fresh.get("facts") or []) + _carried_facts(fresh_facts, prior_runs, dp)
    return out


def _merge_customers(dp: dict, prior_runs: list, report: dict) -> None:
    """Named reference customers an earlier run found and its fact-check sourced.

    A reference customer is a bare name — the source that establishes it lives in that run's
    verification claim. Only a name whose claim carries an http source and was not contradicted is
    carried, with that source in ``customer_evidence`` (traction reads it when this run's own
    verification has no claim for the name) and its customer-type label in ``customer_classes``,
    without which the traction rubric would not count it.
    """
    fresh = list(dp.get("reference_customers") or [])
    have = {_norm(n) for n in fresh}
    evidence = dict(dp.get("customer_evidence") or {})
    classes = list(dp.get("customer_classes") or [])
    classed = {_norm(c.get("name")) for c in classes if isinstance(c, dict)}
    carried: dict = {}
    for run in prior_runs:
        claims = {_norm(c.get("value")): c for c in (run.get("verification") or {}).get("claims") or []
                  if isinstance(c, dict) and c.get("field") == "reference_customer"}
        pdp = _profile_of(run)
        run_classes = {_norm(c.get("name")): c for c in pdp.get("customer_classes") or [] if isinstance(c, dict)}
        prior_evidence = pdp.get("customer_evidence") or {}
        for name in pdp.get("reference_customers") or []:
            key = _norm(name)
            if not key or key in have:
                continue
            claim = claims.get(key) or {}
            url = claim.get("evidence_url") or (prior_evidence.get(name) or {}).get("source_url")
            if not _http(url) or claim.get("status") == "contradicted":
                continue
            when = (prior_evidence.get(name) or {}).get("last_confirmed_at") or _when(run)
            carried[key] = (name, {"source_url": url, "last_confirmed_at": when}, run_classes.get(key))
    for key, (name, ev, cls) in carried.items():
        fresh.append(name)
        evidence[name] = ev
        if cls and key not in classed:
            classes.append(cls)
    reconfirmed = sum(1 for n in fresh[:len(dp.get("reference_customers") or [])]
                      if any(_norm(n) in {_norm(x) for x in _profile_of(r).get("reference_customers") or []}
                             for r in prior_runs))
    report["reference_customers"] = {"new": len(dp.get("reference_customers") or []) - reconfirmed,
                                     "reconfirmed": reconfirmed, "carried": len(carried)}
    if carried:
        dp["reference_customers"], dp["customer_evidence"], dp["customer_classes"] = fresh, evidence, classes


def _carried_facts(fresh_facts: list, prior_runs: list, dp: dict) -> list:
    """The prior facts behind carried items and fields, so the Evidence view can still show them."""
    from .provenance import Fact
    have = {(f.get("key"), _norm(f.get("value")), f.get("source_url")) for f in fresh_facts}
    wanted_names = {_norm(i.get("name")) for path, _ in _PROFILE_LISTS
                    for i in _get(dp, path) or [] if isinstance(i, dict) and i.get("last_confirmed_at")}
    wanted_keys = {k for path, spec in _SCALARS if ".".join(path) in (dp.get("carried_fields") or {})
                   for k in spec.get("facts", ())}
    out, added = [], set()
    for run in prior_runs:
        for f in _facts_of(run):
            sig = (f.get("key"), _norm(f.get("value")), f.get("source_url"))
            if sig in have or sig in added or not _http(f.get("source_url")):
                continue
            if _norm(f.get("value")) in wanted_names or f.get("key") in wanted_keys:
                added.add(sig)
                kept = {k: f[k] for k in ("key", "value", "source_url", "method", "confidence",
                                          "verified", "retrieved_at", "source_type") if k in f}
                out.append(Fact(**kept))
    return out


def _as_of(size: dict) -> _dt.date | None:
    """The date a market figure is stated for; a year alone reads as 31 December of that year."""
    raw = str((size or {}).get("as_of") or "").strip()
    m = re.search(r"(19|20)\d{2}", raw)
    if not m:
        return None
    year = int(m.group(0))
    month = (re.search(r"(?:19|20)\d{2}-(0?[1-9]|1[0-2])\b", raw)
             or re.search(r"\b(0?[1-9]|1[0-2])/(?:19|20)\d{2}\b", raw))
    if month:
        return _dt.date(year, int(month.group(1)), 28)
    return _dt.date(year, 12, 31)            # year only: the latest reading of that year


def market_size_current(size, today: _dt.date, found=None) -> bool:
    """A cited market figure that is current: its year within the last MARKET_MAX_AGE_YEARS, and
    not in the future.

    Reports published this year state last year's or the year before's market, so the old 18-month
    line dropped a 2024 base figure in October 2026 while a "2030" figure passed, because a forecast
    is always dated ahead. A future-dated figure is a forecast and is not the market's size. An
    undated figure is current when it was found (``found``, the run's date) within 18 months.
    """
    if not isinstance(size, dict):
        return False
    when = _as_of(size)
    if when is None:
        return found is not None and 0 <= (today - found).days <= MARKET_MAX_AGE_DAYS
    return today.year - MARKET_MAX_AGE_YEARS <= when.year <= today.year


def _run_date(run: dict):
    try:
        return _dt.date.fromisoformat(_when(run)[:10])
    except ValueError:
        return None


def merge_trend(fresh: dict, prior_runs: list, now: str, today: _dt.date, report: dict) -> dict:
    """``fresh`` is analyze_trend's result. Applies the market-figure currency rule even with no priors.

    ``now`` is not read (a carried item's date is the run that found it); it mirrors merge_profile.
    """
    if not isinstance(fresh, dict):
        return fresh
    out = copy.deepcopy(fresh)
    land = out.get("landscape")
    if not isinstance(land, dict) and prior_runs:
        # This run's trend failed before it produced a landscape (a failed momentum call returns
        # the offline result, which has none). Without this the run showed no competitors, peers or
        # market size at all; an empty landscape lets the merge below carry what earlier runs found.
        land = out["landscape"] = {"competitors": [], "funded_peers": [], "active_investors": [],
                                   "market_size": None}
        report["landscape_missing"] = True
    if isinstance(land, dict):
        size = land.get("market_size")
        if size and not market_size_current(size, today, found=today):
            land["market_size"] = None
            report["market_size_dropped"] = {"as_of": (size or {}).get("as_of", ""),
                                             "reason": "a forecast or older than two years"}
        if True:                                     # lists settle on a first run as well
            held_back: dict = {}
            for path, fields in _TREND_LISTS:
                held: list = []
                land[path[-1]] = _merge_list(
                    _get(out, path), prior_runs, path, fields,
                    lambda run, p: (_get(run.get("trend") or {}, p) or [])
                    + [{**i, "_held": True} for i in _get(run.get("trend") or {}, ("landscape", "held_back", p[-1])) or []
                       if isinstance(i, dict)],
                    report, ".".join(path), cap=LANDSCAPE_CAPS.get(path[-1], 0), overflow=held)
                if held:
                    held_back[path[-1]] = held
            if held_back:
                land["held_back"] = held_back
            stored, stored_run = None, None
            for run in reversed(prior_runs):                # newest qualifying prior figure
                prior = _get(run.get("trend") or {}, ("landscape", "market_size"))
                if prior and _http(prior.get("source_url")) and market_size_current(prior, today, _run_date(run)):
                    stored, stored_run = prior, run
                    break
            fresh_size = land.get("market_size")
            # A different figure is adopted only when the newest earlier run found it too. Every
            # refresh used to show another number for the same market — Radical Dot read $815M,
            # then USD 3.7B, then USD 4.8B — because each search wave lands on another report that
            # sizes a differently drawn market. Seen twice, it is the figure the web now gives.
            if stored and fresh_size and _norm(fresh_size.get("value")) != _norm(stored.get("value")):
                evaluations = [r for r in prior_runs if not r.get("team_record")]
                newest = _get((evaluations[-1] if evaluations else {}).get("trend") or {},
                              ("landscape", "market_size")) or {}
                if _norm(newest.get("value")) != _norm(fresh_size.get("value")):
                    report["market_size_candidate"] = {"value": fresh_size.get("value", ""),
                                                       "as_of": fresh_size.get("as_of", "")}
                    fresh_size = None
            if not fresh_size and stored:
                land["market_size"] = {**stored, "last_confirmed_at": stored.get("last_confirmed_at")
                                       or _when(stored_run)}
                report.setdefault("fields_carried", []).append("landscape.market_size")
    return out
