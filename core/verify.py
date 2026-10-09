"""LLM-confirmed verification: check every self-reported claim against web evidence."""
from __future__ import annotations

import os
import re

import pandas as pd

from .llm import LLMClient
from .text import _split_list, _STOP

# The same failure fit.py documents for its match call. Verification thinks (no reasoning="none":
# it adjudicates claims, not transcribes them) over the whole evidence pool, and measured 18-25s on
# Radical Dot cold — so under any extra load it crosses the 30s LLM_TIMEOUT, is killed and retried
# from scratch, and three attempts later (96s, on the critical path: scoring waits for it) the run
# falls back to heuristics, marked degraded. Lowering the reasoning would be a scoring change; a
# timeout sized for the call is not. Two attempts, for fit's reason: gunicorn's 300s worker limit.
VERIFY_TIMEOUT = float(os.getenv("VERIFY_TIMEOUT", "90"))
# Past ~1.5x its usual 7-16s, a duplicate races the slow request (llm._hedged). 0 turns it off.
VERIFY_HEDGE = float(os.getenv("VERIFY_HEDGE", "25"))


def collect_evidence(enrichment: dict) -> list[dict]:
    """Flatten all DDG hits into one evidence pool the LLM can adjudicate against."""
    pool = []
    for key, hits in enrichment.get("web", {}).items():
        for h in hits:
            pool.append({"q": key, "title": h.get("title", ""),
                         "url": h.get("href", ""), "body": h.get("body", "")})
    return pool


def build_claims(row: pd.Series) -> list[dict]:
    """The self-reported claims we want the LLM to confirm."""
    claims: list[dict] = []
    def add(field, val):
        v = str(val).strip()
        if v:
            claims.append({"field": field, "value": v})
    add("funding", row.get("funding"))
    add("hq", row.get("hq"))
    add("founded_year", row.get("founded_year"))
    add("employees", row.get("employees_count") or row.get("employee_band"))
    add("website", row.get("website"))
    add("dev_stage", row.get("Development stage of your solution"))
    for c in _split_list(str(row.get("customers", "")) or str(row.get("Reference customers", ""))):
        claims.append({"field": "reference_customer", "value": c})
    return claims


_VERIFY_SYSTEM = (
    "You are a meticulous fact-checker for Siemens startup scouting. Confirm each self-reported "
    "claim ONLY against the supplied web evidence. Never assume; if the evidence does not address a "
    "claim, mark it unverified. If evidence conflicts with the claim, mark it contradicted. JSON only."
)


def verify_facts(row: pd.Series, enrichment: dict, llm: "LLMClient") -> dict:
    """LLM confirms every self-reported claim against the gathered web evidence."""
    claims = build_claims(row)
    evidence = collect_evidence(enrichment)
    company = str(row.get("company_name", ""))
    if llm.available and evidence:
        ev = "\n".join(f"- [{e['url']}] {e['title']}: {e['body'][:220]}" for e in evidence[:18])
        cl = "\n".join(f"- ({c['field']}) {c['value']}" for c in claims)
        prompt = (
            f"COMPANY: {company}\n\nSELF-REPORTED CLAIMS:\n{cl}\n\nWEB EVIDENCE (DuckDuckGo):\n{ev}\n\n"
            "For each claim choose status: verified | partial | unverified | contradicted. Cite the single "
            "best evidence URL (empty if none). Give confidence 0-1 and a note of <=12 words.\n"
            'Return ONLY JSON: {"claims":[{"field":"","value":"","status":"","evidence_url":"",'
            '"confidence":0.0,"note":""}], "red_flags":["..."]}'
        )
        data = LLMClient.parse_json(llm.complete(prompt, system=_VERIFY_SYSTEM, max_tokens=1500,
                                                 timeout=VERIFY_TIMEOUT, max_attempts=2,
                                                 hedge_after=VERIFY_HEDGE))
        if data and "claims" in data:
            data["method"] = "llm"
            return data
    return _heuristic_verify(claims, evidence)


def _heuristic_verify(claims: list[dict], evidence: list[dict]) -> dict:
    blob = " ".join((e["title"] + " " + e["body"]).lower() for e in evidence)
    out = []
    for c in claims:
        toks = [t for t in re.findall(r"[a-z0-9]+", c["value"].lower()) if len(t) > 2 and t not in _STOP]
        hits = sum(1 for t in toks if t in blob)
        if not evidence:
            status, conf = "unverified", 0.3
        elif toks and hits >= max(1, len(toks) // 2):
            status, conf = "verified", 0.7
        elif hits:
            status, conf = "partial", 0.5
        else:
            status, conf = "unverified", 0.35
        url = next((e["url"] for e in evidence
                    if any(t in (e["title"] + e["body"]).lower() for t in toks)), "")
        out.append({"field": c["field"], "value": c["value"], "status": status,
                    "evidence_url": url, "confidence": conf, "note": "keyword corroboration"})
    return {"claims": out, "red_flags": [], "method": "offline_keyword"}
