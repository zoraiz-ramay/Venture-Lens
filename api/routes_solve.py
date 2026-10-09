"""Solve a Problem's follow-ups: refine a finished search, and ask about its shortlist.

Both read the search from the reviewer's own job record (api/jobs.py, keyed on their oid), never
from the request body. The browser names a job and a choice; it cannot hand the server a shortlist
of its own to rank, or facts of its own to be cited back as researched.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import core
from core.solve import refine, shortlist_facts
from api import jobs, store
from api.auth import Principal, current_user, sessions
from api.tracxn import client_for as tracxn_client_for

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/solve", tags=["workspace"])

# Headline facts an earlier evaluation can add to a candidate, by profile column.
_RUN_FACTS = (("hq", ("hq",)), ("founded", ("founded_year",)),
              ("employees", ("employees_count", "employee_band")), ("funding", ("funding",)))


class RefineBody(BaseModel):
    # facet id -> chosen option; core.solve.refined_brief keeps only options the search offered.
    answers: dict[str, str] = Field(default_factory=dict, max_length=6)
    note: str = Field("", max_length=300)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(..., max_length=4000)


class ShortlistAskBody(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=12)
    ids: list[str] = Field(default_factory=list, max_length=20)


def _candidates(result: dict) -> list[dict]:
    return [c for key in ("applicants", "market", "market_found")
            for c in result.get(key) or [] if isinstance(c, dict)]


def attach_runs(result: dict) -> None:
    """Link each candidate to an evaluation that already exists, and borrow its headline facts.

    A read only — nothing is researched. A fact the candidate already has (an applicant's own
    answer) is kept; the evaluation fills blanks, labelled as coming from it.
    """
    seen: dict = {}
    for c in _candidates(result):
        key = c["name"].casefold()
        try:
            ref = seen[key] if key in seen else seen.setdefault(key, store.run_ref_for(c["name"]))
        except Exception:                                   # noqa: BLE001 — a lookup never fails a search
            log.warning("[solve] run lookup failed for %s", c["name"], exc_info=True)
            ref = seen[key] = None
        if not ref:
            continue
        c["run_id"], c["evaluated_at"] = ref["run_id"], ref["created_at"]
        facts = c.setdefault("facts", {})
        for fact, columns in _RUN_FACTS:
            value = next((str(ref["profile"].get(col) or "").strip() for col in columns
                          if str(ref["profile"].get(col) or "").strip()), "")
            if value and fact not in facts:
                facts[fact] = {"value": value, "source": "evaluation"}


def _search(user: Principal, job_id: str) -> tuple[str, dict]:
    key = jobs._key(user.oid, job_id)
    record = sessions().get(key)
    if not record or record.get("kind") != "solve" or record.get("status") != "complete" or not record.get("result"):
        raise HTTPException(404, "This search has expired or has not finished. Start it again.")
    return key, record


def _private(result: dict) -> bool:
    # Tracxn rows are licensed to this reviewer; a completion that read them stays out of the
    # shared cache, exactly as the search that fetched them did.
    return any(c.get("source") == "tracxn" for c in _candidates(result))


@router.post("/{job_id}/refine")
def refine_search(job_id: str, body: RefineBody, user: Principal = Depends(current_user)) -> dict:
    """Re-rank a finished search with the facets the reviewer picked. No new market search."""
    from api.main import applications_catalog
    key, record = _search(user, job_id)
    current = record["result"]
    # The first refinement keeps what the search originally found, so dropping every chip goes
    # back to it and a looser refinement can re-admit a market candidate a stricter one dropped.
    unrefined = current.get("unrefined") or {k: current.get(k) for k in ("applicants", "market", "retrieval")}
    base = {**current, **unrefined, "unrefined": unrefined}
    if not any((body.answers or {}).values()) and not body.note.strip():
        result = {**base, "refinement": None}
    else:
        llm = core.LLMClient(model=os.getenv("SCOUTING_LLM_MODEL", ""))
        token = core.web.set_cache_private(True) if _private(base) else None
        try:
            result = refine(base, body.answers, body.note, llm, applications_catalog())
        finally:
            if token is not None:
                core.web.reset_cache_private(token)
        attach_runs(result)
    record.update(result=result, updated_at=time.time())
    sessions().put(key, record, jobs.TTL)
    return result


@router.post("/{job_id}/ask")
def ask_shortlist(job_id: str, body: ShortlistAskBody, user: Principal = Depends(current_user)) -> dict:
    """A follow-up about the shortlist: answered from what the search found first, then the
    reviewer's Tracxn, then the model's web search (core.chat.chat_assistant)."""
    _, record = _search(user, job_id)
    result = record["result"]
    facts = shortlist_facts(result, body.ids)
    wanted = set(body.ids)
    names = list(dict.fromkeys(c["name"] for c in (result.get("applicants") or []) + (result.get("market") or [])
                               if not wanted or c.get("id") in wanted))
    subject = (f'the startups shortlisted for the problem "{str(result.get("problem", ""))[:200]}": '
               + ", ".join(names[:18]))
    llm = core.LLMClient()
    tracxn = tracxn_client_for(user, llm)
    token = core.web.set_cache_private(True) if tracxn is not None or _private(result) else None
    try:
        return core.chat_assistant(body.question, llm=llm, tracxn=tracxn, history=[t.model_dump() for t in body.history],
                                   run_facts=facts, facts_subject=subject)
    finally:
        if token is not None:
            core.web.reset_cache_private(token)
