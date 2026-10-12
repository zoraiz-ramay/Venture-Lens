"""Solve a Problem's follow-ups: refine a finished search, ask about its shortlist, and keep it.

Every route reads the search from the reviewer's own records — the job (api/jobs.py, keyed on their
oid), or the copy they kept (api/store.py ``solve_searches``, keyed the same way) once the job has
expired — never from the request body. The browser names a search and a choice; it cannot hand the
server a shortlist of its own to rank, or facts of its own to be cited back as researched.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable, Literal

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
_EXPIRED = "This search has expired or has not finished. Start it again."
SHORTLIST_MAX = 60


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
    ids: list[str] = Field(default_factory=list, max_length=SHORTLIST_MAX)


class ShortlistBody(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=SHORTLIST_MAX)


def _candidates(result: dict) -> list[dict]:
    return [c for key in ("applicants", "market", "market_found")
            for c in result.get(key) or [] if isinstance(c, dict)]


def _everyone(result: dict) -> list[dict]:
    """Every candidate the search found, including any the current refinement hides: a startup the
    reviewer kept stays on their shortlist when a sharper brief drops it from the list."""
    seen, out = set(), []
    for c in _candidates(result) + _candidates(result.get("unrefined") or {}):
        if c.get("id") and c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return out


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


def _finished_job(user: Principal, job_id: str) -> dict | None:
    record = sessions().get(jobs._key(user.oid, job_id))
    if not record or record.get("kind") != "solve" or record.get("status") != "complete" or not record.get("result"):
        return None
    return record


def _search(user: Principal, job_id: str) -> tuple[dict, Callable[[dict], None]]:
    """The finished search as the reviewer last saw it, and how to write a new result back.

    The live job wins while it exists; after its day in the session store the kept copy answers,
    so a search reopened next week can still be sharpened and questioned. A write goes to both."""
    record = _finished_job(user, job_id)
    kept = store.get_solve_search(user.oid, job_id)
    if record is None and kept is None:
        raise HTTPException(404, _EXPIRED)

    def save(result: dict) -> None:
        if record is not None:
            record.update(result=result, updated_at=time.time())
            sessions().put(jobs._key(user.oid, job_id), record, jobs.TTL)
        if kept is not None:
            store.update_solve_search(user.oid, job_id, result=result)
    return (record["result"] if record is not None else kept["result"]), save


def _thread_key(job_id: str) -> str:
    # One follow-up thread per search, in the assistant's own table. A company's thread key is its
    # casefolded name, which never looks like this prefix plus a 32-hex job id.
    return f"solve:{job_id}"


def _private(result: dict) -> bool:
    # Tracxn rows are licensed to this reviewer; a completion that read them stays out of the
    # shared cache, exactly as the search that fetched them did.
    return any(c.get("source") == "tracxn" for c in _candidates(result))


@router.post("/{job_id}/refine")
def refine_search(job_id: str, body: RefineBody, user: Principal = Depends(current_user)) -> dict:
    """Re-rank a finished search with the facets the reviewer picked. No new market search."""
    from api.main import applications_catalog
    current, save = _search(user, job_id)
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
    save(result)
    return result


@router.post("/{job_id}/ask")
def ask_shortlist(job_id: str, body: ShortlistAskBody, user: Principal = Depends(current_user)) -> dict:
    """A follow-up about the shortlist: answered from what the search found first, then the
    reviewer's Tracxn, then the model's web search (core.chat.chat_assistant). The question and
    its answer are kept with the search, so the thread is there when the reviewer comes back."""
    result, _ = _search(user, job_id)
    wanted = set(body.ids)
    shown = {c.get("id") for c in _candidates(result)}
    # A kept startup a refinement hid is still asked about: it is on the reviewer's shortlist.
    hidden = [c for c in _everyone(result) if c["id"] in wanted and c["id"] not in shown]
    pool = {**result, "market": list(result.get("market") or []) + hidden}
    facts = shortlist_facts(pool, body.ids)
    names = list(dict.fromkeys(c["name"] for c in (pool.get("applicants") or []) + pool["market"]
                               if not wanted or c.get("id") in wanted))
    subject = (f'the startups shortlisted for the problem "{str(result.get("problem", ""))[:200]}": '
               + ", ".join(names[:18]))
    llm = core.LLMClient()
    tracxn = tracxn_client_for(user, llm)
    token = core.web.set_cache_private(True) if tracxn is not None or _private(result) else None
    try:
        out = core.chat_assistant(body.question, llm=llm, tracxn=tracxn, history=[t.model_dump() for t in body.history],
                                  run_facts=facts, facts_subject=subject)
    finally:
        if token is not None:
            core.web.reset_cache_private(token)
    try:
        store.append_chat(user.oid, _thread_key(job_id), [
            {"role": "user", "text": body.question},
            {"role": "assistant", "text": out.get("answer", ""), "source": out.get("source"),
             "provider": out.get("provider"), "note": out.get("note"), "evidence": out.get("evidence")}])
    except Exception:                                # noqa: BLE001 — losing history must not lose the answer
        log.warning("[solve] could not store the follow-up turn", exc_info=True)
    return out


# ------------------------------------------------------------------ searches a reviewer keeps
@router.get("/searches")
def my_solve_searches(user: Principal = Depends(current_user)) -> dict:
    """The reviewer's own kept searches, newest worked-on first. Private: no parameter widens it."""
    return {"searches": store.list_solve_searches(user.oid)}


@router.post("/searches/{job_id}")
def keep_search(job_id: str, user: Principal = Depends(current_user)) -> dict:
    """Open a search to work with: kept the first time its job is seen finished, read back after.

    Idempotent — the page calls it whenever it shows a finished search. A kept search is returned
    as kept: its result may have been refined since and its shortlist edited."""
    record = _finished_job(user, job_id)
    if record is not None:
        kept = store.keep_solve_search(user.oid, job_id, record.get("query") or record["result"].get("problem", ""),
                                       record["result"])
    else:
        kept = store.get_solve_search(user.oid, job_id)
    if kept is None:
        raise HTTPException(404, _EXPIRED)
    return {**kept, "thread": store.get_chat(user.oid, _thread_key(job_id))}


@router.put("/searches/{job_id}/shortlist")
def set_shortlist(job_id: str, body: ShortlistBody, user: Principal = Depends(current_user)) -> dict:
    """Replace the search's shortlist. Only startups the search found are kept, in the order given."""
    result, _ = _search(user, job_id)
    found = {c["id"] for c in _everyone(result)}
    ids = [i for i in dict.fromkeys(body.ids) if i in found]
    if store.get_solve_search(user.oid, job_id) is None:
        record = _finished_job(user, job_id) or {}
        store.keep_solve_search(user.oid, job_id, record.get("query") or result.get("problem", ""), result)
    store.update_solve_search(user.oid, job_id, shortlist=ids)
    return {"shortlist": ids}


@router.delete("/searches/{job_id}")
def forget_search(job_id: str, user: Principal = Depends(current_user)) -> dict:
    """Forget a kept search and its follow-up thread. The search job itself expires on its own."""
    if not store.delete_solve_search(user.oid, job_id):
        raise HTTPException(404, "No kept search with that id.")
    store.clear_chat(user.oid, _thread_key(job_id))
    return {"deleted": job_id}
