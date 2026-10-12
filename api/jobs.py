"""Navigation-independent evaluation jobs. Redis holds snapshots, a bounded pool executes work.
In-flight work is not restartable; stale jobs are explicitly marked interrupted, never retried silently.
"""
import json
import contextvars
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from api.auth import Principal, current_user, sessions
from api.workspace import locked

router = APIRouter(prefix="/api/jobs", tags=["workspace"])
# Threads here only wait: how many evaluations actually run at once is api/flight.slot's job,
# shared with the other two entry points. This pool used to BE the limit (2 per worker), which
# queued workspace searches while the stream route ran unbounded.
_slots = threading.BoundedSemaphore(40)
_pool = ThreadPoolExecutor(max_workers=40, thread_name_prefix="evaluation")
TTL = 86400


class JobBody(BaseModel):
    kind: Literal["evaluate", "solve"] = "evaluate"
    names: list[str] = Field(default_factory=list, max_length=10)
    problem: str = Field("", max_length=2000)
    refresh: bool = False
    # Accepted from older clients and ignored: evaluations cover every department.
    department_id: str | None = Field(None, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    request_id: str = Field(..., min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9-]+$")


def _key(oid, job_id):
    return f"job:{oid}:{job_id}"


def _execute(oid, job_id, body, user):
    """Run one queued job on the pool, writing status and partials to its record as they land."""
    from api.main import EvaluateBody, SolveBody, _run_evaluation, run_solve
    key = _key(oid, job_id)
    record = sessions().get(key)
    if not record:
        _slots.release(); return
    # Solve reports from two threads at once (the applicants and the interpretation land
    # concurrently), and two read-merge-writes of `partial` interleaved would drop one of them.
    guard = threading.RLock()
    def update(**fields):
        with guard:
            record.update(fields, updated_at=time.time())
            sessions().put(key, json.loads(json.dumps(record, default=str)), TTL)
    def merge_partial(section, data):
        with guard:
            update(partial={**(record.get("partial") or {}), **data})
    try:
        update(status="running")
        if body.kind == "solve":
            result = run_solve(SolveBody(problem=record["query"]), user, merge_partial)
        else:
            # Preserve partial rendering; use the same source/cache semantics as the public endpoint.
            # Guarded for the same reason as merge_partial: the researched profile is emitted from
            # the profile branch's own thread while the main thread emits the other sections.
            def partial(section, data):
                with guard:
                    previous = record.get("partial") or {"found": True, "streaming": True}
                    previous.update(data if section in ("identity", "profile") else {section: data})
                    update(partial=previous)
            from api.main import _evaluation_cached
            request = EvaluateBody(name=record["query"], refresh=body.refresh,
                                   department_id=body.department_id)
            result = _evaluation_cached(record["query"], request, user)
            if result is None:
                result = _run_evaluation(record["query"], request, user.as_reviewer(), partial, user)
        update(status="complete", result=result, partial=None)
    except Exception as exc:
        # Do not expose vendor responses or credentials in job errors.
        detail = exc.detail if isinstance(exc, HTTPException) else "Research failed. Please retry this startup."
        update(status="error", error=str(detail), partial=None)
    finally:
        _slots.release()


@router.post("")
def start(body: JobBody, user: Principal = Depends(current_user)):
    """Queue one job per startup name (or one for a problem). Idempotent per ``request_id``: a
    retried request gets the jobs it already started."""
    values = [body.problem.strip()] if body.kind == "solve" else [v.strip() for v in body.names]
    values = list({v.casefold(): v for v in values}.values())
    if not values or any(not v or len(v) > (2000 if body.kind == "solve" else 200) for v in values):
        raise HTTPException(422, "Provide 1–10 startup names (up to 200 characters each) or a problem.")
    if body.kind == "solve" and len(values[0]) < 3:
        raise HTTPException(422, "Describe the problem in at least three characters.")
    dedup = f"job-request:{user.oid}:{body.request_id}"
    with locked(dedup):
        prior = sessions().get(dedup)
        if prior:
            return prior
        acquired = 0
        for _ in values:
            if not _slots.acquire(blocking=False):
                for _ in range(acquired): _slots.release()
                raise HTTPException(429, "The evaluation queue is full. Please try again shortly.")
            acquired += 1
        jobs = []
        for value in values:
            job_id = secrets.token_hex(16)
            row = {"id": job_id, "kind": body.kind, "query": value, "status": "queued",
                   "department_id": body.department_id if body.kind == "evaluate" else None,
                   "updated_at": time.time()}
            sessions().put(_key(user.oid, job_id), row, TTL)
            jobs.append(row)
        response = {"jobs": jobs}
        sessions().put(dedup, response, TTL)
        for row in jobs:
            _pool.submit(contextvars.copy_context().run, _execute, user.oid, row["id"], body, user)
        return response


@router.get("/{job_id}")
def get_job(job_id: str, user: Principal = Depends(current_user)):
    """The caller's own job record; one silent for 30 minutes is reported as interrupted."""
    record = sessions().get(_key(user.oid, job_id))
    if not record:
        raise HTTPException(404, "This job has expired or is not in your workspace.")
    if record["status"] in ("queued", "running") and time.time() - record["updated_at"] > 1800:
        record.update(status="error", error="Research was interrupted. Start it again.")
        sessions().put(_key(user.oid, job_id), record, TTL)
    return record


# ----------------------------------------------------------------------------- profile lookups
# The profile's lookups (market signals, funding, headcount, team, business flow) each wait on the
# model's own web search, and one of those took 34-56s measured: market signals ran its four domains
# in 61s on Radical Dot with nothing else running. The profile opens several at once, so a request
# could outlast the browser's two minutes or a proxy's idle timeout, and the section showed an error
# instead of what the search found. A lookup now runs here and the page polls for it: every
# request returns in milliseconds, and the work finishes whether or not anyone is still waiting.
_lookups = ThreadPoolExecutor(max_workers=16, thread_name_prefix="lookup")
LOOKUP_TTL = 900      # also how long a lookup may run before a poll starts it again


def in_background(key: str, compute, ready=None) -> dict:
    """``compute()``'s result once it is ready, ``{"pending": True}`` until then.

    The first call starts it; later calls with the same key poll it. Redis holds the state, so a
    poll landing on the other gunicorn worker sees the same lookup instead of starting a second.
    ``ready()``, if given, is an answer already on hand (a stored lookup): served when no lookup is
    in flight for the key, so the poll that collects a finished lookup gets that lookup's answer."""
    store_key = f"lookup:{key}"
    with locked(store_key):
        record = sessions().get(store_key)
        if record is None and ready is not None:
            on_hand = ready()
            if on_hand is not None:
                return on_hand
        if record and record.get("status") == "done":
            sessions().delete(store_key)
            return record["result"]
        if record and record.get("status") == "error":
            sessions().delete(store_key)
            raise HTTPException(502, record["error"])
        if record and time.time() - record.get("started", 0) < LOOKUP_TTL:
            return {"pending": True}
        sessions().put(store_key, {"status": "running", "started": time.time()}, LOOKUP_TTL)

    def run():
        try:
            result = json.loads(json.dumps(compute(), default=str))
            sessions().put(store_key, {"status": "done", "result": result}, LOOKUP_TTL)
        except Exception as exc:
            # Do not expose vendor responses or credentials in lookup errors.
            detail = exc.detail if isinstance(exc, HTTPException) else "The lookup failed. Please try again."
            sessions().put(store_key, {"status": "error", "error": str(detail)}, LOOKUP_TTL)

    _lookups.submit(contextvars.copy_context().run, run)
    return {"pending": True}
