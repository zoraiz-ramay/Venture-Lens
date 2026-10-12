"""Databases: the files the engine reads, browsable read-only by a signed-in reviewer.

The Databases page lists each source the engine matches startups against — what it is, what it
feeds, its file, row count, modified time and (where core/catalogs.py computes one) the checksum
every assessment records — and pages through its rows with a server-side search.

What leaves the server is decided here, per source, by an **allow-list of columns**, never a
deny-list: a column added to a workbook stays invisible until someone names it below. Three
sources reuse the catalog loaders the pillars read (core/catalogs.py), so a reviewer sees exactly
the entries a run was matched against, and the applications workbook is read only through
`applications_catalog`, which already leaves out `status`, `contact_*` and `Referral` (an
application's internal status is never shown, at the product owner's request, and a contact
person is personal data). `submission_title` ("Information provided by <name>") and
`pdf_local_path` are not in that catalog either.

Free text is the second hole: applicants paste signatures into their pitch ("Tel: +49 …",
"name@company.com"), and a reviewer typing a problem can do the same. So every cell also passes
`_scrub`, which removes e-mail addresses, phone numbers and — for the applications — the names
the workbook records as the application's contact person. Redaction, not omission: the pitch is
the most useful column the workbook has.

No route here returns a file's bytes, and nothing writes: a reviewer reads, the files are owned
by whoever ships them.
"""
from __future__ import annotations

import functools
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from api.auth import Principal, current_user
from core import catalogs

router = APIRouter(prefix="/api/sources", tags=["sources"])

PAGE_DEFAULT, PAGE_MAX, QUERY_MAX = 25, 100, 200

# Columns per source: (key, label). The key is what a row carries; nothing else is ever put in one.
APPLICATION_COLUMNS = (
    ("company", "Company"), ("website", "Website"), ("short_description", "Description"),
    ("stage", "Development stage"), ("partnership", "Partnership sought"),
    ("siemens_functions", "Siemens functions"), ("business_model", "Business model"),
    ("reference_customers", "Reference customers"), ("hq", "HQ"), ("founded", "Founded"),
    ("employees", "Employees"), ("funding", "Funding"), ("customers", "Customers"),
    ("pitch", "Pitch"), ("differentiation", "Differentiation"),
)
TOOL_COLUMNS = (("name", "Tool"), ("category", "Category"), ("division", "Division"),
                ("description", "Description"), ("url", "URL"))
SELLER_COLUMNS = (("name", "Seller"), ("industries", "Industries"), ("topics", "Topics"),
                  ("motion", "Motion"), ("regions", "Regions"), ("description", "Description"),
                  ("url", "URL"))
NEED_COLUMNS = (("id", "Capability ID"), ("department", "Department"), ("category", "Need category"),
                ("capability", "Capability"), ("description", "Description"), ("keywords", "Keywords"))
# `asked_by`, `reviewer`, `reviewer_oid` (who) and `status` (the innovation team's approval) stay out.
CHALLENGE_COLUMNS = (("problem", "Problem"), ("keywords", "Keywords"), ("created_at", "Recorded"))

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# International numbers ("+49 (0)175 …", "0020 …") anywhere, national ones only after a label
# ("Tel:", "M.", "Mob"): a bare digit run is as often a standard ("IEC 60870-5-104") or an id.
_PHONE_INTL = re.compile(r"(?<![\w+])(?:\+|00)\d[\d \t().\-/]{6,}\d")
_PHONE_LABELLED = re.compile(
    r"(?i)\b(tel|telefon|telephone|phone|mob|mobile|cell|whatsapp|fon|m|t)\b(\.?:?\s{0,3})(\d[\d \t().\-/]{6,}\d)")


def _digits(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


def _scrub(text: str, names: re.Pattern | None = None) -> str:
    text = _EMAIL.sub("[email removed]", text)
    text = _PHONE_INTL.sub(lambda m: "[phone removed]" if _digits(m.group()) >= 8 else m.group(), text)
    text = _PHONE_LABELLED.sub(
        lambda m: m.group(1) + m.group(2) + "[phone removed]" if _digits(m.group(3)) >= 7 else m.group(), text)
    return names.sub("[contact removed]", text) if names else text


def _text(value) -> str:
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v).strip() for v in value if str(v).strip())
    return "" if value is None else str(value).strip()


def _rows(entries: list[dict], columns: tuple, names: re.Pattern | None = None) -> list[dict]:
    """Each entry reduced to the allow-listed keys, as scrubbed text."""
    return [{key: _scrub(_text(e.get(key)), names) for key, _ in columns} for e in entries]


@functools.lru_cache(maxsize=2)
def _contact_names(path: str, mtime: float) -> re.Pattern | None:
    """The applications' contact persons, as one whole-word pattern, read only to remove them.

    From `contact_name` and from `submission_title` ("Information provided by <name>"). A name of a
    single word is skipped: the export fills `contact_name` with the company ("U2DIA") when no
    person was given, and removing a company's name from its own row would hide the row's subject.
    """
    import pandas as pd
    try:
        df = pd.read_excel(path, usecols=lambda c: str(c).strip() in ("contact_name", "submission_title"))
    except Exception:
        return None
    found: set[str] = set()
    for col in df.columns:
        for value in df[col].dropna().astype(str):
            name = re.sub(r"^\s*Information provided by\s+", "", value, flags=re.I).strip()
            if len(name.split()) >= 2 and len(name) <= 80:
                found.add(name)
    if not found:
        return None
    alternation = "|".join(re.escape(n) for n in sorted(found, key=len, reverse=True))
    return re.compile(rf"(?i)(?<!\w)(?:{alternation})(?!\w)")


def _file_meta(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {"file": os.path.basename(path or ""), "modified": None}
    modified = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    return {"file": os.path.basename(path), "modified": modified.isoformat(timespec="seconds")}


def _applications() -> dict:
    from api.main import _LOCAL_XLSX, _gd_key, applications_catalog
    cat = applications_catalog()
    names = _contact_names(_LOCAL_XLSX, os.path.getmtime(_LOCAL_XLSX)) if cat["available"] else None
    entries = [{**e, **{k: f["value"] for k, f in (e.get("facts") or {}).items()}} for e in cat["entries"]]
    live = _gd_key()
    return {**cat, "path": _LOCAL_XLSX, "entries": entries, "names": names,
            "live_api": live,
            "note": ("GLASSDOLLAR_API_KEY is set: evaluation and name search read the live GlassDollar "
                     "API first; this workbook is the problem search's catalogue and the second source."
                     if live else "No GlassDollar API key is set, so this workbook is the only startup "
                     "database: name search and Solve a Problem both read it.")}


def _tools() -> dict:
    cat = catalogs.tools_catalog()
    return {**cat, "path": catalogs.DEFAULT_TOOLS_CSV}


def _sellers() -> dict:
    cat = catalogs.xcelerator_catalog()
    return {**cat, "path": catalogs.XCELERATOR_XLSX}


def _needs() -> dict:
    from core.department_needs import _XLSX_PATH, load_department_requirements
    cat = catalogs.needs_index_catalog()
    entries = [{**n, "department": d["label"]} for d in load_department_requirements().values() for n in d["needs"]]
    return {**cat, "path": str(_XLSX_PATH), "entries": entries}


def _challenges() -> dict:
    from core.solve import CHALLENGES_PATH, load_challenges
    items = [c for c in load_challenges() if isinstance(c, dict)]
    exists = os.path.exists(CHALLENGES_PATH)
    # catalogs.py computes no checksum for the library: it is appended to, not matched against.
    return {"available": exists, "reason": "" if exists else "no problem library yet", "checksum": "",
            "path": CHALLENGES_PATH, "entries": items}


SOURCES = {
    "applications": {
        "label": "Startup applications", "load": _applications, "columns": APPLICATION_COLUMNS,
        "about": "Pitch-form answers from startups that applied through GlassDollar, with the "
                 "profile fields exported alongside them.",
        "feeds": "Solve a Problem's applicant search, and name search."},
    "tools": {
        "label": "Siemens tools", "load": _tools, "columns": TOOL_COLUMNS,
        "about": "Siemens' software and hardware catalogue, one entry per tool name.",
        "feeds": "Empower (which Siemens tools a startup could build on) and the fit stage."},
    "xcelerator": {
        "label": "Xcelerator ecosystem", "load": _sellers, "columns": SELLER_COLUMNS,
        "about": "Partners listed on the Siemens Xcelerator marketplace, duplicate sellers merged.",
        "feeds": "Connect (which ecosystem partners a startup overlaps or complements)."},
    "needs": {
        "label": "Department needs", "load": _needs, "columns": NEED_COLUMNS,
        "about": "The capabilities each Siemens department has said it is looking for.",
        "feeds": "Collaborate (does a startup answer a department's stated need)."},
    "challenges": {
        "label": "Problem library", "load": _challenges, "columns": CHALLENGE_COLUMNS,
        "about": "Every problem stated in Solve a Problem, recorded as it was asked.",
        "feeds": "Kept as the demand-side catalogue; not yet scored against."},
}


def _summary(source_id: str, loaded: dict) -> dict:
    spec = SOURCES[source_id]
    return {"id": source_id, "label": spec["label"], "about": spec["about"], "feeds": spec["feeds"],
            **_file_meta(loaded.get("path", "")),
            "available": bool(loaded.get("available")), "reason": loaded.get("reason", ""),
            "rows": len(loaded["rows"]), "checksum": loaded.get("checksum") or None,
            "columns": [{"key": k, "label": label} for k, label in spec["columns"]],
            **({"live_api": loaded["live_api"], "note": loaded["note"]} if "note" in loaded else {})}


# source id -> (file version, rows). Scrubbing the applications is the one costly step (every
# pitch against every contact name), so it runs once per version of the file, not per page.
_ROWS: dict[str, tuple] = {}


def _load(source_id: str) -> dict:
    """The source's metadata plus ``rows``: its entries reduced to the allow-list and scrubbed."""
    spec = SOURCES[source_id]
    try:
        loaded = spec["load"]()
        path = loaded.get("path") or ""
        version = (path, os.path.getmtime(path) if path and os.path.exists(path) else 0,
                   loaded.get("checksum"), len(loaded.get("entries") or ()))
        cached = _ROWS.get(source_id)
        if not cached or cached[0] != version:
            cached = (version, _rows(loaded.get("entries") or [], spec["columns"], loaded.get("names")))
            _ROWS[source_id] = cached
    except Exception as exc:                      # an unreadable file is reported, never a 500
        return {"available": False, "reason": f"could not be read: {type(exc).__name__}", "rows": []}
    return {**{k: v for k, v in loaded.items() if k not in ("entries", "names")}, "rows": cached[1]}


@router.get("")
def list_sources(user: Principal = Depends(current_user)) -> dict:
    """Every data source with what it is, what it feeds and its file's identity — no rows."""
    return {"sources": [_summary(sid, _load(sid)) for sid in SOURCES]}


@router.get("/{source_id}")
def browse_source(source_id: str, q: str = "", offset: int = 0, limit: int = PAGE_DEFAULT,
                  user: Principal = Depends(current_user)) -> dict:
    """One page of a source's allow-listed rows, filtered by every word of ``q``."""
    if source_id not in SOURCES:
        raise HTTPException(404, "Unknown data source.")
    loaded = _load(source_id)
    rows = loaded["rows"]
    terms = q[:QUERY_MAX].casefold().split()
    if terms:
        rows = [r for r in rows if all(t in " ".join(r.values()).casefold() for t in terms)]
    offset, limit = max(0, offset), max(1, min(limit, PAGE_MAX))
    return {**_summary(source_id, loaded), "q": q[:QUERY_MAX], "total": len(rows),
            "offset": offset, "limit": limit, "items": rows[offset:offset + limit]}
