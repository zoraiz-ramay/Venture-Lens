"""The catalogs a startup is matched against, loaded with their checksums.

Siemens Fit's three — the Siemens tools CSV (Empower, and the fit stage), the Xcelerator sellers
workbook (Connect) and a department's stated needs (Collaborate) — plus the needs index all
departments are ranked from, and the applications workbook the problem search reads
(core/solve.py).

Each pillar in core/pillars.py asks "which of THESE entries does the startup fit", and the answer
is only reproducible if we know exactly which entries existed. So every loader returns the SHA-256
of the bytes it read, and every assessment records it: a run scored against last month's
Xcelerator export and one scored against this month's are different results, and the run cache
(api/store.py) keys on the checksum so it never serves one for the other.

Pure data loading — no model, no network. A missing or malformed catalog returns
``available: False`` with the reason, and the pillar that needed it reports itself unassessed;
it is never treated as a catalog with nothing in it, which would score every startup a no-match.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re

from .config import DEFAULT_GLASSDOLLAR, DEFAULT_TOOLS_CSV, BASE_DIR

XCELERATOR_XLSX = os.getenv("XCELERATOR_XLSX", str(BASE_DIR / "siemens_xcelerator_data.xlsx"))
_SELLER_COLUMNS = ("Seller", "Region", "Industry", "Topic", "Motion", "Description", "URL")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(path: str) -> str:
    """The checksum of a text file whatever its line endings.

    git stores siemens_tools.csv with LF and a Windows checkout writes it with CRLF, so the byte
    checksum differed by platform: the tools index, built on Windows, never matched the Linux
    production image, and Empower and fit fell back to word matching there without anyone seeing
    why. The workbooks are binary and git leaves them alone, so only this file needs it."""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()


def _split(value) -> list[str]:
    return [v.strip() for v in str(value or "").split(";") if v.strip()]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _unavailable(name: str, reason: str) -> dict:
    """A catalog that could not be read — reported as such, never as an empty catalog."""
    return {"name": name, "available": False, "reason": reason, "checksum": "", "entries": []}


# `mtime` is unused in the body on purpose: it is part of the lru_cache key, so editing a catalog
# file on disk is picked up without a restart.
@functools.lru_cache(maxsize=4)
def _tools(path: str, mtime: float) -> dict:
    import pandas as pd
    try:
        df = pd.read_csv(path).fillna("")
    except Exception as exc:                     # unreadable file, not "no tools"
        return _unavailable("siemens_tools", f"tools catalog unreadable: {type(exc).__name__}")
    df = df.rename(columns={"name": "product"}) if "product" not in df.columns else df
    if "product" not in df.columns or "description" not in df.columns:
        return _unavailable("siemens_tools", "tools catalog lacks name/description columns")
    entries, seen = [], set()
    for rec in df.to_dict("records"):
        name = str(rec.get("product", "")).strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        entries.append({"id": f"tool:{_slug(name)}", "name": name,
                        "category": str(rec.get("category", "") or rec.get("type", "")),
                        "division": str(rec.get("division", "")),
                        "description": str(rec.get("description", "")),
                        "url": str(rec.get("url", "")) if str(rec.get("url", "")).startswith("http") else ""})
    return {"name": "siemens_tools", "available": bool(entries), "checksum": _sha256_text(path),
            "path": os.path.basename(path), "entries": entries,
            "reason": "" if entries else "tools catalog is empty"}


def tools_catalog(path: str = DEFAULT_TOOLS_CSV) -> dict:
    """siemens_tools.csv as `tool:` entries (de-duplicated by name), cached until the file changes."""
    if not os.path.exists(path):
        return _unavailable("siemens_tools", "tools catalog file not found")
    return _tools(path, os.path.getmtime(path))


def _canonical_url(urls: list[str]) -> str:
    """Prefer the seller's own ecosystem page over a `change-me-xxxx` placeholder slug."""
    good = [u for u in urls if u.startswith("http") and "change-me" not in u]
    return good[0] if good else ""


@functools.lru_cache(maxsize=4)
def _xcelerator(path: str, mtime: float) -> dict:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        return _unavailable("xcelerator", f"Xcelerator workbook unreadable: {type(exc).__name__}")
    issues: list[str] = []
    if "Sellers" not in wb.sheetnames:
        return _unavailable("xcelerator", "Xcelerator workbook has no Sellers sheet")
    rows = list(wb["Sellers"].iter_rows(values_only=True))
    header = [str(h or "").strip() for h in (rows[0] if rows else ())]
    missing = [c for c in _SELLER_COLUMNS if c not in header]
    if missing:
        return _unavailable("xcelerator", f"Sellers sheet missing columns: {', '.join(missing)}")
    col = {c: header.index(c) for c in _SELLER_COLUMNS}
    # Seven sellers appear twice, identical but for one row carrying a `change-me` placeholder URL.
    # Merged by name so a startup is never offered the same partner twice, keeping the union of
    # their filter values and the real URL.
    merged: dict[str, dict] = {}
    for r in rows[1:]:
        if not r or not str(r[col["Seller"]] or "").strip():
            continue
        name = str(r[col["Seller"]]).strip()
        rec = merged.setdefault(name.casefold(), {
            "name": name, "industries": [], "topics": [], "regions": [], "motion": [],
            "description": "", "urls": []})
        for key, column in (("industries", "Industry"), ("topics", "Topic"),
                            ("regions", "Region"), ("motion", "Motion")):
            for v in _split(r[col[column]]):
                if v not in rec[key]:
                    rec[key].append(v)
        rec["description"] = rec["description"] or str(r[col["Description"]] or "").strip()
        rec["urls"].append(str(r[col["URL"]] or "").strip())
    sellers = []
    for rec in merged.values():
        url = _canonical_url(rec.pop("urls"))
        if not url:
            issues.append(f"{rec['name']}: no valid seller URL")
        sellers.append({"id": f"seller:{_slug(rec['name'])}", **rec, "url": url})
    duplicates = len(rows) - 1 - len(merged)
    if duplicates:
        issues.append(f"{duplicates} duplicate seller row(s) merged")
    # The published filter vocabularies. Read from Filter_Values when present, else derived from
    # the sellers — the vocabulary is what the model may match against, so it must be closed.
    vocab = {"Industry": set(), "Topic": set()}
    if "Filter_Values" in wb.sheetnames:
        for r in list(wb["Filter_Values"].iter_rows(values_only=True))[1:]:
            if r and r[0] in vocab and str(r[1] or "").strip():
                vocab[r[0]].add(str(r[1]).strip())
    else:
        issues.append("Filter_Values sheet missing; vocabulary derived from sellers")
    for s in sellers:
        vocab["Industry"].update(s["industries"])
        vocab["Topic"].update(s["topics"])
    wb.close()
    industries = [{"id": f"industry:{_slug(v)}", "name": v, "kind": "industry"} for v in sorted(vocab["Industry"])]
    topics = [{"id": f"topic:{_slug(v)}", "name": v, "kind": "topic"} for v in sorted(vocab["Topic"])]
    return {"name": "xcelerator", "available": bool(sellers), "checksum": _sha256(path),
            "path": os.path.basename(path), "entries": sellers, "industries": industries,
            "topics": topics, "issues": issues,
            "reason": "" if sellers else "Xcelerator workbook has no sellers"}


def xcelerator_catalog(path: str = XCELERATOR_XLSX) -> dict:
    """The Xcelerator sellers plus the closed industry/topic vocabularies, cached until the file changes."""
    if not os.path.exists(path):
        return _unavailable("xcelerator", "Xcelerator workbook not found")
    return _xcelerator(path, os.path.getmtime(path))


def needs_index_catalog() -> dict:
    """Every department's stated needs from the requirements workbook, as one catalog to embed.

    One index for all departments, keyed on the workbook's bytes; each department's Collaborate
    ranks only its own needs out of it. An entry's ``name`` is its catalog id, the one unique key a
    ranking can hand back. Needs an admin overrode are not in it and are ranked by words.
    """
    from .department_needs import _XLSX_PATH, load_department_requirements
    reqs = load_department_requirements()
    if not reqs or not os.path.exists(_XLSX_PATH):
        return _unavailable("department_needs_index", "no departmental requirements workbook")
    entries = [{"id": f"need:{_slug(n['id'])}", "name": f"need:{_slug(n['id'])}", "department": d["label"],
                "category": n.get("category", ""), "capability": n.get("capability", ""),
                "description": n.get("description", ""), "keywords": n.get("keywords", [])}
               for d in reqs.values() for n in d["needs"]]
    return {"name": "department_needs_index", "available": bool(entries), "checksum": _sha256(str(_XLSX_PATH)),
            "entries": entries, "reason": "" if entries else "the requirements workbook states no needs"}


# Which columns of the applications workbook a problem search may read, and under which label.
# The pitch-form answers are the startup's own words; the profile columns were filled from
# GlassDollar when the file was exported. `status`, `contact_*` and `Referral` are deliberately
# absent: an application's internal status and its contact person are not for a problem search,
# and leaving them out here is what keeps them out of every result built from an entry.
APPLICATION_SOURCE = "application"
GLASSDOLLAR_PROFILE_SOURCE = "glassdollar_profile"
_APPLICATION_FACTS = (
    ("stage", "Development stage of your solution", APPLICATION_SOURCE),
    ("business_model", "Business model", APPLICATION_SOURCE),
    ("reference_customers", "Reference customers", APPLICATION_SOURCE),
    ("siemens_functions", "Which Siemens function will profit from your solution?", APPLICATION_SOURCE),
    ("partnership", "What type of partnership are you seeking for?", APPLICATION_SOURCE),
    ("hq", "hq", GLASSDOLLAR_PROFILE_SOURCE),
    ("founded", "founded_year", GLASSDOLLAR_PROFILE_SOURCE),
    ("employees", "employees_count", GLASSDOLLAR_PROFILE_SOURCE),
    ("funding", "funding", GLASSDOLLAR_PROFILE_SOURCE),
    ("customers", "customers", GLASSDOLLAR_PROFILE_SOURCE),
)


def _cell_text(value) -> str:
    s = str(value if value is not None else "").strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def _application_fact(key: str, value) -> str:
    """One workbook cell as the text a candidate's fact shows; "" when the export holds no value."""
    from .text import format_funding
    s = _cell_text(value)
    if key == "hq":                                  # the export writes "null, null" for an unknown HQ
        parts = [p.strip() for p in s.split(",") if p.strip() and p.strip().lower() not in ("null", "none")]
        return ", ".join(parts)
    if key in ("founded", "employees"):
        try:
            return str(int(float(s))) if s else ""
        except ValueError:
            return s
    if key == "funding":
        return format_funding(s) if s not in ("0", "0.0") else ""
    return s


def applications_catalog_from_df(df) -> dict:
    """Every application in the workbook as a searchable entry, for the problem search.

    An entry's ``name`` is the application's ``startup_id`` — company names repeat across pitches,
    and a ranking hands back names. The checksum covers exactly what is embedded and shown, so it
    is the same whether the frame came from the file or a test, and it changes only when that does.
    """
    if df is None or not len(df):
        return _unavailable("applications", "no applications file")
    name_col = "company_name" if "company_name" in df.columns else None
    entries, seen = [], set()
    for i, rec in enumerate(df.to_dict("records")):
        company = _cell_text(rec.get(name_col)) if name_col else ""
        if not company:
            continue
        key = _cell_text(rec.get("startup_id")) or f"row-{i}"
        try:
            key = str(int(float(key)))
        except ValueError:
            pass
        if key in seen:
            key = f"{key}-{i}"
        seen.add(key)
        website = _cell_text(rec.get("website"))
        facts = {}
        for fact, column, source in _APPLICATION_FACTS:
            value = _application_fact(fact, rec.get(column))
            if value:
                facts[fact] = {"value": value, "source": source}
        entries.append({
            "name": key, "company": company,
            "website": website if website.startswith(("http://", "https://")) else "",
            "short_description": _cell_text(rec.get("short_description")),
            "pitch": _cell_text(rec.get("Your pitch")),
            "differentiation": _cell_text(rec.get("Differentiation")),
            "facts": facts,
        })
    checksum = hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"name": "applications", "available": bool(entries), "checksum": checksum, "entries": entries,
            "reason": "" if entries else "the applications file names no company"}


@functools.lru_cache(maxsize=2)
def _applications(path: str, mtime: float) -> dict:
    import pandas as pd
    try:
        df = pd.read_excel(path).fillna("")
    except Exception as exc:
        return _unavailable("applications", f"applications file unreadable: {type(exc).__name__}")
    df.columns = [str(c).strip() for c in df.columns]
    return applications_catalog_from_df(df)


def applications_catalog(path: str = DEFAULT_GLASSDOLLAR) -> dict:
    """The applications workbook as problem-search entries, cached until the file changes."""
    if not path or not os.path.exists(path):
        return _unavailable("applications", "applications file not found")
    return _applications(path, os.path.getmtime(path))


def department_catalog(department: dict | None) -> dict:
    """The selected department's needs, snapshotted with a checksum of exactly what was read.

    A department whose interests change later gets a different checksum, so a run assessed
    against the old needs is never served as current for the new ones.
    """
    if not department or not department.get("id"):
        return _unavailable("department_needs", "no department selected")
    interests = [str(n).strip() for n in department.get("interests") or [] if str(n).strip()]
    # Stated needs (the requirements workbook) are one entry per capability, carrying what the
    # capability means; without them, each interest keyword is an entry of its own.
    structured = [n for n in department.get("needs") or [] if isinstance(n, dict) and n.get("capability")]
    snapshot = {"id": department["id"], "label": department.get("label", department["id"]),
                "interests": interests, "demo": bool(department.get("demo", True)),
                **({"needs": structured} if structured else {})}
    checksum = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    if not (structured or interests):
        return {**_unavailable("department_needs", "the department has no stated needs"),
                "snapshot": snapshot, "checksum": checksum}
    entries = ([{"id": f"need:{_slug(n['id'])}", "name": n["capability"], "kind": "need",
                 "category": n.get("category", ""), "description": n.get("description", ""),
                 "keywords": n.get("keywords", [])} for n in structured]
               if structured else [{"id": f"need:{_slug(n)}", "name": n} for n in interests])
    return {"name": "department_needs", "available": True, "checksum": checksum,
            "snapshot": snapshot, "provisional": snapshot["demo"], "reason": "", "entries": entries}
