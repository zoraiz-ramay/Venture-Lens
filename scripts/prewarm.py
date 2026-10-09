#!/usr/bin/env python3
"""Evaluate known startups ahead of time, so a reviewer's lookup is served from the database.

A fresh evaluation takes about a minute and ~26 model calls; a stored one is instant. Most
lookups are for startups the team already knows — the applications file, a watchlist — so
evaluating those off-peak turns the slow path into the rare one. Runs land in runs.db like any
other and are served while younger than EVAL_TTL_DAYS.

It can never delay a reviewer: each run takes an evaluation slot only when nobody is waiting and
fewer than PREWARM_CONCURRENCY runs (default 2) are active in total (api/flight.slot,
background=True). Run it inside the container, where it shares the API's Redis queue; with
SESSION_BACKEND=memory it only coordinates with itself.

A startup that already has a current all-departments run is skipped, so re-running the script
costs only the startups that went stale. No search is recorded: no reviewer asked.

Usage:
    py -3 scripts/prewarm.py --applications            # every company in the applications file
    py -3 scripts/prewarm.py --names "Phena, Bliro"    # named startups
    py -3 scripts/prewarm.py --file watchlist.txt      # one name per line
    add --dry-run to list what would be evaluated, --limit N to cap a run

Speed report (measures; saves nothing, so the next measurement is not a cache hit):
    py -3 scripts/prewarm.py --report --names "Radical Dot, Wandelbots"
    add --cold to bypass the web and completion caches, --out DIR to keep each result as JSON
"""
from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

log = logging.getLogger("prewarm")


def plan(names, is_current) -> tuple[list[str], list[str]]:
    """(to evaluate, already current), de-duplicated case-insensitively, order kept."""
    seen, todo, fresh = set(), [], []
    for raw in names:
        name = str(raw or "").strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        (fresh if is_current(name) else todo).append(name)
    return todo, fresh


# The header rows a reviewer reads first; a blank one is what "missing info" means on the page.
_HEADLINE = ("founded_year", "funding", "employees_count", "hq", "website", "linkedin_url")


def report_row(name: str, result: dict, wall: float, partials: dict) -> dict:
    """One startup's speed report: wall time, when each section first reached the page, and
    where the model time went.

    ``partials`` maps a section to the seconds-since-start of each time it was emitted. The
    profile is emitted more than once (row-only, then researched); the researched one is the
    one that counts as "the profile is ready", so it is the last emission's time.
    """
    log_ = (result.get("token_usage") or {}).get("log") or []
    by_stage: dict = {}
    for c in log_:
        row = by_stage.setdefault(c["stage"], {"calls": 0, "cached": 0, "seconds": 0.0,
                                               "reasoning_tokens": 0})
        row["calls"] += 1
        row["cached"] += bool(c.get("cached"))
        row["seconds"] += c.get("duration_ms", 0) / 1000
        row["reasoning_tokens"] += c.get("reasoning_tokens", 0)
    kinds: dict = {}
    for c in log_:
        if not c.get("cached"):
            kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    # The run keeps enrichment's search stats only as this suffix on `engine` (pipeline._evaluate).
    timed_out = re.search(r"(\d+)/\d+ web queries timed out", str(result.get("engine", "")))
    profile = result.get("profile") or {}
    return {
        "name": name, "found": bool(result.get("found")), "wall_s": round(wall, 1),
        "profile_s": round(partials["profile"][-1], 1) if partials.get("profile") else None,
        "first_seen_s": {s: round(t[0], 1) for s, t in sorted(partials.items(), key=lambda kv: kv[1][0])},
        "calls": kinds,
        "cached_calls": sum(bool(c.get("cached")) for c in log_),
        "reasoning_tokens": sum(c.get("reasoning_tokens", 0) for c in log_),
        "by_stage": {k: {**v, "seconds": round(v["seconds"], 1)} for k, v in
                     sorted(by_stage.items(), key=lambda kv: -kv[1]["seconds"])},
        "timed_out": int(timed_out.group(1)) if timed_out else 0,
        "degraded": result.get("degraded") or [],
        "missing": [k for k in _HEADLINE if not str(profile.get(k, "")).strip()],
    }


def _report(names, cold: bool, out_dir: str) -> int:
    import json
    import threading
    if cold:
        # Read at import by core.llm, so it has to be set before the engine loads.
        os.environ["LLM_CACHE"] = "0"
    import core
    from api import main as app
    departments = app._all_departments(None)
    df = None if app._gd_key() else app._get_local_df()
    rows = []
    for name in names:
        partials: dict = {}
        lock = threading.Lock()
        started = time.time()

        def on_partial(section, _data, partials=partials, lock=lock, started=started):
            with lock:
                partials.setdefault(section, []).append(time.time() - started)
        result = core.evaluate(name, None, core.DEFAULT_TOOLS_CSV, do_web=True, df=df,
                               use_web_cache=not cold, on_partial=on_partial,
                               departments=departments)
        row = report_row(name, result, time.time() - started, partials)
        rows.append(row)
        print(json.dumps(row, indent=2))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, "".join(ch if ch.isalnum() else "_" for ch in name) + ".json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=1, sort_keys=True, default=str)
    found = [r for r in rows if r["found"]]
    if found:
        def pct(vals, q):
            vals = sorted(v for v in vals if v is not None)
            return vals[min(len(vals) - 1, int(q * len(vals)))] if vals else None
        print(f"\n{len(found)} evaluated · wall p50 {pct([r['wall_s'] for r in found], .5)}s "
              f"p90 {pct([r['wall_s'] for r in found], .9)}s · profile p50 "
              f"{pct([r['profile_s'] for r in found], .5)}s p90 {pct([r['profile_s'] for r in found], .9)}s")
    return 0


def _names(args) -> list[str]:
    names: list[str] = []
    if args.names:
        names += [n for n in args.names.replace(";", ",").split(",")]
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            names += fh.read().splitlines()
    if args.applications:
        from api import main
        df = main._get_local_df()
        if df is not None and "company_name" in df.columns:
            names += df["company_name"].astype(str).tolist()
    return names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--names", help="comma-separated startup names")
    ap.add_argument("--file", help="a file with one startup name per line")
    ap.add_argument("--applications", action="store_true", help="every company in the applications file")
    ap.add_argument("--limit", type=int, default=0, help="evaluate at most this many")
    ap.add_argument("--dry-run", action="store_true", help="list what would be evaluated, then stop")
    ap.add_argument("--report", action="store_true",
                    help="measure each evaluation instead of storing it (nothing is saved)")
    ap.add_argument("--cold", action="store_true", help="with --report: bypass the web and completion caches")
    ap.add_argument("--out", default="", help="with --report: write each result as JSON into this directory")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.report:
        names = plan(_names(args), lambda n: False)[0]
        return _report(names[:args.limit] if args.limit else names, args.cold, args.out)

    from api import main as app, store
    departments = app._all_departments(None)
    key = app._all_key(departments)
    todo, fresh = plan(_names(args), lambda n: store.latest_department_run(n, "*", key) is not None)
    if args.limit:
        todo = todo[:args.limit]
    log.info("prewarm: %d to evaluate, %d already current", len(todo), len(fresh))
    if args.dry_run:
        for name in todo:
            print(name)
        return 0

    def one(name):
        started = time.time()
        body = app.EvaluateBody(name=name)
        try:
            res = app._run_evaluation(name, body, {}, departments=departments, background=True)
            log.info("prewarm: %s done in %.0fs (run %s)", name, time.time() - started, res.get("run_id"))
            return True
        except Exception as exc:                     # one bad name must not stop the batch
            log.warning("prewarm: %s failed: %s", name, getattr(exc, "detail", exc))
            return False

    # Threads only wait here; flight.slot decides how many actually run.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, app.PREWARM_CONCURRENCY)) as ex:
        ok = sum(ex.map(one, todo))
    log.info("prewarm: %d of %d evaluated", ok, len(todo))
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
