"""Deep structured profile research: founders, advisors, employees, parent group,
startup programs (Xcelerator / incubators / corporate programs), reference customers,
and commercial posture (deployment, APIs, certifications, pricing, revenue, investors).

LLM path: LLM-generated queries -> DuckDuckGo -> LLM extraction strictly from evidence,
every populated field backed by a source URL. Offline fallback: keyword detection over
the same search corpus, so a profile is always returned.

Everything here TRANSCRIBES evidence; nothing here judges. The Siemens Financial Services
verdict used to be asked for in the middle of the main extraction prompt and is now decided in
core/programs.py, which reads the commercial posture this module evidences. That split is the
reason the answer stopped being "yes" for every company.

Reading order: `research_profile` (the last function) is the pipeline's profile branch and calls
the rest in order — main wave and extraction, the grounding gates, database seeding, the recall
nets, commercial posture, the Siemens relationship, then the tail passes. `refresh_team`,
`search_team_web` and `merge_team` serve the API's team-section Refresh. Section headers below
mark each part.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import copy
import datetime
import functools
import os
import re
import unicodedata
from urllib.parse import urlparse

import pandas as pd

from .provenance import Fact
from .web import _ddg_many
from .llm import LLMClient
from .config import KNOWN_PROGRAM_TIERS
from . import programs as programs_mod
from .text import (_clean_source_url, _norm, dedupe_people, founders_first, has_funding_signal, is_named_org,
                   is_revenue_estimate, parse_funding_stage, person_key, same_person)

# ------------------------------------------------------------- the profile's shape
# Known startup programs for offline detection (matched case-insensitively).
KNOWN_PROGRAMS = {
    "siemens xcelerator": "corporate_program",
    "startup autobahn": "corporate_program",
    "nvidia inception": "corporate_program",
    "microsoft for startups": "corporate_program",
    "google for startups": "corporate_program",
    "aws activate": "corporate_program",
    "intel ignite": "corporate_program",
    "sap.io": "corporate_program",
    "y combinator": "accelerator",
    "techstars": "accelerator",
    "plug and play": "accelerator",
    "500 global": "accelerator",
    "entrepreneur first": "accelerator",
    "sosv": "accelerator",
    "antler": "accelerator",
    "startupbootcamp": "accelerator",
    "masschallenge": "accelerator",
    "seedcamp": "accelerator",
    "alchemist accelerator": "accelerator",
    "eit ": "incubator",
    "station f": "incubator",
    "cdl": "incubator",
    "unternehmertum": "incubator",
    "tum venture labs": "incubator",
    "respond accelerator": "accelerator",
    "xpreneurs": "incubator",
    "esa bic": "incubator",
}

EMPTY_PROFILE = {
    "founders": [],           # [{name, role, background, linkedin, source_url}]
    "key_team": [],           # early/core non-founder team [{name, role, source_url}]
    "advisors": [],           # [{name, role, affiliation, source_url}]
    "employees": "",          # best-evidence headcount
    "employees_over_time": [],  # [{year, count, source_url}] — evidence-cited points only
    # Why the series above is empty, which an empty list cannot say: "too_young" (fewer than
    # two calendar years exist to cite), "not_found" (searched, nothing cited), "unavailable"
    # (the search wave was throttled or the model was off, so nothing was actually looked at).
    # Without this the UI reported "no cited headcount history" for a company it never checked.
    "employees_history_status": "",
    # Public identifiers, recovered from evidence already in hand (see _extract_links). The web
    # path never captured these: web_profile_row skips social results when it guesses the website
    # and then hard-codes linkedin_url to "", so the profile header showed a blank LinkedIn row
    # for every company not in GlassDollar.
    "website": "",
    "linkedin_url": "",
    "crunchbase_url": "",
    # "web" when the URL was read off a search result, "llm" when it came from model knowledge
    # with nothing backing it. Same contract as hq_origin; see _recall_links_offline.
    "linkedin_url_origin": "",
    "crunchbase_url_origin": "",
    # What LinkedIn's own page states as the company size, when a result snippet carries it. A
    # BAND, kept apart from the cited headcount above rather than overwriting it: the two
    # disagree routinely (aggregators lag LinkedIn), and a band is not a count.
    "linkedin_size_band": "",
    "linkedin_size_source": "",
    "parent_group": "",       # part of a major group / corporate parent
    "hq": "",                 # web-researched headquarters; backfills a blank DB column
    "hq_source": "",          # URL supporting hq, when the evidence cited one
    # "web" when hq came from a cited result, "llm" when it came from model knowledge with no
    # source. The UI needs the distinction to label the value honestly; see _recall_hq_offline.
    "hq_origin": "",
    "founded_year": "",       # web-researched; backfills a blank DB column (see pipeline)
    "founded_year_source": "",  # URL supporting founded_year, when the evidence cited one
    "funding": "",            # web-researched round/amount; backfills a blank DB column
    "funding_source": "",     # URL supporting funding, when the evidence cited one
    # [{name, type: incubator|accelerator|corporate_program, source_url,
    #   confidence: corroborated|self_asserted}]
    "programs": [],
    "reference_customers": [],  # NAMED accounts only, grounded in evidence
    "customer_segment": "",    # segment/scale descriptor when customers aren't named (e.g. "7-8 figure e-commerce brands")
    "customer_segment_source": "",
    # Labels on the GROUNDED reference customers above — never new names — for the traction
    # rubric: [{name, relation: customer|pilot|partner|investor|supplier, size:
    # large_enterprise|sme, by}]. How big a named third party is identifies it, like a programme's
    # prestige tier; whether it is a customer at all stays with _ground_customers.
    "customer_classes": [],
    # {level: 1|2|3, quote, source_url} — how much a generically stated customer base says.
    "customer_segment_grade": {},
    # An existing relationship with Siemens, as the evidence states it — see _siemens_relationship:
    # [{unit, department, type, product, tool_id, year, quote, source_url, claimed}].
    "siemens_engagements": [],
    # Commercial posture — what the Siemens pillar gates and the SFS financeability gate read.
    # None of this was collected anywhere, so "can this be listed on the Xcelerator Marketplace"
    # and "is there anything here for SFS to underwrite" were both being answered from the pitch
    # text, which is the startup describing itself. Every field carries its own source URL under
    # the same rule as the rest of this profile: no source, not displayed and not scored.
    "commercial": {
        "deployment": "",            # cloud | edge | on_prem | hardware | ""
        "deployment_source": "",
        "has_public_api": False,
        "api_source": "",
        "certifications": [],        # [{name, source_url}] — ISO 27001, IEC 62443, SOC 2, ...
        "pricing_public": False,
        "pricing_source": "",
        "sells_hardware": False,
        "hardware_source": "",
        "revenue_signal": "",        # none | customers | contracted | recurring
        "revenue_source": "",
        # {status: amount|pre_revenue, quote, metric, fiscal_year, growth_pct, source_url}. The
        # quote is verbatim from the evidence and the amount is parsed from it in Python
        # (core/traction.py); the model's own reading of the number is never kept.
        "revenue": {},
        "funding_stage": "",         # pre_seed | seed | series_a | series_b_plus | grant | ""
        "investors": [],             # [{name, source_url}]
        "method": "none",
    },
    "method": "none",
}

# ------------------------------------------------------------- the evidence corpus
# Site paths whose text actually bears on commercial posture, and the enrichment query keys that
# search for the same thing. The commercial extraction reads ONLY these rather than sharing the
# general corpus: the two extractions want different evidence, and one 24k budget split between
# them means whichever runs second sees whatever the first left.
_COMMERCIAL_PATHS = ("/pricing", "/security", "/trust", "/docs", "/developers", "/api",
                     "/integrations", "/product", "/", "/about")
_COMMERCIAL_QUERIES = ("pricing_web", "security_web", "api_web", "deployment_web",
                       "marketplace_web", "investors_web", "funding_web", "crunchbase_web",
                       "customers_web", "revenue_web")


_CORPUS_CHARS = int(os.getenv("PROFILE_CORPUS_CHARS", "24000"))
# No single result may take more than this slice of the corpus. A ``__site__`` pseudo-result carries
# a WHOLE fetched page — fetch_site_text caps each at 12,000 characters — so TWO of them filled the
# entire 24k budget and the loop returned before a single search result was reached. Downstream that
# is invisible and self-consistent: the model is handed a corpus containing nothing but the
# company's own website, and reports quite correctly that it found no third-party evidence of
# customers, founders or headcount.
_CORPUS_LINE_CHARS = int(os.getenv("PROFILE_CORPUS_LINE_CHARS", "1800"))
# ...and the site pages TOGETHER may take at most this share, so a startup's own account of itself
# can never crowd out the independent evidence it exists to be checked against. Truncating here is
# safe for grounding: _program_grounded and _ground_customers scan the full ``results`` dict, not
# this corpus, so a membership published at the bottom of a long /partners page is still found.
_CORPUS_SITE_SHARE = float(os.getenv("PROFILE_CORPUS_SITE_SHARE", "0.4"))


def _interleave(queues: list, budget: int) -> list:
    """Round-robin across queues until ``budget`` characters are spent."""
    lines, total = [], 0
    for rank in range(max((len(q) for q in queues), default=0)):
        for q in queues:
            if rank >= len(q):
                continue
            line = q[rank]
            if total + len(line) + 1 > budget:
                return lines
            lines.append(line)
            total += len(line) + 1
    return lines


def _corpus(results: dict) -> str:
    """Flatten {query_key: hits} into the evidence block handed to the LLM.

    Results are interleaved ROUND-ROBIN across query keys, with the company's own fetched
    pages (``__site__*``) first, rather than concatenated key by key. A flat concatenation
    spends the whole budget on whichever queries happen to come first in the dict: for a
    typical 11-query wave the corpus ran to ~19k chars, so a 9k cap meant only the first four
    keys ever reached the model and everything after them — headcount, founding year,
    customers, and the site text itself — was silently dropped. That is indistinguishable
    downstream from "the web knows nothing", and it is why researched employee counts and
    founding years kept coming back empty even when the searches had found them.

    Interleaving alone was not enough, because it makes every QUERY equal without making every
    RESULT equal, and a fetched page is three orders of magnitude larger than a search snippet.
    Two of them exhausted the budget at rank 0. So each line is capped
    (``_CORPUS_LINE_CHARS``) and the site pages share a fixed slice of the total
    (``_CORPUS_SITE_SHARE``); whatever the site does not use falls through to the searches.
    Override any of the three with PROFILE_CORPUS_CHARS / _LINE_CHARS / _SITE_SHARE.
    """
    site_q, web_q = [], []
    for key, hits in (results or {}).items():
        queue = [f"[{key}] {h.get('title','')} :: {h.get('body','')} :: {h.get('href','')}"
                 [:_CORPUS_LINE_CHARS] for h in (hits or [])]
        (site_q if str(key).startswith("__site__") else web_q).append(queue)
    site_lines = _interleave(site_q, int(_CORPUS_CHARS * _CORPUS_SITE_SHARE))
    spent = sum(len(line) + 1 for line in site_lines)
    return "\n".join(site_lines + _interleave(web_q, _CORPUS_CHARS - spent))


# ------------------------------------------------------------- the main wave and value cleaners
_HQ_NON_ANSWERS = {"", "unknown", "n/a", "na", "none", "not stated", "not available",
                   "not specified", "remote", "worldwide", "global", "-", "—"}


def _clean_hq(value) -> str:
    """A place name, or ''.

    Asked where a company is based, a model that has not found the answer will say "Unknown",
    "Remote" or "Global" rather than nothing — and any of those rendered in the Location tile
    reads as an established fact about the company. They are refusals, so they are dropped. The
    length cap catches the other failure mode, a sentence of hedging in place of a city.
    """
    hq = " ".join(str(value or "").split()).strip(" .,;")
    if hq.lower() in _HQ_NON_ANSWERS or len(hq) > 60:
        return ""
    return hq


def _site_hint(row: pd.Series) -> str:
    """Bare host ('phena.tech') from the row's domain/website, or '' when unknown.

    Used to disambiguate identity-sensitive searches. A bare company name is frequently
    ambiguous — 'Phena' collides with Tryphena, Phena International Ltd, Phena's Studio — and
    those queries come back as noise, which downstream is indistinguishable from "the web
    knows nothing". Pinning the query to the company's own domain is what surfaces its
    LinkedIn ('Company size 2-10 employees') and CB Insights ('founded in 2026') entries."""
    raw = str(row.get("domain", "") or row.get("website", "")).strip()
    if not raw:
        return ""
    host = urlparse(raw if "//" in raw else "https://" + raw).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _queries(company: str, row: pd.Series, llm: LLMClient) -> dict:
    """The profile's whole wave: the fixed queries, then up to two the model suggests.

    research_profile searches the two halves separately (so the fixed ones start before the model
    answers); this combined form is what tests/test_research_recall.py checks the wave through."""
    return {**_base_queries(company, row), **_suggested_queries(company, row, llm)}


def _base_queries(company: str, row: pd.Series) -> dict:
    """The fixed main-wave queries, keyed by what each looks for."""
    # Only the identity-sensitive queries take the domain hint. The founder/advisor/customer
    # searches already resolve well on the name alone, and the wave is budget-sensitive: see
    # _ddg_many, where an oversized wave gets throttled into silently empty results.
    hint = _site_hint(row)
    q = f"{company} {hint}".strip() if hint else company
    base = {
        "founders": f"{company} founders co-founder CEO CTO LinkedIn",
        "founder_bg": f"{company} founder previous company career university",
        "advisors": f"{company} advisory board scientific advisor professor",
        "programs": f"{company} accelerator incubator startup program member cohort",
        # Generic membership signals rather than a few brand names, so the evidence surfaces
        # whatever program the startup actually belongs to (YC, Techstars, Antler, ...). The
        # grounding gate (see _program_grounded) requires the program to co-occur with the
        # company in a single result, so naming programs here can't create false positives.
        "corp_programs": f'{q} ("backed by" OR alumni OR cohort OR portfolio OR '
                         f'accelerator OR incubator OR "Y Combinator" OR Techstars)',
        "parent": f"{company} subsidiary parent company acquired part of group",
        "team": f"{q} number of employees company size linkedin",
        # Nothing searched for the founding year or the funding round before, so both were
        # only ever extracted from whatever the other queries happened to return.
        "founded": f"{q} founded year established headquarters",
        "funding": f"{q} funding round raised investors pre-seed seed series",
        "customers": f"{company} customer case study deployment client announcement",
    }
    return base


def _suggested_queries(company: str, row: pd.Series, llm: LLMClient) -> dict:
    """Up to two extra queries from the model, keyed llm_0 / llm_1.

    Separate from the fixed set so research_profile can start searching those while this call is
    still being answered; waiting for it put a whole completion in front of the profile's first
    search."""
    extra: dict = {}
    if llm.available:
        known = " ".join(str(row.get(c, "")) for c in ("short_description", "Your pitch"))[:600]
        prompt = (f"We research the startup '{company}' ({known}). Suggest up to 4 additional web "
                  "search queries that would surface: its founders' backgrounds, scientific/industry "
                  "advisors, membership in incubators/accelerators/corporate startup programs, or a "
                  'corporate parent. Return ONLY JSON: {"queries": ["..."]}')
        data = LLMClient.parse_json(llm.complete(prompt, max_tokens=300, reasoning="none"))
        if data and isinstance(data.get("queries"), list):
            # cap the extras: an oversized wave triggers DuckDuckGo throttling, which
            # silently empties the program/advisor queries
            for i, q in enumerate(data["queries"][:2]):
                if str(q).strip():
                    extra[f"llm_{i}"] = str(q).strip()
    return extra


def _startup_text(row: pd.Series) -> str:
    """The startup's own words from its row: name, description, pitch, business model, customers."""
    return " ".join(str(row.get(c, "")) for c in
                    ("company_name", "short_description", "Your pitch", "Business model",
                     "customers", "Reference customers"))


# ------------------------------------------------------------- programme memberships: grounding
@functools.lru_cache(maxsize=512)
def _word_match(needle: str) -> "re.Pattern":
    """Whole-word matcher for a program name.

    Plain substring matching mis-fires badly on short names: the KNOWN_PROGRAMS key for EIT is
    written ``"eit "`` with a trailing space precisely to avoid that, but this function's
    caller strips the name before comparing — so it degraded to a bare ``"eit" in blob`` and
    matched inside ordinary words (Zeit, arbeit, ...). Meili Robots consequently picked up a
    fabricated "Eit" membership, labelled *corroborated*, which inflated its ecosystem score.

    ``\\b`` is unreliable next to non-word characters (``sap.io``, ``500 global``), so the
    boundaries are asserted only on the sides that actually begin/end with a word character.
    """
    n = needle.strip()
    left = r"(?<!\w)" if n[:1].isalnum() or n[:1] == "_" else ""
    right = r"(?!\w)" if n[-1:].isalnum() or n[-1:] == "_" else ""
    return re.compile(left + re.escape(n) + right, re.I)


def _program_grounded(name: str, company: str, app_text: str,
                      results: dict) -> tuple[str, str] | None:
    """Decide whether a program membership is actually tied to THIS startup.

    Returns ``(source_url, confidence)`` when grounded, or None when it is not:
      * ``corroborated``  -> program and company co-occur in a SINGLE THIRD-PARTY result;
                             returns that result's URL.
      * ``self_asserted`` -> the only support is the company itself — its own fetched site
                             pages (the ``__site__*`` pseudo-results merged by
                             _merge_site_results) or its application text. URL may be ''.
      * ungrounded        -> returns None (caller drops it).

    Matching a program name anywhere in the concatenated corpus is NOT enough: the
    program search query names specific programs, so DuckDuckGo returns generic program
    directory pages that mention many unrelated startups. Requiring the program and the
    company in the same result is what prevents false memberships (e.g. AfterFlow showing
    Nvidia Inception / Microsoft for Startups / Google for Startups it never had).

    The corroborated/self_asserted split matters because a company's own site is evidence
    that it CLAIMS a membership, not that the membership exists. Programs like NVIDIA
    Inception and Microsoft for Startups publish no searchable public member directory, so
    a claim found only on the startup's site is frequently uncheckable. Dropping it loses a
    real signal; presenting it as verified overstates it. Labelling lets the UI show it
    honestly. Third-party corroboration is preferred, so search results are scanned first.
    """
    n = str(name).strip().lower()
    if not n:
        return None
    n_re = _word_match(n)
    if company:
        fallback = None
        for key, hits in results.items():
            for h in hits or []:
                blob = (str(h.get("title", "")) + " " + str(h.get("body", ""))).lower()
                if n_re.search(blob) and company in blob:
                    if str(key).startswith("__site__"):
                        # Remember, but keep scanning: a third-party hit outranks the site.
                        if fallback is None:
                            fallback = h.get("href", "") or ""
                    else:
                        return (h.get("href", "") or "", "corroborated")
        if fallback is not None:
            return (fallback, "self_asserted")
    if n_re.search(app_text):            # self-claim in the startup's own application text
        return ("", "self_asserted")
    return None


def _detect_programs(row: pd.Series, results: dict) -> list[dict]:
    """Keyword scan for KNOWN_PROGRAMS, kept only for programs grounded to THIS startup
    (see _program_grounded). Used offline AND as a safety net alongside LLM extraction,
    so a genuine known-program mention never disappears if the LLM omitted it."""
    company = str(row.get("company_name", "")).strip().lower()
    app_text = _startup_text(row).lower()
    found = []
    for name, ptype in KNOWN_PROGRAMS.items():
        grounded = _program_grounded(name, company, app_text, results)
        if grounded is not None:
            src, conf = grounded
            found.append({"name": name.strip().title(), "type": ptype,
                          "source_url": src, "confidence": conf})
    return found


def _ground_programs(programs: list, row: pd.Series, results: dict) -> list[dict]:
    """Validate an arbitrary program list (e.g. LLM-extracted, which can name ANY program
    worldwide) against the fetched evidence. Keeps only memberships tied to this startup
    and rewrites source_url to the real co-occurring result, so a program the LLM lifted
    from a generic 'top startups' directory page — or invented — is dropped rather than
    trusted. Prioritises correctness over recall: an unverifiable membership is removed."""
    company = str(row.get("company_name", "")).strip().lower()
    app_text = _startup_text(row).lower()
    out, seen = [], set()
    for p in programs or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        key = name.lower()
        if not name or key in seen:
            continue
        grounded = _program_grounded(name, company, app_text, results)
        if grounded is None:
            continue
        src, conf = grounded
        seen.add(key)
        out.append({"name": name,
                    "type": str(p.get("type") or "program").strip() or "program",
                    "source_url": src, "confidence": conf})
    return out


_PROGRAM_NOISE = re.compile(
    r"\b(the|program|programme|accelerator|incubator|startups?|inc|ltd|gmbh)\b", re.I)


def _program_key(name: str) -> str:
    """Canonical identity for a program, so spelling variants collapse to one entry.

    The LLM and the keyword scan name the same membership differently ('NVIDIA Inception
    Program' vs 'Nvidia Inception'), and an exact-string dedup let both through — the profile
    then listed one membership twice and the ecosystem score counted it twice."""
    n = _PROGRAM_NOISE.sub(" ", str(name).lower())
    return re.sub(r"[^a-z0-9]+", "", n)


def _dedupe_programs(programs: list) -> list[dict]:
    """One entry per membership, preferring the independently corroborated spelling."""
    best: dict = {}
    for p in programs or []:
        if not isinstance(p, dict) or not str(p.get("name", "")).strip():
            continue
        key = _program_key(p["name"]) or str(p["name"]).strip().lower()
        cur = best.get(key)
        if cur is None:
            best[key] = p
            continue
        # Corroborated beats self-asserted; otherwise keep whichever already has a source URL.
        if (str(p.get("confidence", "")).lower() == "corroborated"
                and str(cur.get("confidence", "")).lower() != "corroborated"):
            best[key] = p
        elif not str(cur.get("source_url", "")).strip() and str(p.get("source_url", "")).strip():
            best[key] = p
    return list(best.values())


# ------------------------------------------------------------- the main extraction
def _blank(template: dict) -> dict:
    """A copy of ``template`` whose list and dict values are copied one level deep."""
    return {k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in template.items()}


def _offline_extract(row: pd.Series, results: dict) -> dict:
    """Keyword-based fallback without an LLM: an empty profile plus the grounded known programs."""
    prof = _blank(EMPTY_PROFILE)
    prof["programs"] = _detect_programs(row, results)
    prof["method"] = "offline_keyword"
    return prof


def _llm_extract(company: str, row: pd.Series, results: dict, llm: LLMClient) -> dict | None:
    """The main extraction: one call over the corpus, cleaned field by field; None when unreadable.

    Programmes and customers come back ungrounded — research_profile grounds them next."""
    corpus = _corpus(results)
    known = _startup_text(row)[:1200]
    prompt = (
        f"You are researching the startup '{company}'. Below are web search results and what we "
        "already know. Extract a structured profile using ONLY supported facts — leave fields "
        "empty/[] if the evidence does not support them. Never invent names. For every founder, "
        "advisor, program and the parent group, include the source_url of the search result that "
        "supports it.\n\n"
        f"KNOWN:\n{known}\n\nWEB RESULTS:\n{corpus}\n\n"
        "Rules:\n"
        "- reference_customers: NAMED companies/organisations ONLY (e.g. 'Deutsche Bahn', 'Bosch') "
        "that the evidence ties to THIS startup as a customer. NEVER generic descriptions — put "
        "those in customer_segment instead.\n"
        "- customer_segment: if the customers are described by type/scale rather than named "
        "(e.g. '7-8 figure e-commerce brands', 'Fortune 500 manufacturers'), capture that one "
        "short phrase here; leave empty if the customers are named or unknown.\n"
        "- founders: include role AND a specific background (prior companies, roles, university/PhD) "
        "whenever the evidence mentions it; include the LinkedIn URL if present in the results.\n"
        "- employees: a number or tight range (e.g. '25' or '50-100'), not vague words.\n"
        "- hq: the headquarters as 'City, Country' (e.g. 'Munich, Germany'), and ONLY if a "
        "result states where the company is based. Never infer it from a top-level domain, a "
        "language, or an investor's address.\n"
        "- founded_year: 4-digit year only (e.g. '2021'), and ONLY if a result states when the "
        "company was founded/incorporated/started. Never infer it from a copyright notice, a "
        "domain registration date, or the earliest news article.\n"
        "- funding: the most recent round as a short phrase with stage and amount when both are "
        "evidenced (e.g. 'Seed, $2.5M (2024)'). If the stage is evidenced but the amount is NOT "
        "public — Crunchbase renders it as 'obfuscated', or the source says undisclosed — STILL "
        "report the stage, e.g. 'Pre-Seed, amount undisclosed'. Leave empty only when the "
        "evidence names neither a stage nor an amount, and NEVER guess an amount.\n"
        "- hq_source / founded_year_source / funding_source / customer_segment_source: the source_url of the result supporting each — a "
        "real http link from the results, never a label; leave empty if the value came from the "
        "KNOWN block rather than a search result.\n"
        # The SFS judgement used to be asked for here, as one line appended to an extraction
        # prompt. Being a judgement rather than a transcription, it did not belong in a call whose
        # entire instruction is "report only what the evidence states" — and it showed: the answer
        # was true for all 18 stored runs, because "is this a capex-heavy space" is a question
        # about the startup's CUSTOMERS. It is now decided in core/programs.py from the commercial
        # posture this pipeline evidences, against the criteria each SFS line actually underwrites.
        'Return ONLY JSON:\n'
        '{"founders": [{"name":"","role":"","background":"","linkedin":"","source_url":""}],\n'
        ' "key_team": [{"name":"","role":"","source_url":""}],\n'
        ' "advisors": [{"name":"","role":"","affiliation":"","source_url":""}],\n'
        ' "employees": "", "parent_group": "",\n'
        ' "hq": "", "hq_source": "",\n'
        ' "founded_year": "", "founded_year_source": "",\n'
        ' "funding": "", "funding_source": "",\n'
        ' "programs": [{"name":"","type":"incubator|accelerator|corporate_program","source_url":""}],\n'
        ' "reference_customers": [""], "customer_segment": "", "customer_segment_source": ""}'
    )
    data = LLMClient.parse_json(llm.complete(prompt, system="You extract structured company facts "
                                             "strictly from supplied evidence. JSON only.",
                                             max_tokens=1200, reasoning="none"))
    if not data:
        return None
    prof = _blank(EMPTY_PROFILE)
    for key in ("founders", "key_team", "advisors", "programs", "reference_customers"):
        if isinstance(data.get(key), list):
            prof[key] = [x for x in data[key] if x]
    for key in ("founders", "key_team", "advisors"):
        prof[key] = dedupe_people(prof[key])
    founders_first(prof)
    prof["employees"] = str(data.get("employees") or "").strip()
    prof["parent_group"] = str(data.get("parent_group") or "").strip()
    prof["customer_segment"] = str(data.get("customer_segment") or "").strip()
    prof["customer_segment_source"] = (_clean_source_url(data.get("customer_segment_source"))
                                       if prof["customer_segment"] else "")
    hq = _clean_hq(data.get("hq"))
    prof["hq"] = hq
    prof["hq_source"] = _clean_source_url(data.get("hq_source")) if hq else ""
    prof["hq_origin"] = "web" if hq else ""
    # Founded year is only accepted as a bare 4-digit year in a plausible range: the model
    # otherwise happily returns '2021 (est.)', 'circa 2019' or a copyright year, none of which
    # a downstream consumer can treat as a number.
    fy = re.sub(r"\D", "", str(data.get("founded_year") or ""))[:4]
    prof["founded_year"] = fy if len(fy) == 4 and 1800 <= int(fy) <= 2100 else ""
    prof["founded_year_source"] = (_clean_source_url(data.get("founded_year_source"))
                                   if prof["founded_year"] else "")
    # Same bar as the recall net: a round must name a stage or an amount to be a usable fact.
    funding = str(data.get("funding") or "").strip()
    prof["funding"] = funding if has_funding_signal(funding) else ""
    prof["funding_source"] = (_clean_source_url(data.get("funding_source"))
                              if prof["funding"] else "")
    prof["method"] = "llm"
    return prof


# ------------------------------------------------------------- reference customers: grounding
# Words that betray a generic customer *description* (a segment/scale) rather than a named
# company. Such phrases belong in customer_segment, never in reference_customers.
_GENERIC_CUSTOMER = re.compile(
    r"\b(factor(y|ies)|sectors?|industr(y|ies)|companies|clients?|customers?|various|"
    r"several|leading|multiple|enterprises?|manufacturers?|startups?|and more|etc|"
    r"brands?|businesses|firms?|high[- ]?ticket|mid[- ]?market|figure|smbs?|smes?)\b", re.I)

# A genuine customer statement puts the customer, the company, and a relationship phrase
# close together ("ShopSolar uses AfterFlow", "AfterFlow's client Acme"). Requiring all
# three within a short window rejects listicles where both names appear far apart on the
# same page with an unrelated verb elsewhere.
_CUSTOMER_REL = re.compile(
    r"customer|client|case stud|works? with|working with|deployed|deployment|"
    r"partner|trusted by|uses |used by|powered by|helps? ", re.I)
_REL_WINDOW = 140


def _rel_grounded(blob: str, name: str, company: str) -> bool:
    """True if `name`, `company` and a relationship phrase all fall within a short window —
    evidence of an actual customer relationship rather than incidental co-mention."""
    start = 0
    while True:
        i = blob.find(name, start)
        if i == -1:
            return False
        seg = blob[max(0, i - _REL_WINDOW): i + len(name) + _REL_WINDOW]
        if company in seg and _CUSTOMER_REL.search(seg):
            return True
        start = i + 1


def _ground_customers(names: list, row: pd.Series, results: dict) -> list[str]:
    """Keep only customer names actually tied to THIS startup: either the startup self-declared
    the customer in its application row, or a single web result states the relationship with the
    customer name, the company, and a relationship phrase all close together (see _rel_grounded).
    Web-extracted names that merely appear somewhere in the corpus — competitors, investors,
    companies from an unrelated listing, or a name matched to the wrong entity when the startup's
    name is ambiguous (e.g. AfterFlow -> 'ShopSolar.com') — are dropped. Prioritises correctness
    over recall: an unverifiable customer is removed."""
    company = str(row.get("company_name", "")).strip().lower()
    declared = str(row.get("customers", "") or row.get("Reference customers", "")).lower()
    out: list[str] = []
    for name in names or []:
        n = str(name).strip()
        nl = n.lower()
        if not n:
            continue
        grounded = bool(nl) and nl in declared          # self-declared in the application
        if not grounded and company:                    # else: name + company + relationship
            for hits in results.values():               # phrase all close together in a result
                for h in hits or []:
                    blob = (str(h.get("title", "")) + " " + str(h.get("body", ""))).lower()
                    if _rel_grounded(blob, nl, company):
                        grounded = True
                        break
                if grounded:
                    break
        if grounded and n not in out:
            out.append(n)
    return out


def _clean_customers(items: list) -> list[str]:
    """Keep only entries that look like NAMED organisations; drop generic descriptions
    like 'factories in the semiconductor and new energy sectors', and the fragments a prose
    'Reference customers' box splits into ('In parallel', 'Chemical producers (platform …')."""
    out = []
    for c in items or []:
        s = str(c).strip().strip(".")
        if not s or _GENERIC_CUSTOMER.search(s) or not is_named_org(s):
            continue
        if s not in out:
            out.append(s)
    return out


# ------------------------------------------------------------- recall nets, database seeding and Facts
def _recover_founders(prof: dict, company: str, llm: LLMClient) -> None:
    """Safety net mirroring the programs scan: when the main extraction returns ZERO
    founders (throttled queries, truncated corpus, or the LLM simply omitting them),
    run a dedicated founder-only search wave with its own focused extraction call."""
    if prof.get("founders") or not llm.available:
        return
    queries = {
        "f1": f"{company} founders who founded",
        "f2": f"{company} founder CEO co-founder LinkedIn",
        "f3": f"{company} startup team about us founders",
    }
    corpus = _corpus(_ddg_many(queries, max_results=5))
    if not corpus:
        return
    data = LLMClient.parse_json(llm.complete(
        f"Web results about the startup '{company}':\n\n{corpus}\n\n"
        "Extract the FOUNDERS of this company: name, role, specific background (prior "
        "companies/roles, education), LinkedIn URL, and the supporting source_url. Only "
        "people the results clearly identify as founders/co-founders/founding CEO-CTO. "
        "Never invent names; return an empty list if the results name nobody.\n"
        'Return ONLY JSON: {"founders": [{"name":"","role":"","background":"","linkedin":"","source_url":""}]}',
        system="You extract structured facts strictly from supplied evidence. JSON only.",
        max_tokens=700, reasoning="none")) or {}
    found = [f for f in data.get("founders", [])
             if isinstance(f, dict) and str(f.get("name", "")).strip()]
    if found:
        prof["founders"] = dedupe_people(found)
        founders_first(prof)


def _deepen_founders(prof: dict, company: str, llm: LLMClient) -> None:
    """Second research pass: for founders whose background is still empty, run
    person-targeted searches and one LLM call to fill role/background/LinkedIn."""
    thin = [f for f in prof.get("founders", [])
            if isinstance(f, dict) and f.get("name")
            and (not str(f.get("background", "")).strip() or says_nothing(f.get("background"), f.get("name", "")))][:3]
    if not thin or not llm.available:
        return
    queries = {f"f{i}": f"\"{f['name']}\" {company} founder background LinkedIn"
               for i, f in enumerate(thin)}
    corpus = _corpus(_ddg_many(queries, max_results=4))
    if not corpus:
        return
    names = ", ".join(f["name"] for f in thin)
    data = LLMClient.parse_json(llm.complete(
        f"Web results about the founders of '{company}' ({names}):\n\n{corpus}\n\n"
        "For each founder, extract role, a SPECIFIC background (prior companies/roles, "
        "education/PhD), and LinkedIn URL — only what the results support; leave empty otherwise.\n"
        'Return ONLY JSON: {"founders": [{"name":"","role":"","background":"","linkedin":"","source_url":""}]}',
        system="You extract structured facts strictly from supplied evidence. JSON only.",
        max_tokens=700, reasoning="none")) or {}
    updates = {str(f.get("name", "")).strip().lower(): f
               for f in data.get("founders", []) if isinstance(f, dict) and f.get("name")}
    for f in prof["founders"]:
        u = updates.get(str(f.get("name", "")).strip().lower())
        if u:
            if says_nothing(f.get("background"), f.get("name", "")):
                f["background"] = ""
            for k in ("role", "background", "linkedin", "source_url"):
                if not str(f.get(k, "")).strip() and str(u.get(k, "")).strip() \
                        and not (k == "background" and says_nothing(u[k], f.get("name", ""))):
                    f[k] = str(u[k]).strip()


# Headline profile fields the GlassDollar record can answer directly, and the row columns
# that carry them. These are exactly the fields _recover_headline_facts spends a focused
# search wave and an extraction call on.
_DB_SEEDABLE = (
    ("founded_year", ("founded_year",)),
    ("funding", ("funding",)),
    ("employees", ("employees_count", "employee_band")),
    # hq joined the list when Location became a headline tile. GlassDollar answers it directly
    # (glassdollar_api.company_to_row's "hq"), so the database still wins; the recall net and,
    # failing that, _recall_hq_offline only ever fill a blank.
    ("hq", ("hq",)),
)


def _seed_from_database(prof: dict, row: pd.Series) -> set:
    """Take the headline facts from the GlassDollar record BEFORE the recall nets run.

    Two things happen here, and both are the point of preferring GlassDollar.

    Speed: _recover_headline_facts fires its own search wave plus an extraction call for
    founded year, headcount and funding, and it is the slowest and least reliable part of the
    profile chain. GlassDollar holds all three. Populating them here means that pass sees
    them filled and skips itself.

    Precedence: the database wins over the main wave's extraction, not merely over a blank.
    A curated record beats a model reading DuckDuckGo snippets — which CLAUDE.md already
    names as the most fragile thing in the system. The nets still run for whatever GlassDollar
    left empty, so this narrows the web's job rather than replacing it.

    When the database and the web agree, the researched source URL is kept: it corroborates.
    When they disagree the URL is dropped, because it evidences the value that just lost and
    leaving it attached would make the surviving value look sourced by a page that contradicts
    it. Returns the field names that came from the database, so their Facts can say so.

    Deliberately NOT seeded: employees_over_time. _clean_employee_series drops any datapoint
    without an http source, and GlassDollar supplies one current headcount with no URL and no
    history — putting it in the series would be a datapoint the series cannot evidence. It
    goes to the scalar `employees` field only.
    """
    seeded = set()
    for key, cols in _DB_SEEDABLE:
        val = ""
        for col in cols:
            candidate = str(row.get(col, "") or "").strip()
            # pandas renders a missing cell as the string "nan" once it has been through
            # astype(str), which is not a founding year.
            if candidate and candidate.lower() != "nan":
                val = candidate
                break
        if not val:
            continue
        researched = str(prof.get(key, "")).strip()
        if researched and researched != val:
            prof.pop(f"{key}_source", None)
        prof[key] = val
        seeded.add(key)
    return seeded


def _profile_facts(prof: dict, from_db: set | None = None,
                   db_method: str = "glassdollar_db") -> list[Fact]:
    """`from_db` names the headline fields that came from the GlassDollar record rather than
    web research, so their Facts carry that provenance instead of claiming a search found
    them. Confidence is higher for those: a curated database entry is better evidence than a
    model reading an aggregator page, and it is the reason the search wave was skipped."""
    facts: list[Fact] = []
    from_db = from_db or set()

    def add(key, value, src="", origin=""):
        if str(value).strip():
            db = origin and origin in from_db
            facts.append(Fact(key=key, value=str(value)[:300], source_url=src or "",
                              method=db_method if db else "profile_research",
                              confidence=0.8 if db else 0.65,
                              verified=bool(src)))

    for f in prof.get("founders", []):
        if isinstance(f, dict) and f.get("name"):
            add("founder", f"{f.get('name')} — {f.get('role','')} {f.get('background','')}".strip(),
                f.get("source_url", ""))
    for t in prof.get("key_team", []):
        if isinstance(t, dict) and t.get("name"):
            add("key_team", f"{t.get('name')} — {t.get('role','')}".strip(), t.get("source_url", ""))
    for a in prof.get("advisors", []):
        if isinstance(a, dict) and a.get("name"):
            add("advisor", f"{a.get('name')} — {a.get('role','')} {a.get('affiliation','')}".strip(),
                a.get("source_url", ""))
    for p in prof.get("programs", []):
        if isinstance(p, dict) and p.get("name"):
            tier = str(p.get("prestige", "")).strip()
            label = f"{p.get('name')} ({p.get('type', 'program')}"
            label += f", {tier})" if tier else ")"
            if str(p.get("confidence", "")).lower() == "self_asserted":
                label += " — company-claimed, not independently corroborated"
            add("program", label, p.get("source_url", ""))
    add("parent_group", prof.get("parent_group", ""))
    add("founded_year_research", prof.get("founded_year", ""),
        prof.get("founded_year_source", ""), origin="founded_year")
    add("funding_research", prof.get("funding", ""), prof.get("funding_source", ""),
        origin="funding")
    add("employees_research", prof.get("employees", ""), origin="employees")
    # Commercial posture reaches the Evidence tab like everything else, so a reviewer can see the
    # page a Marketplace gate was decided on rather than being handed a verdict.
    commercial = prof.get("commercial") or {}
    add("deployment_model", commercial.get("deployment", ""), commercial.get("deployment_source", ""))
    if commercial.get("has_public_api"):
        add("public_api", "developer documentation / API reference published",
            commercial.get("api_source", ""))
    if commercial.get("pricing_public"):
        add("public_pricing", "price or plan tier published", commercial.get("pricing_source", ""))
    if commercial.get("sells_hardware"):
        add("sells_hardware", "sells physical equipment a buyer takes delivery of",
            commercial.get("hardware_source", ""))
    for cert in commercial.get("certifications", []):
        if isinstance(cert, dict) and cert.get("name"):
            add("certification", cert["name"], cert.get("source_url", ""))
    add("revenue_signal", commercial.get("revenue_signal", ""), commercial.get("revenue_source", ""))
    add("funding_stage", commercial.get("funding_stage", ""))
    for inv in commercial.get("investors", []):
        if isinstance(inv, dict) and inv.get("name"):
            add("investor", inv["name"], inv.get("source_url", ""))
    for e in prof.get("siemens_engagements") or []:
        if isinstance(e, dict):
            add("siemens_relationship", programs_mod.engagement_label(e)
                + (" — company-claimed" if e.get("claimed") else ""), e.get("source_url", ""))
    return facts


def _merge_site_results(results: dict, company: str, row: pd.Series,
                        site: dict | None) -> dict:
    """Fold the company's own fetched pages into the search-results dict as pseudo-hits.

    The grounding gate (_program_grounded / _rel_grounded) requires a program/customer name
    and the company to co-occur in a SINGLE result. A company's own /partners or /ecosystem
    page trivially satisfies "company co-occurs" (it is their site), so we prepend the company
    name to each page's body and use the site URL as href. This lets memberships published
    only on the site be grounded, without weakening the co-occurrence rule for real search
    results. Returns a new dict; the input is not mutated."""
    merged = dict(results or {})
    if not site:
        return merged
    website = str(row.get("website", "") or row.get("domain", "")).strip()
    for i, (path, text) in enumerate(site.items()):
        if not str(text).strip():
            continue
        merged[f"__site__{i}"] = [{
            "title": f"{company} — {path}",
            "body": f"{company} {text}",
            "href": website,
        }]
    return merged


def _recheck_programs(prof: dict, row: pd.Series, company: str,
                      results: dict, llm: LLMClient) -> None:
    """Second-pass recall check for program/ecosystem membership.

    When the first pass found NO grounded programs, an empty result is not yet proof of
    "no memberships" — the ecosystem queries may have been throttled or the membership may
    live on a page DuckDuckGo skipped. Run a dedicated ecosystem/accelerator search wave,
    merge it with whatever site evidence we already have, and re-run the same grounded
    detection. Only memberships tied to THIS startup (co-occurrence) survive, so recall
    improves without sacrificing correctness. No-op when programs already exist."""
    if prof.get("programs"):
        return
    company_l = company.lower()
    queries = {
        "e1": f'{company} ("part of" OR member OR backed OR portfolio) ecosystem',
        "e2": f"{company} accelerator incubator cohort alumni program",
        "e3": f"{company} strategic partner alliance network",
    }
    extra = _ddg_many(queries, max_results=5, overall_timeout=30.0)
    combined = dict(results or {})
    combined.update(extra)
    found = _detect_programs(row, combined)
    if not found and llm.available:
        # Let the LLM name any program the evidence supports, then ground it hard.
        corpus = _corpus(combined)
        if corpus:
            data = LLMClient.parse_json(llm.complete(
                f"Web/company-site results about '{company}':\n\n{corpus}\n\n"
                "List ONLY startup programs, accelerators, incubators, corporate startup "
                "programs, or partner ecosystems that the evidence clearly ties to THIS "
                "company as a member/participant. Never guess; empty list if none.\n"
                'Return ONLY JSON: {"programs":[{"name":"","type":"incubator|accelerator|'
                'corporate_program","source_url":""}]}',
                system="You extract structured facts strictly from supplied evidence. JSON only.",
                max_tokens=500, reasoning="none")) or {}
            found = _ground_programs(data.get("programs", []), row, combined)
    if found:
        # Drop any "membership" that is just the company's own name echoed back.
        prof["programs"] = _dedupe_programs(
            [p for p in found if str(p.get("name", "")).strip().lower() != company_l])


def _recover_headline_facts(prof: dict, company: str, row: pd.Series, llm: LLMClient) -> None:
    """Second-pass recall net for founded_year / employees / funding, like _recover_founders.

    All three live on aggregator pages (LinkedIn "Company size 2-10 employees", CB Insights
    "It was founded in 2026", Crunchbase funding profiles) that rank below the company's own
    pages and drop in and out of a 5-result window between runs. The main wave therefore finds
    them only sometimes, and an empty field is indistinguishable from "the web does not know".
    When one is still blank, spend a focused wave plus one extraction call on it rather than
    declaring it unavailable. Only blank fields are filled; never raises.
    """
    need_year = not str(prof.get("founded_year", "")).strip()
    need_emp = not str(prof.get("employees", "")).strip()
    need_funding = not str(prof.get("funding", "")).strip()
    need_hq = not str(prof.get("hq", "")).strip()
    if not (need_year or need_emp or need_funding or need_hq) or not llm.available:
        return
    hint = _site_hint(row)
    q = f"{company} {hint}".strip() if hint else company
    corpus = _corpus(_ddg_many({
        "h1": f"{q} linkedin company size employees",
        "h2": f"{q} crunchbase pitchbook cbinsights company profile founded",
        "h3": f"{q} founded in year headquarters about the company",
        "h4": f"{q} crunchbase pitchbook funding rounds total raised",
        "h5": f"{q} pre-seed seed series A investment announcement",
    }, max_results=5, overall_timeout=25.0))
    if not corpus:
        return
    # A name-based search for a small startup returns near-namesakes (Phena -> FENA Holdings,
    # Phenna Group, Fena Private Limited), several carrying their own headcount. Without an
    # anchor the model either declines or picks the wrong row, so the company's own
    # description/HQ/website go into the prompt as the identity test.
    known = _startup_text(row)[:600]
    data = LLMClient.parse_json(llm.complete(
        f"Web results about the startup '{company}':\n\n{corpus}\n\n"
        f"THIS COMPANY IS:\nname: {company}\nwebsite: {hint or 'unknown'}\n{known}\n\n"
        "Extract ONLY these facts, strictly from the evidence and only from results that "
        "match THIS company — the results contain other organisations with similar names, and "
        "a fact taken from one of those is worse than no answer:\n"
        "- founded_year: 4-digit year the company was founded/incorporated. NEVER infer it "
        "from a copyright notice, a domain registration, or the date of the earliest article.\n"
        "- employees: a number or tight range exactly as stated (e.g. '25', '2-10'), not a "
        "vague word.\n"
        "- hq: headquarters as 'City, Country', exactly as stated. Never infer it from a "
        "top-level domain or an investor's address.\n"
        "- funding: the most recent round, stage and amount when both are evidenced (e.g. "
        "'Seed, $2.5M (2024)'). If the stage is evidenced but the amount is NOT public — "
        "Crunchbase renders it as 'obfuscated', or the source says undisclosed — STILL report "
        "the stage, e.g. 'Pre-Seed, amount undisclosed'. Leave empty only when not even a "
        "stage is evidenced, and NEVER guess an amount.\n"
        "Give the supporting source_url (a real http link from the results, not a label) for "
        "each. Leave a field empty if unsupported.\n"
        'Return ONLY JSON: {"founded_year":"","founded_year_source":"",'
        '"employees":"","employees_source":"","funding":"","funding_source":"",'
        '"hq":"","hq_source":""}',
        system="You extract structured facts strictly from supplied evidence. JSON only.",
        max_tokens=500, reasoning="none")) or {}
    if need_year:
        fy = re.sub(r"\D", "", str(data.get("founded_year") or ""))[:4]
        if len(fy) == 4 and 1800 <= int(fy) <= 2100:
            prof["founded_year"] = fy
            prof["founded_year_source"] = _clean_source_url(data.get("founded_year_source"))
    if need_emp:
        emp = str(data.get("employees") or "").strip()
        # A bare count or range only — the prompt asks for one, but a model can still answer
        # "a small team", which is not a fact a downstream consumer can use.
        if emp and re.fullmatch(r"[\d,]+(\s*[-–]\s*[\d,]+)?\+?", emp):
            prof["employees"] = emp
    if need_funding:
        fund = str(data.get("funding") or "").strip()
        # Must name a stage or an amount; "raised funding" on its own is not a fact.
        if fund and has_funding_signal(fund):
            prof["funding"] = fund
            prof["funding_source"] = _clean_source_url(data.get("funding_source"))
    if need_hq:
        hq = _clean_hq(data.get("hq"))
        if hq:
            prof["hq"] = hq
            prof["hq_source"] = _clean_source_url(data.get("hq_source"))
            prof["hq_origin"] = "web"


def _recall_hq_offline(prof: dict, company: str, row: pd.Series, llm: LLMClient) -> None:
    """Last resort for headquarters: ask the model what it already knows, with no evidence.

    This is a deliberate, narrow exception to the rule that verifiable fields never come from
    model memory (CLAUDE.md; core/data.py's web_profile_row refuses to do this). Headquarters is
    now a headline tile on the profile, and GlassDollar leaves it blank often enough that the
    tile was empty on runs where the company's location is not actually in doubt. The exception
    is contained three ways: it runs ONLY after the database and both web passes have come back
    empty, the value is stamped ``hq_origin='llm'`` so the UI labels it unverified rather than
    web-sourced, and it carries no source_url, so provenance grades it as inferred and the
    scorer gives it no credit. Every other verifiable field keeps the original rule.
    """
    if str(prof.get("hq", "")).strip() or not llm.available:
        return
    hint = _site_hint(row)
    data = LLMClient.parse_json(llm.complete(
        f"Where is the startup '{company}'"
        + (f" (website {hint})" if hint else "")
        + " headquartered?\n"
        "Answer from your own knowledge. Return the city and country as 'City, Country'.\n"
        "If you are not confident you are thinking of THIS company, or you do not know, return "
        'an empty string — a wrong location is much worse than none.\n'
        'Return ONLY JSON: {"hq":""}',
        system="You answer with a place name or nothing. JSON only.",
        max_tokens=60, reasoning="none")) or {}
    hq = _clean_hq(data.get("hq"))
    if hq:
        prof["hq"] = hq
        prof["hq_source"] = ""
        prof["hq_origin"] = "llm"


# ------------------------------------------------------------- commercial posture
_DEPLOYMENTS = ("cloud", "edge", "on_prem", "hardware")
_REVENUE_SIGNALS = ("none", "customers", "contracted", "recurring")
# Certifications the Xcelerator Marketplace governance actually names, plus the two adjacent ones
# a startup is most likely to hold instead. Anything the model returns outside this set is dropped:
# "GDPR compliant" and "enterprise-grade security" are marketing, not certifications.
_KNOWN_CERTS = {
    "iso 27001": "ISO/IEC 27001", "iso/iec 27001": "ISO/IEC 27001", "iso27001": "ISO/IEC 27001",
    "iec 62443": "IEC 62443", "iso 62443": "IEC 62443", "iec62443": "IEC 62443",
    "soc 2": "SOC 2", "soc2": "SOC 2", "soc 2 type ii": "SOC 2",
    "iso 9001": "ISO 9001", "tisax": "TISAX", "iso 27017": "ISO/IEC 27017",
    "iso 27018": "ISO/IEC 27018", "cyber essentials": "Cyber Essentials",
}


def _commercial_corpus(site: dict | None, web: dict | None, company: str) -> str:
    """Evidence block for the commercial extraction: the startup's own commercial pages plus the
    enrichment searches that asked the same questions. Site text is capped per page here for the
    same reason _corpus caps it — one /docs page is longer than every search snippet combined."""
    lines: list[str] = []
    for path, text in (site or {}).items():
        if path in _COMMERCIAL_PATHS and str(text).strip():
            lines.append(f"[site{path}] {company} :: {str(text)[:_CORPUS_LINE_CHARS]}")
    for key in _COMMERCIAL_QUERIES:
        for hit in (web or {}).get(key, []) or []:
            lines.append(f"[{key}] {hit.get('title','')} :: {hit.get('body','')} "
                         f":: {hit.get('href','')}"[:_CORPUS_LINE_CHARS])
    return "\n".join(lines)[:_CORPUS_CHARS]


def _clean_certifications(items) -> list[dict]:
    """Keep only recognised certifications, canonically spelled, each with a real source URL."""
    out, seen = [], set()
    for c in items or []:
        if isinstance(c, dict):
            raw, src = str(c.get("name", "")), _clean_source_url(c.get("source_url"))
        else:
            raw, src = str(c), ""
        canonical = _KNOWN_CERTS.get(re.sub(r"[^a-z0-9 /]", "", raw.lower()).strip())
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        out.append({"name": canonical, "source_url": src})
    return out


def _extract_commercial_posture(prof: dict, company: str, row: pd.Series,
                                site: dict | None, web: dict | None, llm: LLMClient) -> None:
    """Fill ``prof['commercial']`` from evidence already gathered; never raises, never searches.

    Deliberately no search wave of its own. `enrich` now asks for pricing, certifications, APIs,
    deployment, marketplace listings and investors in its existing single wave, and `fetch_site_text`
    already pulls the company's own /pricing, /security, /docs and /api pages — so the evidence is
    in hand and this costs exactly one completion.

    The funding stage is parsed deterministically from the funding string the rest of the pipeline
    already grounded, and only falls to the model when that string is absent: re-deriving a fact
    already in hand would add a failure mode to recover nothing.
    """
    commercial = _blank(EMPTY_PROFILE["commercial"])
    prof["commercial"] = commercial

    # Deterministic first — this needs no model and cannot drift between runs.
    commercial["funding_stage"] = parse_funding_stage(
        prof.get("funding") or row.get("funding") or "")

    corpus = _commercial_corpus(site, web, company)
    # The grounded list is final before the recall pool starts (research_profile), so the model
    # labels exactly the names a reviewer sees — it is never asked for customers of its own.
    customers = [str(c) for c in prof.get("reference_customers") or []][:15]
    segment = str(prof.get("customer_segment") or "").strip()
    if not corpus or not llm.available:
        commercial["method"] = "unavailable" if not llm.available else "no_evidence"
        return

    data = LLMClient.parse_json(llm.complete(
        f"Evidence about the company '{company}' — its own web pages and search results:\n\n"
        f"{corpus}\n\n"
        "Report its COMMERCIAL POSTURE using ONLY what this evidence states. Leave a field at its "
        "empty/false default when the evidence does not address it — 'not stated' is a valid and "
        "useful answer here, and a guess is not.\n"
        "- deployment: how the product runs. 'cloud' (hosted/SaaS), 'edge' (on-device or on-prem "
        "gateway), 'on_prem' (installed in the customer's datacentre), 'hardware' (the product IS "
        "physical equipment the customer takes delivery of). Empty if unclear.\n"
        "- has_public_api: true ONLY if the evidence shows developer documentation, an API "
        "reference, or an SDK. A page saying 'integrates with X' is not an API.\n"
        "- certifications: security or quality certifications the company states it HOLDS "
        "(ISO/IEC 27001, IEC 62443, SOC 2, TISAX, ISO 9001). Not 'GDPR compliant', not "
        "'enterprise-grade security' — those are claims, not certifications.\n"
        "- pricing_public: true ONLY if a price, a plan tier, or a published rate card is visible. "
        "'Contact us for a quote' is false.\n"
        "- sells_hardware: true if the company sells physical equipment, devices, machines or "
        "instruments that a buyer takes delivery of. Software that RUNS ON hardware is false.\n"
        "- revenue_signal: 'recurring' (subscriptions/SaaS contracts evidenced), 'contracted' "
        "(named multi-year or framework agreements), 'customers' (named paying customers, no "
        "contract terms stated), 'none'.\n"
        "- investors: named funds or institutional investors, NOT individuals unless the evidence "
        "calls them the lead.\n"
        "- revenue: the company's OWN revenue. status 'amount' only with a stated figure; "
        "'pre_revenue' only if the evidence says so explicitly. quote = the exact words from the "
        "evidence containing the figure, copied verbatim. metric = revenue | arr | mrr | "
        "run_rate | turnover | gmv | bookings | estimate | projection — funding, valuation, market "
        "size, a customer's revenue and third-party estimates (Growjo, Owler, Zoominfo) are NOT "
        "revenue. growth_quote = verbatim words stating revenue growth, if any. Leave all empty "
        "if not stated.\n"
        + (f"- customer_classes: for EACH of these already-identified customers — {customers} — "
           "relation to the company (customer | pilot | partner | investor | supplier) and size "
           "(large_enterprise = a well-known multinational or major public body; sme = anything "
           "smaller). Use only these names, spelled as given.\n" if customers else "")
        + (f"- segment_grade: the company describes its customers as '{segment}'. level 1 = a "
           "customer type only ('chemical producers'); 2 = type plus scale or count ('50+ "
           "mid-size manufacturers'); 3 = type plus a quantified top-tier claim ('3 of the top-10 "
           "chemical producers'). quote = the verbatim words.\n" if segment else "")
        + "Give a source_url — a real http link from the evidence, never a label — for every "
        "non-empty field.\n"
        'Return ONLY JSON: {"deployment":"","deployment_source":"","has_public_api":false,'
        '"api_source":"","certifications":[{"name":"","source_url":""}],"pricing_public":false,'
        '"pricing_source":"","sells_hardware":false,"hardware_source":"","revenue_signal":"",'
        '"revenue_source":"","investors":[{"name":"","source_url":""}],'
        '"revenue":{"status":"","quote":"","metric":"","fiscal_year":"","growth_quote":"",'
        '"source_url":""},"customer_classes":[{"name":"","relation":"","size":""}],'
        '"segment_grade":{"level":0,"quote":"","source_url":""}}',
        system="You extract structured company facts strictly from supplied evidence. JSON only.",
        max_tokens=1300, reasoning="none")) or {}
    if not data:
        commercial["method"] = "no_answer"
        return

    deployment = str(data.get("deployment") or "").strip().lower().replace("-", "_")
    if deployment in _DEPLOYMENTS:
        commercial["deployment"] = deployment
        commercial["deployment_source"] = _clean_source_url(data.get("deployment_source"))

    # A boolean claim with no URL is exactly the "public claim without a link" that provenance.py
    # demotes to `inferred`, and these three drive a hard Marketplace gate, so an uncited true is
    # not carried at all. The company's own site counts: _commercial_corpus labels those lines
    # [site/path] and the model returns the site URL for them.
    for flag, src_key in (("has_public_api", "api_source"),
                          ("pricing_public", "pricing_source"),
                          ("sells_hardware", "hardware_source")):
        url = _clean_source_url(data.get(src_key))
        if bool(data.get(flag)) and url:
            commercial[flag] = True
            commercial[src_key] = url

    commercial["certifications"] = _clean_certifications(data.get("certifications"))

    revenue = str(data.get("revenue_signal") or "").strip().lower()
    if revenue in _REVENUE_SIGNALS and revenue != "none":
        url = _clean_source_url(data.get("revenue_source"))
        if url:
            commercial["revenue_signal"] = revenue
            commercial["revenue_source"] = url

    investors, seen = [], set()
    for inv in data.get("investors") or []:
        name = str(inv.get("name", "") if isinstance(inv, dict) else inv).strip()
        if not name or len(name) > 60 or name.lower() in seen:
            continue
        seen.add(name.lower())
        investors.append({"name": name,
                          "source_url": _clean_source_url(
                              inv.get("source_url") if isinstance(inv, dict) else "")})
    commercial["investors"] = investors[:8]
    commercial["revenue"] = _clean_revenue(data.get("revenue"), corpus)
    prof["customer_classes"] = _clean_customer_classes(data.get("customer_classes"), customers)
    prof["customer_segment_grade"] = _clean_segment_grade(data.get("segment_grade"), corpus) \
        if segment else {}
    commercial["method"] = "llm"


def _verbatim(quote, corpus: str, min_len: int = 12) -> str:
    """The quote, if it really appears in the evidence; otherwise ''.

    The traction rubric scores what a quote *says*, so a paraphrase is not good enough: a model
    asked for a revenue figure will round, convert and occasionally invent one. Whitespace and
    case are normalised because snippets are, and nothing else is.
    """
    q = re.sub(r"\s+", " ", str(quote or "")).strip()
    if len(q) < min_len:
        return ""
    return q if q.casefold() in re.sub(r"\s+", " ", corpus).casefold() else ""


_REVENUE_METRICS = ("revenue", "arr", "mrr", "run_rate", "turnover", "gmv", "bookings",
                    "estimate", "projection")
_GROWTH_WORDS = {"doubled": 100.0, "tripled": 200.0, "quadrupled": 300.0}


def _clean_revenue(raw, corpus: str) -> dict:
    """A revenue statement the traction rubric may read, or {} — never a figure without its words."""
    if not isinstance(raw, dict):
        return {}
    status = str(raw.get("status") or "").strip().lower()
    url = _clean_source_url(raw.get("source_url"))
    quote = _verbatim(raw.get("quote"), corpus, 6 if status == "pre_revenue" else 12)
    if status not in ("amount", "pre_revenue") or not url or not quote:
        return {}
    metric = str(raw.get("metric") or "revenue").strip().lower().replace("-", "_").replace(" ", "_")
    if status == "amount" and is_revenue_estimate(quote, url):
        metric = "estimate"                  # kept and shown with its source, never scored as revenue
    year = re.sub(r"\D", "", str(raw.get("fiscal_year") or ""))[:4]
    out = {"status": status, "quote": quote, "metric": metric if metric in _REVENUE_METRICS else "estimate",
           "fiscal_year": year if len(year) == 4 else "", "growth_pct": None, "source_url": url}
    growth = _verbatim(raw.get("growth_quote"), corpus, 6)
    if growth:
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%", growth)
        word = next((v for k, v in _GROWTH_WORDS.items() if k in growth.lower()), None)
        out["growth_pct"] = float(pct.group(1)) if pct else word
    return out


def _clean_customer_classes(raw, customers: list) -> list[dict]:
    """Labels for grounded customers only. A name the model adds, renames or merges is dropped."""
    allowed = {c.casefold(): c for c in customers}
    out, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = allowed.get(str(item.get("name") or "").strip().casefold())
        relation = str(item.get("relation") or "").strip().lower()
        size = str(item.get("size") or "").strip().lower()
        if not name or name in seen or relation not in (
                "customer", "pilot", "partner", "investor", "supplier"):
            continue
        seen.add(name)
        out.append({"name": name, "relation": relation,
                    "size": size if size in ("large_enterprise", "sme") else "sme", "by": "llm"})
    return out


def _clean_segment_grade(raw, corpus: str) -> dict:
    """A 1–3 level with the words it was read from; a free-form number is not a level."""
    if not isinstance(raw, dict):
        return {}
    level, url = raw.get("level"), _clean_source_url(raw.get("source_url"))
    quote = _verbatim(raw.get("quote"), corpus, 6)
    if isinstance(level, bool) or level not in (1, 2, 3) or not url or not quote:
        return {}
    return {"level": int(level), "quote": quote, "source_url": url}


# ------------------------------------------------------------- programme prestige
def _program_tier_offline(name: str) -> str:
    """Deterministic prestige tier from the known-program map; unknown -> tier3."""
    n = str(name).lower()
    for key, tier in KNOWN_PROGRAM_TIERS.items():
        if key and key in n:
            return tier
    return "tier3"


def _grade_programs(programs: list, company: str, llm: LLMClient) -> None:
    """Annotate each program with a prestige ``tier`` (tier1/tier2/tier3) in place.

    Every program first gets a deterministic tier from KNOWN_PROGRAM_TIERS (unknown -> tier3),
    so scoring is stable even offline. When the LLM is available it re-grades by global
    reputation (tier1 = top-tier / Siemens-run, tier3 = generic local), but it may ONLY set
    the tier — it can never add, drop, or rename a membership, so grading can't fabricate
    credibility. Invalid/absent LLM tiers keep the deterministic baseline."""
    if not programs:
        return
    for p in programs:
        if isinstance(p, dict):
            p["prestige"] = _program_tier_offline(str(p.get("name", "")))
    if not llm.available:
        return
    named = [str(p.get("name", "")).strip() for p in programs
             if isinstance(p, dict) and str(p.get("name", "")).strip()]
    if not named:
        return
    listing = "; ".join(named)
    data = LLMClient.parse_json(llm.complete(
        f"Rate the prestige of these startup programs that '{company}' belongs to: {listing}.\n"
        "Tiers: tier1 = globally top-tier accelerator/program or run by a major corporate "
        "(e.g. Y Combinator, Techstars, Siemens Xcelerator, Startup Autobahn, Intel Ignite); "
        "tier2 = well-known but broad-access (e.g. Microsoft/Google for Startups, Plug and "
        "Play, Antler); tier3 = regional/generic/unknown. Judge by the program's reputation, "
        "not this company.\n"
        'Return ONLY JSON: {"tiers": {"<program name>": "tier1|tier2|tier3"}}',
        system="You grade startup-program prestige. JSON only.", max_tokens=300,
        reasoning="none")) or {}
    tiers = data.get("tiers") or {}
    if not isinstance(tiers, dict):
        return
    lut = {str(k).strip().lower(): str(v).strip().lower() for k, v in tiers.items()}
    for p in programs:
        if not isinstance(p, dict):
            continue
        t = lut.get(str(p.get("name", "")).strip().lower())
        if t in ("tier1", "tier2", "tier3"):
            p["prestige"] = t


# ------------------------------------------------------------- headcount history
def _clean_employee_series(points: list) -> list[dict]:
    """Sanitise raw {year, count, source_url} points into a trustworthy time series.

    Correctness over recall: a point is kept ONLY when it has a plausible year
    (2000..current+1), a positive integer headcount, and an http(s) source_url — an
    uncited number is dropped rather than guessed. Duplicated years collapse to one
    (highest count wins) and the result is sorted ascending. Fewer than TWO cited points
    returns [] so the UI can honestly show 'insufficient data' instead of a misleading
    single dot or a fabricated line."""
    max_year = datetime.date.today().year + 1
    by_year: dict[int, dict] = {}
    for p in points or []:
        if not isinstance(p, dict):
            continue
        src = str(p.get("source_url", "")).strip()
        if not src.startswith("http"):
            continue
        try:
            year = int(str(p.get("year", "")).strip()[:4])
            count = int(float(str(p.get("count", "")).strip().replace(",", "")))
        except (ValueError, TypeError):
            continue
        if year < 2000 or year > max_year or count <= 0:
            continue
        prev = by_year.get(year)
        if prev is None or count > prev["count"]:
            by_year[year] = {"year": year, "count": count, "source_url": src}
    series = [by_year[y] for y in sorted(by_year)]
    return series if len(series) >= 2 else []


def _too_young(row: pd.Series) -> bool:
    """A company that started this calendar year cannot produce the two distinct annual points
    _clean_employee_series requires, so the headcount wave can only ever return []. Skipping is
    not a heuristic about what is "probably" worth trying — it is the same arithmetic the gate
    downstream applies. It also returns three searches and a model call to the shared budget,
    which is what the companies that CAN answer are being starved of."""
    try:
        founded = int(str(row.get("founded_year", "") or "")[:4])
    except (TypeError, ValueError):
        founded = 0
    return bool(founded) and datetime.date.today().year - founded < 1


def _history_wave(company: str, row: pd.Series, llm: LLMClient):
    """The headcount search wave alone, as (hits, stats), or None when it would not run.

    Separate from the extraction because it needs nothing but the company's name: research_profile
    starts it beside the base wave instead of after the main extraction, where it used to queue.
    No overall_timeout override. This used to pass 18s — under half _ddg_many's default — for
    queries issued in the recovery block, after the main wave, while four other pipeline stages
    are also searching. That is the most throttled moment of a run, and web.py's own docstring
    warns that too tight a deadline turns throttling into a silently empty result. Run alone the
    same queries return a clean four-point series for Uber; inside a full run they returned nothing.
    """
    if not llm.available or _too_young(row):
        return None
    queries = {
        "h1": f"{company} number of employees 2021 2022 2023 2024 headcount growth",
        "h2": f"{company} linkedin employees company size over time",
        "h3": f"{company} crunchbase employee count history",
    }
    stats: dict = {}
    try:
        extra = _ddg_many(queries, max_results=5, stats=stats)
    except Exception:
        extra = {}
    return extra, stats


def _employee_history(company: str, row: pd.Series, results: dict,
                      llm: LLMClient, wave=None) -> tuple[list[dict], str]:
    """Best-effort headcount-over-time series, every point backed by a source URL.

    Runs a small dedicated wave of historical-headcount queries, then asks the LLM to pull
    ONLY year+count pairs the evidence supports, each with the supporting URL. The result is
    passed through _clean_employee_series, so anything uncited or implausible is discarded and
    a series with <2 cited points collapses to []. Never raises.

    ``wave`` is that search wave already started (a future of _history_wave's result); without
    it the wave runs here.

    Returns (series, status); see EMPTY_PROFILE for what the statuses mean. An empty list on
    its own cannot distinguish "this company has no history to find" from "we never managed to
    look", and the second was being reported to reviewers as the first."""
    if not llm.available:
        return [], "unavailable"
    if _too_young(row):
        return [], "too_young"
    extra, stats = (_ready(wave) if wave is not None else None) or _history_wave(company, row, llm) or ({}, {})
    combined = dict(results or {})
    combined.update(extra)
    corpus = _corpus(combined)
    if not corpus:
        # Abandoned in flight is not the same fact as "the web knows nothing about this".
        return [], ("unavailable" if stats.get("timed_out") else "not_found")
    data = LLMClient.parse_json(llm.complete(
        f"Web results about the headcount of the startup '{company}':\n\n{corpus}\n\n"
        "Extract the number of EMPLOYEES per YEAR, using ONLY figures the evidence states "
        "for this company. For each data point give the calendar year, the employee count as "
        "an integer, and the source_url of the result that supports it. Never estimate or "
        "interpolate; omit any year you cannot cite. Return an empty list if none are cited.\n"
        'Return ONLY JSON: {"employees_over_time":[{"year":2023,"count":42,"source_url":""}]}',
        system="You extract structured facts strictly from supplied evidence. JSON only.",
        max_tokens=600, reasoning="none")) or {}
    series = _clean_employee_series(data.get("employees_over_time", []))
    if series:
        return series, "ok"
    return [], ("unavailable" if stats.get("timed_out") else "not_found")


# ------------------------------------------------------------- public profile links
# Legal and vanity suffixes that show up in a profile slug but not in the company's own name, or
# the other way round. Stripped from BOTH sides before comparing, so "bliro" matches "bliro-gmbh"
# and "makkook-ai" matches "Makkook AI".
_SLUG_SUFFIXES = ("gmbh", "ug", "ag", "inc", "llc", "ltd", "limited", "bv", "nv", "oy", "ab",
                  "sa", "srl", "spa", "plc", "co", "corp", "company", "group", "holding",
                  "holdings", "technologies", "technology", "tech", "labs", "lab", "io", "ai",
                  "app", "hq", "official", "global", "international")

# Where a public company profile lives, and how its slug is spelled in the path.
_LINK_SOURCES = (
    ("linkedin_url", "linkedin.com", re.compile(r"/company/([A-Za-z0-9_.\-]+)"),
     "https://www.linkedin.com/company/{}/"),
    ("crunchbase_url", "crunchbase.com", re.compile(r"/organization/([A-Za-z0-9_.\-]+)"),
     "https://www.crunchbase.com/organization/{}"),
)

# Hosts that are never the company's own site, so a matching name in one of them is a directory
# entry rather than a homepage.
_DIRECTORY_HOSTS = ("linkedin.", "crunchbase.", "wikipedia.", "facebook.", "twitter.", "x.com",
                    "youtube.", "instagram.", "bloomberg.", "pitchbook.", "growjo.", "getlatka.",
                    "cbinsights.", "tracxn.", "dealroom.", "glassdoor.", "indeed.", "medium.",
                    "github.", "producthunt.", "angel.co", "wellfound.")

# "Company size 11-50 employees" as LinkedIn renders it in a search snippet. The trailing word is
# required: the same snippets lead with a follower count, and a follower is not an employee.
_SIZE_BAND = re.compile(r"(\d[\d,]*(?:\s*[-\u2013]\s*\d[\d,]*)?\+?)\s*employees", re.I)


def _identity_forms(text: str) -> set[str]:
    """Every compact spelling one name can reasonably take, for comparing against a URL slug."""
    words = _norm(text).split()
    compact = "".join(words)
    if not compact:
        return set()
    forms = {compact}
    while len(words) > 1 and words[-1] in _SLUG_SUFFIXES:
        words = words[:-1]
        forms.add("".join(words))
    # A single run-together token keeps its suffix too ("makkookai" -> "makkook"). The length
    # guard is what stops "sonio" being read as "son".
    for suffix in _SLUG_SUFFIXES:
        if len(compact) > len(suffix) + 3 and compact.endswith(suffix):
            forms.add(compact[:-len(suffix)])
    return {f for f in forms if len(f) >= 3}


def _iter_hits(result_maps) -> list[dict]:
    """Every hit across the supplied {query_key: hits} maps, in the order they were collected."""
    hits: list[dict] = []
    for results in result_maps:
        for bucket in (results or {}).values():
            for hit in bucket or []:
                if isinstance(hit, dict):
                    hits.append(hit)
    return hits


def _extract_links(company: str, row: pd.Series, *result_maps) -> dict:
    """LinkedIn / Crunchbase / website URLs, from search results already in hand.

    Deterministic: no model, no extra search. These URLs are sitting in the result sets the run
    has already paid for — `web_profile_row` literally walks past them, skipping every social
    result while it guesses the website — and nothing ever recorded them, so the profile header
    showed a blank LinkedIn row for every company that is not in GlassDollar.

    The grounding bar is `_program_grounded`'s: a near-namesake's LinkedIn page is worse than an
    empty field, so the slug has to BE the company rather than merely mention it. Identity is the
    company name and its own domain label, both reduced to their compact forms.
    """
    identities = _identity_forms(company)
    hint = _site_hint(row)
    if hint:
        identities |= _identity_forms(hint.split(".")[0])
    if not identities:
        return {}

    out: dict = {}
    hits = _iter_hits(result_maps)
    for key, host, slug_re, canonical in _LINK_SOURCES:
        for hit in hits:
            href = str(hit.get("href", ""))
            if host not in href.lower():
                continue
            match = slug_re.search(href)
            if not match:
                continue
            slug = match.group(1).rstrip(".")
            if _identity_forms(slug.replace("-", " ").replace("_", " ")) & identities:
                out[key] = canonical.format(slug)
                out[f"{key}_origin"] = "web"
                break

    # The company's own site, for a database row whose website column is blank. Same identity
    # test, applied to the host rather than to a slug, with the directories excluded.
    if not str(row.get("website", "") or "").strip():
        for hit in hits:
            href = str(hit.get("href", ""))
            if not href.lower().startswith(("http://", "https://")):
                continue
            netloc = (urlparse(href).hostname or "").lower()
            if not netloc or any(d in netloc for d in _DIRECTORY_HOSTS):
                continue
            label = netloc[4:] if netloc.startswith("www.") else netloc
            if _identity_forms(label.split(".")[0]) & identities:
                out["website"] = f"https://{label}"
                break

    # LinkedIn's own headcount band, kept as a band and kept apart from the cited count. It is
    # the figure a reviewer sees when they open LinkedIn and find it disagreeing with the
    # aggregator the run cited, and reporting it explicitly is more honest than either silently
    # preferring one or pretending the discrepancy is not there.
    for hit in hits:
        if "linkedin.com" not in str(hit.get("href", "")).lower():
            continue
        band = _SIZE_BAND.search(f"{hit.get('title', '')} {hit.get('body', '')}")
        if band:
            out["linkedin_size_band"] = band.group(1).strip()
            out["linkedin_size_source"] = out.get("linkedin_url") or _clean_source_url(hit.get("href"))
            break
    return out


def _recall_links_offline(prof: dict, company: str, row: pd.Series, llm: LLMClient) -> None:
    """Last resort for the LinkedIn / Crunchbase URLs: ask the model what it already knows.

    The second sanctioned exception to "no verifiable field comes from model memory", added on
    the same terms as _recall_hq_offline and contained the same three ways: it runs ONLY after
    the database and _extract_links have both come back empty, the value is stamped
    ``*_origin='llm'`` so the UI labels it unverified rather than web-sourced, and it carries no
    source URL, so it is never treated as evidence.

    The extra containment these two fields need, which headquarters does not: a URL is a claim
    that a page EXISTS, and a near-namesake's profile page is worse than a blank row. So a
    recalled URL is not taken as written — it must parse as a real profile path on the right
    host, and its slug must satisfy the same identity test `_extract_links` applies to a
    searched result. The canonical form is rebuilt from the slug, so the model's own string
    never reaches the profile.
    """
    missing = [(key, host, slug_re, canonical) for key, host, slug_re, canonical in _LINK_SOURCES
               if not str(prof.get(key, "")).strip()]
    if not missing or not llm.available:
        return
    identities = _identity_forms(company)
    hint = _site_hint(row)
    if hint:
        identities |= _identity_forms(hint.split(".")[0])
    if not identities:
        return
    data = LLMClient.parse_json(llm.complete(
        f"The startup '{company}'" + (f" (website {hint})" if hint else "") + ":\n"
        "From your own knowledge, give its official company profile URLs.\n"
        "Return the full URL for each, or an empty string. If you are not confident you are "
        "thinking of THIS company, or you do not know the exact profile path, return an empty "
        "string — a link to the wrong company is much worse than no link.\n"
        "Never invent a path from the company name.\n"
        'Return ONLY JSON: {"linkedin_url":"","crunchbase_url":""}',
        system="You answer with URLs you actually know, or nothing. JSON only.",
        max_tokens=120, reasoning="none")) or {}
    for key, host, slug_re, canonical in missing:
        href = str(data.get(key) or "").strip()
        if host not in href.lower():
            continue
        match = slug_re.search(href)
        if not match:
            continue
        slug = match.group(1).rstrip(".")
        if _identity_forms(slug.replace("-", " ").replace("_", " ")) & identities:
            prof[key] = canonical.format(slug)
            prof[f"{key}_origin"] = "llm"


# ------------------------------------------------------------- an existing Siemens relationship
# A startup that already works with Siemens — or builds on Siemens products — is a stronger
# Collaborate case (core/pillars.with_relationship_bonus). Detection is transcription, like the rest
# of this module, and deterministic: no model reads these passages. The bar is _rel_grounded's —
# Siemens, the company and an engagement phrase together in ONE result — plus two vetoes for the
# false positives "Siemens" attracts in startup coverage: a founder's CV ("previously at Siemens")
# and a comparison ("an alternative to Siemens' ...").
_SIEMENS_TYPES = (           # first match wins, most specific first
    ("investment", re.compile(r"invest|backed by|funding from|funded by|led by next47|portfolio compan", re.I)),
    ("pilot", re.compile(r"\bpilot|proof[- ]of[- ]concept|\bpoc\b", re.I)),
    ("joint_project", re.compile(r"joint (?:project|development|research)|co-?develop|collaborat|research project", re.I)),
    ("programme", re.compile(r"xcelerator|accelerator|incubator|cohort|startup program|selected for|challenge winner", re.I)),
    ("supplier", re.compile(r"supplier|supplies|supplying", re.I)),
    ("partner", re.compile(r"partner|alliance|reseller|distribut", re.I)),
    ("customer", re.compile(r"customer|client|deployed|deployment|rolled out|\buses\b|\busing\b|works? with|"
                            r"working with|contract|trusted by|chose|selected", re.I)),
)
_PRODUCT_USE = re.compile(r"built on|integrat|\buses\b|\busing\b|runs on|compatible with|certified|"
                          r"connector|plug-?in|app for|add-?on|extension for|interfaces? with|powered by", re.I)
_NOT_ENGAGEMENT = re.compile(r"former|previously|\bex-|worked (?:at|for)|career|years at|joined from|"
                             r"competitor|competes|alternative|\bvs\.?(?!\w)|versus|compared (?:to|with)|rival", re.I)
_SIEMENS_ANCHOR_WINDOW = 60  # the vetoes read only around the Siemens mention, not the whole passage
# Siemens and the company as two items of one list — "alongside SAP, Siemens, Wandelbots" — say they
# were named together, not that they work together. A comma is what marks the list: "Acme and Siemens
# announce a partnership" is two parties and stays in.
_LISTED_TOGETHER = re.compile(r"^[\s,;/&]*(?:(?:and\s+)?[\w.\-]+[\s,;/&]+){0,4}(?:and\s+)?$")
# A founding year is not the engagement's year ("since its foundation in 2017, ... Next47").
_FOUNDED_YEAR = re.compile(r"(?:found(?:ed|ation)|established|since|incorporated)\D{0,25}$", re.I)
_SIEMENS_QUERIES = {
    "sr_engage": "{c} Siemens partnership OR pilot OR customer OR collaboration",
    "sr_invest": '{c} next47 OR "Siemens Financial Services" OR "Siemens Xcelerator"',
    "sr_units": '{c} "Siemens Mobility" OR "Smart Infrastructure" OR "Digital Industries" OR Healthineers',
    "sr_products": '{c} Siemens integration Teamcenter OR Simcenter OR SIMATIC OR "Insights Hub" OR Mendix',
}


@functools.lru_cache(maxsize=2)
def _siemens_products(_catalog_checksum: str) -> tuple:
    """(compiled product-name matcher, {name lower: (display name, tool id)}) from the tool catalog."""
    from .catalogs import tools_catalog
    names: dict = {}
    for e in tools_catalog().get("entries") or []:
        name = str(e.get("name", "")).strip()
        if len(name) >= 2:
            names.setdefault(name.lower(), (name, str(e.get("id", ""))))
    for brand in programs_mod.SIEMENS_BRANDS:
        names.setdefault(brand, (brand.title(), ""))
    alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.I), names


def _product_matcher() -> tuple:
    """`_siemens_products` for the current tool catalog, or the curated brands when it is missing."""
    try:
        from .catalogs import tools_catalog
        return _siemens_products(str(tools_catalog().get("checksum", "")))
    except Exception:                          # no catalog: the curated brands still count
        names = {b: (b.title(), "") for b in programs_mod.SIEMENS_BRANDS}
        return re.compile(rf"(?<!\w)(?:{'|'.join(map(re.escape, names))})(?!\w)", re.I), names


_SIEMENS_ANCHOR = re.compile(r"siemens|healthineers|next47|"
                             + "|".join(re.escape(b) for b in programs_mod.SIEMENS_BRANDS))


def _listed_together(passage: str, anchor: "re.Match", company: str) -> bool:
    """True when the company mention nearest to this Siemens mention is just another list item."""
    starts = [k.start() for k in re.finditer(re.escape(company), passage)]
    if not starts:
        return False
    j = min(starts, key=lambda k: abs(k - anchor.start()))
    gap = passage[j + len(company): anchor.start()] if j < anchor.start() else passage[anchor.end(): j]
    return "," in gap and len(gap) <= 60 and bool(_LISTED_TOGETHER.match(gap))


def _siemens_in(passage: str, original: str, company: str, own_site: bool, url: str) -> list[dict]:
    """Engagements one lower-cased passage states between the company and Siemens."""
    matcher, products = _product_matcher()
    out = []
    for m in _SIEMENS_ANCHOR.finditer(passage):
        i = m.start()
        seg = passage[max(0, i - _REL_WINDOW): i + _REL_WINDOW]
        if company not in seg:
            continue
        if _NOT_ENGAGEMENT.search(passage[max(0, i - _SIEMENS_ANCHOR_WINDOW): i + _SIEMENS_ANCHOR_WINDOW]):
            continue
        if _listed_together(passage, m, company):
            continue
        unit = programs_mod.siemens_unit(seg)
        product = None
        for pm in matcher.finditer(seg):
            key = pm.group(0).lower()
            if key in programs_mod.SIEMENS_BRANDS or "siemens" in seg:
                product = products.get(key)
                break
        if product and _PRODUCT_USE.search(seg):
            kind = "uses_product"
        else:
            kind = next((k for k, rx in _SIEMENS_TYPES if rx.search(seg)), "")
            product = None
        if not kind or (unit is None and product is None):
            continue
        unit = unit or ("Siemens", "")
        near_lo = max(0, i - _SIEMENS_ANCHOR_WINDOW)
        year = next((y for y in re.finditer(r"(?<!\d)20[0-3]\d(?!\d)", passage[near_lo: i + _SIEMENS_ANCHOR_WINDOW])
                     if not _FOUNDED_YEAR.search(passage[max(0, near_lo + y.start() - 40): near_lo + y.start()])),
                    None)
        start = max(0, i - _REL_WINDOW)
        out.append({"unit": unit[0], "department": unit[1], "type": kind,
                    "product": product[0] if product else "", "tool_id": product[1] if product else "",
                    "year": year.group(0) if year else "",
                    "quote": re.sub(r"\s+", " ", original[start: i + _REL_WINDOW]).strip()[:280],
                    "source_url": url if str(url).startswith("http") else "", "claimed": own_site})
    return out


# ---- the team record: founders and external validation, searched once and reused ----------------

_TEAM_LISTS = ("founders", "key_team", "advisors", "programs", "investors", "partners")


def _recover_investors(prof: dict, company: str, llm: LLMClient) -> None:
    """A dedicated investor wave, for the team section's Refresh. Same bar as the commercial
    extraction's investors (named funds or institutions, each with its URL), plus co-occurrence:
    an investor counts only where one result names it with the company."""
    if not llm.available:
        return
    hits = _ddg_many({"i1": f"{company} investors funding round led by",
                      "i2": f"{company} backed by venture capital seed series",
                      "i3": f"{company} raises investment from"}, max_results=5)
    corpus = _corpus(hits)
    if not corpus:
        return
    data = LLMClient.parse_json(llm.complete(
        f"Web results about the startup '{company}':\n\n{corpus}\n\n"
        "List the INVESTORS in this company: named funds or institutional investors, NOT individuals "
        "unless the evidence calls them the lead. Each with the source_url of the result that names it. "
        "Never invent; empty list if none.\n"
        'Return ONLY JSON: {"investors":[{"name":"","source_url":""}]}',
        system="You extract structured facts strictly from supplied evidence. JSON only.",
        max_tokens=500, reasoning="none")) or {}
    blobs = [(str(h.get("title", "")) + " " + str(h.get("body", ""))).lower()
             for found in hits.values() for h in found or []]
    out = []
    for inv in data.get("investors") or []:
        name = str(inv.get("name", "") if isinstance(inv, dict) else inv).strip()
        url = _clean_source_url(inv.get("source_url") if isinstance(inv, dict) else "")
        if name and len(name) <= 60 and url and any(name.lower() in b and company.lower() in b for b in blobs):
            out.append({"name": name, "source_url": url})
    prof["investors"] = out


def refresh_team(company: str, row: pd.Series, llm: LLMClient) -> dict:
    """The founders, programmes and investors searched again from scratch.

    A full evaluation reuses what earlier runs found (``research_profile(known_team=...)``) instead
    of searching for them again; this is the one place they are searched on request. Lists only —
    whoever stores it merges it with what was already known (``merge_team``)."""
    prof: dict = {"founders": [], "programs": [], "investors": []}
    web_team: dict = {}
    jobs = [functools.partial(_recover_founders, prof, company, llm),
            functools.partial(_recheck_programs, prof, row, company, {}, llm),
            functools.partial(_recover_investors, prof, company, llm),
            lambda: web_team.update(search_team_web(company, str(row.get("website") or ""), llm))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        for fut in [ex.submit(contextvars.copy_context().run, fn) for fn in jobs]:
            try:
                fut.result()
            except Exception:
                pass
    try:
        _deepen_founders(prof, company, llm)
    except Exception:
        pass
    found = {k: prof.get(k) or [] for k in ("founders", "programs", "investors")}
    return merge_team(found, web_team)


# ------------------------------------------------------------- team & ecosystem from web search
# The evaluation reads founders and programmes off search passages: two 200-character windows per
# result. A founder's education and earlier employers sit elsewhere on the page, and a cohort page
# carries the programme in its heading and the company far down its list, so _program_grounded's
# "both in one result" never saw them together. Radical Dot showed two founders with no
# background and one programme, while the web names the CEO's Cambridge PhD and Systemiq years and
# four programmes (XPRENEURS, Circular Valley, BMW Foundation RESPOND, TUM Venture Labs ChemSPACE).
#
# So the team is also looked up the way the funding lookup works: the model's own web search, then
# an extraction naming the page behind each item. Every item is then checked against that page,
# fetched in full: a founder's page must name the person and the company and carry the background
# (_supported); a programme or partner must stand near the company on a page, or be the cohort page
# that lists it. A page that cannot be fetched supports nothing. LinkedIn URLs are never taken from
# here: the search itself reports it cannot verify a profile's address, and a wrong one sends a
# reviewer elsewhere.
# Two searches, run side by side: one asked for founders, programmes and partners at once ran past
# the 90s web-search timeout on one of two tries and answered nothing; each half answers in ~60s.
_TEAM_WEB_PROMPTS = (
    "Search the web for {company}{site}. List every founder and co-founder with their role, education "
    "and previous employers. After each fact put the URL of the page it comes from. Say only what the "
    "pages state.",
    "Search the web for {company}{site}. List every accelerator, incubator, startup programme, cohort, "
    "award, prize or competition it is part of or won, and its research, university or corporate "
    "partners (not investors). After each fact put the URL of the page it comes from. Say only what "
    "the pages state.")
_TEAM_WEB_JSON = ('{"founders":[{"name":"","role":"","background":"","source_urls":[]}],'
                  '"programs":[{"name":"","type":"accelerator|incubator|program|award|competition","source_urls":[]}],'
                  '"partners":[{"name":"","type":"university|research|corporate","source_urls":[]}]}')
_NEAR = 1500            # characters between a programme or partner and the company on one page


def search_team_web(company: str, website: str, llm: LLMClient) -> dict:
    """{founders, programs, partners} found by the model's web search, each held by its own page."""
    if not (llm and llm.available and company):
        return {}
    site = f" ({website})" if website else ""

    def ask(prompt):
        # One retry: a web search that timed out or failed answers nothing at all.
        for _ in range(2):
            answer = llm.web_answer(prompt.format(company=company, site=site), max_tokens=2000)
            if answer and str(answer.get("text") or "").strip():
                return str(answer["text"])
        return ""
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(_TEAM_WEB_PROMPTS)) as ex:
        # The caller's context, copied per search: it carries the usage log and the cache bypass.
        futures = [ex.submit(contextvars.copy_context().run, ask, pr) for pr in _TEAM_WEB_PROMPTS]
        notes = "\n\n".join(t for t in (f.result() for f in futures) if t)
    if not notes:
        return {}
    data = LLMClient.parse_json(llm.complete(
        f"Research notes about the startup '{company}':\n\n{notes}\n\n"
        "Extract the founders, the programmes and the partners these notes state, each with every URL "
        "the notes give for it (a founder's background often comes from a different page than the role). "
        "Background: education and previous employers in one short line, only as the notes state them. "
        "Programme names as the programme calls itself (e.g. \"XPRENEURS\", not \"XPRENEURS Batch 15\"). "
        "One partner per entry, named as the page names it. Investors are not partners. Leave out "
        "anything without a URL.\n"
        "Return ONLY JSON: " + _TEAM_WEB_JSON,
        system="You extract structured facts strictly from supplied evidence. JSON only.",
        max_tokens=1500, reasoning="none"))
    if not isinstance(data, dict):
        return {}
    urls = {u for k in ("founders", "programs", "partners") for i in data.get(k) or [] for u in _urls_of(i)}
    pages = _read_pages(urls)
    own = _host(website)
    name = company.casefold()
    out: dict = {"founders": [], "programs": [], "partners": []}
    for f in data.get("founders") or []:
        person = str(f.get("name") or "").strip() if isinstance(f, dict) else ""
        # A founder needs a page naming them at this company. Their background may come from another
        # page that names them in full — an alumni story or an employer's profile seldom names the
        # startup (Kremer's HEC Paris profile does not) — and that page is linked as its source.
        named = [(u, pages[u].casefold()) for u in _urls_of(f) if pages.get(u)]
        named = [(u, page) for u, page in named if person and _names_in(person, page)]
        held = [(u, page) for u, page in named if name in page]
        if not held:
            continue
        entry = {"name": person, "role": "", "background": "", "linkedin": "", "source_url": held[0][0]}
        role = str(f.get("role") or "").strip()
        if role and any(all(w in page for w in re.findall(r"[a-z]{3,}", role.casefold())) for _, page in held):
            entry["role"] = role
        background = str(f.get("background") or "").strip()
        backed = next((u for u, page in held + named if background and _carried(background, page)), "")
        if backed:
            entry["background"], entry["background_source"] = background, backed
        out["founders"].append(entry)
    for key in ("programs", "partners"):
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("name") or "").strip()
            url = next((u for u in _urls_of(item) if pages.get(u) and label and label.casefold() != name
                        and _tied(label, name, pages[u].casefold(), u)), "")
            if not url:
                continue
            entry = {"name": label, "type": str(item.get("type") or ("program" if key == "programs" else "partner")).strip(),
                     "source_url": url}
            if key == "programs":
                # The company's own pages claim a membership; anyone else's corroborate it (score.py
                # discounts the claim).
                entry["confidence"] = "self_asserted" if own and _host(url) == own else "corroborated"
                entry["prestige"] = _program_tier_offline(label)
            if not any(_program_key(e["name"]) == _program_key(label) for e in out[key]):
                out[key].append(entry)
    return out


def _urls_of(item) -> list[str]:
    """An extracted item's http source URLs (``source_urls`` and ``source_url``), deduplicated."""
    if not isinstance(item, dict):
        return []
    raw = list(item.get("source_urls") or []) + [item.get("source_url")]
    return [u for u in dict.fromkeys(_clean_source_url(x) for x in raw if isinstance(x, str)) if u]


def _read_pages(urls) -> dict:
    """{url: visible text} for each page that could be fetched (web._fetch_with_url, SSRF-guarded)."""
    from . import web
    urls = [u for u in dict.fromkeys(urls) if str(u).startswith("http")][:12]
    if not urls:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as ex:
        futures = {u: ex.submit(contextvars.copy_context().run, web._fetch_with_url, u, 6.0) for u in urls}
        out = {}
        for u, fut in futures.items():
            try:
                out[u] = str(fut.result()[0] or "")
            except Exception:
                pass
    return out


def _host(url: str) -> str:
    """A URL's host without a leading www."""
    url = str(url or "")
    host = urlparse(url if "//" in url else "https://" + url).hostname or ""
    return host.removeprefix("www.")


def _names_in(person: str, page: str) -> bool:
    """Every part of the person's name (diacritics folded, honorifics dropped) appears on the page."""
    def fold(text):
        text = unicodedata.normalize("NFKD", str(text))
        return "".join(c for c in text if not unicodedata.combining(c)).casefold()
    words = set(re.findall(r"[a-z0-9]+", fold(page)))
    return all(part in words for part in person_key(person).split() if len(part) > 1)


def _carried(claim: str, page: str) -> bool:
    """The business flow's bar: half the claim's content words, and every number in it, on the page."""
    from .business_flow import _supported
    return _supported(claim, page)


def _tied(label: str, company: str, page: str, url: str = "") -> bool:
    """The page is the programme's own and names the company, or the programme stands within _NEAR
    characters of the company.

    A cohort page puts the programme in its address and the company down its list: UnternehmerTUM's
    XPRENEURS Batch 15 page has the name after 1,700 characters of site menu and Radical Dot 1,860
    characters further on. Being first on a page is not enough — page text cannot tell a heading
    from an article that opens on "Y Combinator funds many startups" and lists the company later."""
    if company not in page:
        return False
    slug = re.sub(r"[^a-z0-9]", "", label.casefold())
    if len(slug) >= 5 and slug in re.sub(r"[^a-z0-9]", "", str(url).casefold()):
        return True
    pattern = _word_match(label.casefold())
    spots = [m.start() for m in pattern.finditer(page)]
    if not spots:
        return False
    at = [m.start() for m in re.finditer(re.escape(company), page)]
    return any(abs(a - b) <= _NEAR for a in spots for b in at)


# A background that says only that there is none: the evaluation's extraction wrote "Dr. Andreas
# Wagner; no prior companies or roles are specified in the results." for Radical Dot's CEO. It is
# shown as nothing, and a found background replaces it.
_SAYS_NOTHING = re.compile(r"\b(?:not (?:specified|stated|mentioned|available|provided|given|found|disclosed)|"
                           r"no (?:prior|previous|further|other|specific) (?:compan|role|information|detail|background)|"
                           r"unknown|n/a)", re.I)


_HONORIFICS = {"dr", "prof", "mr", "mrs", "ms", "sir", "dame", "herr", "frau"}


def says_nothing(background, name: str = "") -> bool:
    """Filler, or nothing but the person's own name ("Dr. Andreas Wagner" was his "background")."""
    def words(text):
        text = "".join(c for c in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(c))
        return set(re.findall(r"[a-z0-9]+", text.casefold()))
    text = str(background or "")
    if _SAYS_NOTHING.search(text):
        return True
    return bool(text.strip()) and not (words(text) - words(name) - _HONORIFICS)


def _fill_person(known: dict, found: dict) -> None:
    """What a later record knows about a person the earlier one listed thinly: a background the
    earlier one left blank, and a role that only adds to the earlier one ("Co-founder" becomes
    "Co-founder and CEO", but a different role never replaces one). Each carries its own source."""
    if says_nothing(known.get("background"), known.get("name", "")):
        known["background"] = ""
    if not str(known.get("background") or "").strip() and str(found.get("background") or "").strip() \
            and not says_nothing(found.get("background"), found.get("name", "")):
        known["background"] = str(found["background"]).strip()
        if found.get("background_source"):
            known["background_source"] = found["background_source"]
    old_role, new_role = str(known.get("role") or "").strip(), str(found.get("role") or "").strip()
    if new_role and len(new_role) > len(old_role) and old_role.casefold() in new_role.casefold():
        known["role"] = new_role
    for k in ("linkedin", "source_url"):
        if not str(known.get(k) or "").strip() and str(found.get(k) or "").strip():
            known[k] = found[k]


def merge_team(base: dict, *more: dict) -> dict:
    """``base``'s lists first and in their order, then what each later record adds.

    The run's own lists lead because Team & Ecosystem cites their entries by position; anything
    only a refresh found is appended, so those positions never move. An entry is the same entry by
    the person (``same_person``), the programme (``_program_key``) or the investor's name. Entries
    only the last record adds are marked ``new``."""
    out: dict = {}
    for key in _TEAM_LISTS:
        merged = [dict(i) for i in base.get(key) or [] if isinstance(i, dict)]
        for person in merged if key in ("founders", "key_team", "advisors") else []:
            if says_nothing(person.get("background"), person.get("name", "")):
                person["background"] = ""
        for n, record in enumerate(more):
            for item in (record or {}).get(key) or []:
                if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                    continue
                if key in ("founders", "key_team", "advisors"):
                    same = next((m for m in merged if same_person(item, m)), None)
                    known = same is not None
                    if known:
                        _fill_person(same, item)
                elif key == "programs":
                    known = any(_program_key(item.get("name", "")) == _program_key(m.get("name", "")) for m in merged)
                else:
                    known = any(str(item["name"]).strip().lower() == str(m.get("name", "")).strip().lower() for m in merged)
                if not known:
                    item = {**item, "new": n == len(more) - 1 and len(more) > 1}
                    if key in ("founders", "key_team", "advisors") and says_nothing(item.get("background"), item.get("name", "")):
                        item["background"] = ""
                    merged.append(item)
        out[key] = merged
    return out


# ------------------------------------------------------------- the Siemens relationship, assembled
def _siemens_as_customer(prof: dict) -> None:
    """Siemens among the reference customers when the relationship wave found it as one.

    Reference customers come from the general waves only, so Siemens reached them only when one of
    those happened to return a page tying it to the company — the model's suggested queries and the
    day's DuckDuckGo results decided, and Humanoid lost a customer the Siemens wave had found with
    two Siemens press releases. A `customer` engagement with an http source already met the
    `_ground_customers` bar: the company, Siemens and a customer phrase together in one result.
    Pilots, partners and programmes are not customers and stay in the relationship only."""
    customer = any(e.get("type") == "customer" and str(e.get("source_url", "")).startswith("http")
                   for e in prof.get("siemens_engagements") or [])
    names = prof.setdefault("reference_customers", [])
    if customer and not any(str(n).strip().lower().startswith("siemens") for n in names):
        names.append("Siemens")


def _siemens_relationship(prof: dict, company: str, row: pd.Series, *result_maps) -> list[dict]:
    """Every engagement between the company and Siemens the run's evidence states, deduplicated.

    Reads each search result (and the company's own pages, which arrive as ``__site__*`` pseudo-
    results and count as ``claimed``), the grounded investors and programmes, and the application
    text. An application-only mention has no link, so it is listed but cannot earn the Collaborate
    bonus — every engagement that does carries the URL a reviewer can open.
    """
    name = company.strip().lower()
    if not name:
        return []
    own_host = _site_hint(row)
    found: list[dict] = []
    for results in result_maps:
        for key, hits in (results or {}).items():
            for h in hits or []:
                if not isinstance(h, dict):
                    continue
                href = str(h.get("href", ""))
                own = str(key).startswith("__site__") or bool(own_host and own_host in href.lower())
                text = f"{h.get('title', '')} {h.get('body', '')}"
                found += _siemens_in(text.lower(), text, name, own, href)
    for inv in (prof.get("commercial") or {}).get("investors") or []:
        unit = programs_mod.siemens_unit(str(inv.get("name", "")).lower()) if isinstance(inv, dict) else None
        if unit and str(inv.get("source_url", "")).startswith("http"):
            found.append({"unit": unit[0], "department": unit[1], "type": "investment", "product": "",
                          "tool_id": "", "year": "", "quote": f"Investor: {inv.get('name')}",
                          "source_url": inv["source_url"],
                          "claimed": bool(own_host and own_host in inv["source_url"].lower())})
    for prog in prof.get("programs") or []:
        unit = programs_mod.siemens_unit(str(prog.get("name", "")).lower()) if isinstance(prog, dict) else None
        if unit and str(prog.get("source_url", "")).startswith("http"):
            found.append({"unit": unit[0], "department": unit[1], "type": "programme", "product": "",
                          "tool_id": "", "year": "", "quote": f"Programme: {prog.get('name')}",
                          "source_url": prog["source_url"],
                          "claimed": str(prog.get("confidence", "")).lower() == "self_asserted"})
    app = _startup_text(row)
    found += _siemens_in(app.lower(), app, name, True, "")
    # One entry per (unit, type, product): a linked, independent source first, then a linked claim.
    found.sort(key=lambda e: (not e["source_url"], e["claimed"]))
    out, seen = [], set()
    for e in found:
        k = (e["unit"], e["type"], e["product"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def _search_siemens(company: str) -> dict:
    """The relationship wave: four queries, run beside the recall nets."""
    return _ddg_many({k: q.format(c=company) for k, q in _SIEMENS_QUERIES.items()}, max_results=5)


# ------------------------------------------------------------- entry point
def _ready(value):
    """A value the caller may still be computing: a future is waited for, anything else is used as is."""
    return value.result() if hasattr(value, "result") else value


def research_profile(row: pd.Series, llm: LLMClient, do_web: bool = True,
                     site=None, web=None, on_progress=None, known_team: dict | None = None) -> dict:
    """Return {'profile': {...}, 'facts': [Fact...]}; never raises.

    ``site`` is the optional {path: text} map of the company's OWN pages already fetched
    during enrichment (web.fetch_site_text). Folding it into the evidence lets the recall
    check ground ecosystem/program memberships that live only on the site and were never
    indexed by DuckDuckGo.

    ``web`` is enrichment's {query_key: hits} map. It is passed in rather than re-searched
    because enrich already asks the commercial-posture questions in its single wave, so the
    evidence exists by the time this runs; re-issuing those queries here would double the
    searches to fetch results already in memory.

    Either may be a future. The pipeline starts this branch the moment the company is resolved,
    while enrichment is still fetching both, and each is waited for only where it is first read:
    ``site`` before extraction, ``web`` after it. Waiting for them at the start put enrichment's
    whole search wave and site fetch in front of the profile's own.

    ``on_progress(snapshot)`` receives a copy, shaped like the return value, once the main extraction is grounded and seeded
    from the database, before the recall nets run: the header facts are usually all there by
    then, and the nets take as long again. It is a snapshot; the returned profile only adds to it."""
    company = str(row.get("company_name", "")).strip()
    if not company:
        return {"profile": dict(EMPTY_PROFILE), "facts": []}
    try:
        # Uses _ddg_many's default budget, which is sized for the full wave; a tighter
        # deadline here silently empties the program/advisor queries under throttling.
        # 5 results, not 4: the aggregator pages that actually carry headcount and founding
        # year (LinkedIn's "Company size 2-10 employees", CB Insights) routinely rank fifth
        # behind the company's own pages, so a 4-result window cut them off.
        # The fixed queries go out at once; the model's suggestions are searched when it answers.
        # Same queries, same results and the same key order as one wave of both, so the evidence
        # (and every prompt built from it) is unchanged — only the wait in front of it is gone.
        results = {}
        # Started with the base wave, not after the main extraction where they used to queue: the
        # headcount and Siemens waves read nothing but the company's name, and the headcount
        # extraction reads only the merged results. Same queries, same results, same prompts.
        early = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        history_wave = siemens = history = None
        try:
            if do_web:
                history_wave = early.submit(contextvars.copy_context().run, _history_wave, company, row, llm)
                siemens = early.submit(contextvars.copy_context().run, _search_siemens, company)
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    extra = ex.submit(contextvars.copy_context().run, lambda: _ddg_many(
                        _suggested_queries(company, row, llm), max_results=5))
                    results = _ddg_many(_base_queries(company, row), max_results=5)
                    results.update(extra.result())
            site = _ready(site)
            # Fold the company's own site text in as pseudo-results so the grounding gate
            # (name + company must co-occur in one result) can see facts published only there.
            results = _merge_site_results(results, company, row, site)
            if do_web:
                history = early.submit(contextvars.copy_context().run, _employee_history,
                                       company, row, results, llm, history_wave)
        finally:
            early.shutdown(wait=False)
        prof = None
        if llm.available:
            prof = _llm_extract(company, row, results, llm)
        if prof is None:
            prof = _offline_extract(row, results)
        else:
            # Ground the LLM's programs against the evidence (it may name any program
            # worldwide, but each membership must be tied to THIS startup), then union
            # with the KNOWN_PROGRAMS keyword scan so a known program never vanishes
            # because the LLM skipped it or the corpus was truncated.
            prof["programs"] = _dedupe_programs(
                _ground_programs(prof.get("programs", []), row, results)
                + _detect_programs(row, results))
        # Named-companies-only filter + grounding (a web-extracted name must co-occur with
        # the company or be self-declared), then backfill from the DB row if research found
        # none. DB-declared customers are trusted, so the backfill needs no grounding.
        prof["reference_customers"] = _ground_customers(
            _clean_customers(prof["reference_customers"]), row, results)
        if not prof["reference_customers"]:
            raw = str(row.get("customers", "") or row.get("Reference customers", ""))
            prof["reference_customers"] = _clean_customers(re.split(r"[,\n;·|]+", raw))
        # Public identifiers, read off the results both waves already returned. Free, so it runs
        # before the recall nets decide what still needs a search of its own.
        web = _ready(web)
        prof.update(_extract_links(company, row, results, web))
        # GlassDollar first: seed the headline facts it already holds so the recall net below
        # skips them. This is what makes an API-sourced evaluation faster than a web-only one.
        seeded = _seed_from_database(prof, row)
        # A row carrying a glassdollar_id came from the live REST API; one without it came
        # from the shipped application export. Both are GlassDollar, but only one of them is
        # the startup writing about itself, and provenance.py grades them differently.
        # A Tracxn-seeded row is Tracxn's record, not GlassDollar's.
        if str(row.get("tracxn_id", "")).strip():
            db_method = "tracxn_mcp"
        elif str(row.get("glassdollar_id", "")).strip() not in ("", "nan"):
            db_method = "glassdollar_api"
        else:
            db_method = "glassdollar_db"
        if on_progress is not None:
            # A copy: the recall nets below keep writing into `prof` while the caller reads this.
            try:
                snapshot = copy.deepcopy(prof)
                on_progress({"profile": snapshot, "facts": _profile_facts(snapshot, seeded, db_method)})
            except Exception:
                pass
        # ---- second-pass recall nets ------------------------------------------------------
        # Four independent passes, each firing its own search wave plus one extraction call:
        # the headline facts (founded_year / employees), founder recovery, the program recheck,
        # and the headcount series. Run sequentially they dominated the evaluation — ~29s of
        # the ~67s profile chain — yet they touch DISJOINT profile fields, so they overlap
        # safely and the wall time collapses to the slowest one. Each decides for itself
        # whether it is needed (all no-op when their field is already populated), and each is
        # individually best-effort: one failure must never cost the profile.
        siemens_hits: dict = {}
        if do_web:
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                # Pool threads start with an empty context, so each job runs inside a copy of
                # this one — that is what carries core.web's cache-bypass flag into the search
                # calls these passes make.
                def _spawn(fn, *a):
                    return ex.submit(contextvars.copy_context().run, functools.partial(fn, *a))

                # Founders and programmes an earlier run already found are not searched for again:
                # carry_forward brings them in with their sources and last-confirmed dates, and the
                # team section's Refresh (refresh_team) is where they are searched on request.
                known = known_team or {}
                jobs = {
                    "headline": _spawn(_recover_headline_facts, prof, company, row, llm),
                    "founders": None if known.get("founders") else _spawn(_recover_founders, prof, company, llm),
                    "programs": None if known.get("programs") else _spawn(
                        _recheck_programs, prof, row, company, results, llm),
                    "history": history,
                    # Costs one completion and no searches, so it rides along in the pool it does
                    # not lengthen. It reads `prof['funding']`, which _llm_extract has already set
                    # and _seed_from_database has already overridden — but not what the headline
                    # net may still find, so the stage is re-derived after the pool joins.
                    "commercial": _spawn(_extract_commercial_posture, prof, company, row,
                                         site, web, llm),
                    # Searches only, started above; read after the pool, once programmes and
                    # investors are final.
                    "siemens": siemens,
                }
                for name, fut in jobs.items():
                    try:
                        out = fut.result() if fut is not None else None
                    except Exception:
                        out = None
                    if name == "history":
                        series, status = out if isinstance(out, tuple) else ([], "unavailable")
                        prof["employees_over_time"] = series
                        prof["employees_history_status"] = status
                    elif name == "siemens":
                        siemens_hits = out or {}
        # Re-derive the stage from whatever funding string finally survived. The headline recall
        # net runs in the same pool as the commercial extraction, so a round it recovers lands
        # after that extraction has already read the field. Deterministic and free, so it simply
        # runs again rather than the two passes being ordered against each other.
        prof.setdefault("commercial", _blank(EMPTY_PROFILE["commercial"]))
        prof["commercial"]["funding_stage"] = parse_funding_stage(prof.get("funding", ""))
        # The last four passes all follow the recall nets and none follows another. Each writes
        # its own fields — programme tiers, founder backgrounds, headquarters, the two profile
        # URLs — and reads none the others write, so they run together; one after another they
        # were four round trips at the end of the slowest branch.
        #   - Grading must follow the program recheck: it grades whatever that found. In place;
        #     it never adds or removes a program.
        #   - The founder deep-dive fills thin backgrounds, so it must follow founder recovery.
        #   - The two model-recall exceptions run only if the database and every web pass left
        #     their field blank, and stamp *_origin='llm' so nothing downstream can mistake the
        #     value for evidence. A completion apiece, and only on a run that would otherwise
        #     show a blank row.
        try:
            prof["siemens_engagements"] = _siemens_relationship(prof, company, row, results, web, siemens_hits)
        except Exception:
            prof["siemens_engagements"] = []
        _siemens_as_customer(prof)
        tail = [functools.partial(_grade_programs, prof.get("programs", []), company, llm),
                functools.partial(_recall_hq_offline, prof, company, row, llm),
                functools.partial(_recall_links_offline, prof, company, row, llm)]
        if do_web and not (known_team or {}).get("founders"):
            tail.append(functools.partial(_deepen_founders, prof, company, llm))
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tail)) as ex:
            for fut in [ex.submit(contextvars.copy_context().run, fn) for fn in tail]:
                try:
                    fut.result()
                except Exception:
                    pass
        return {"profile": prof, "facts": _profile_facts(prof, seeded, db_method)}
    except Exception:
        return {"profile": dict(EMPTY_PROFILE), "facts": []}
