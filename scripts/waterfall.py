#!/usr/bin/env python3
"""Draw where a stored evaluation's wall time went: branches, model calls and search waves.

The token log says how long each model call took; it cannot say which calls ran one after another,
and that chain is what a slow run is made of. Measured before this existed, the profile branch
took ~100s while its ten completions summed to ~12s — the rest was search waves queued behind
each other, which no per-call duration shows.

Reads ``result_json.token_usage`` (``timeline`` + ``log``), so only runs saved after the timeline
existed can be drawn.

Usage:
    py -3 scripts/waterfall.py                 # the newest run that has a timeline
    py -3 scripts/waterfall.py 97              # one run
    py -3 scripts/waterfall.py 97 --stage profile   # every call and wave inside one branch
    py -3 scripts/waterfall.py --json result.json   # an evaluation result saved to a file
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WIDTH = 60


def _load(db: str, run_id: int | None) -> tuple[int, str, dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    query = "SELECT id, company, result_json FROM runs " + ("WHERE id=?" if run_id else "ORDER BY id DESC")
    for rid, company, raw in con.execute(query, (run_id,) if run_id else ()):
        usage = (json.loads(raw or "{}").get("token_usage") or {})
        if usage.get("timeline"):
            return rid, company, usage
        if run_id:
            sys.exit(f"run {rid} has no timeline (saved before it existed, or made no model call)")
    sys.exit("no stored run has a timeline yet — evaluate a company first")


def _bar(start: int, dur: int, span: int) -> str:
    a = round(start / span * WIDTH)
    b = max(a + 1, round((start + dur) / span * WIDTH))
    return " " * a + "#" * (b - a)


def _row(label: str, start: int, dur: int, span: int, note: str = "") -> str:
    return f"{label:<28.28} {start / 1000:6.1f}s {dur / 1000:6.1f}s |{_bar(start, dur, span):<{WIDTH}}| {note}"


def _calls(usage: dict) -> list[dict]:
    return [{**c, "kind": c.get("kind", "completion")} for c in usage.get("log") or []
            if isinstance(c.get("start_ms"), int)]


def _covered(events: list[dict]) -> int:
    """Milliseconds inside at least one of ``events`` — overlapping calls are counted once."""
    total, end = 0, -1
    for e in sorted(events, key=lambda e: e["start_ms"]):
        a, b = e["start_ms"], e["start_ms"] + e["duration_ms"]
        if b > end:
            total += b - max(a, end)
            end = b
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_id", nargs="?", type=int)
    ap.add_argument("--stage", help="list every call and search wave inside this branch")
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "runs.db"))
    ap.add_argument("--json", help="read an evaluation result from this file instead of the database")
    args = ap.parse_args()
    if args.json:
        with open(args.json, encoding="utf-8") as f:
            result = json.load(f)
        rid, company, usage = "-", result.get("company", ""), result.get("token_usage") or {}
        if not usage.get("timeline"):
            sys.exit(f"{args.json} has no timeline")
    else:
        rid, company, usage = _load(args.db, args.run_id)
    timeline, calls = usage["timeline"], _calls(usage)
    events = timeline + calls
    span = max((e["start_ms"] + e["duration_ms"] for e in events), default=1) or 1
    print(f"run {rid} · {company} · {span / 1000:.1f}s from resolve to last recorded event\n")
    print(f"{'branch':<28} {'start':>7} {'took':>7}")
    sections = sorted((e for e in timeline if e["kind"] == "section"), key=lambda e: e["start_ms"])
    for s in sections:
        mine = [e for e in events if e["kind"] != "section" and e.get("stage") == s["stage"]]
        model = _covered([c for c in mine if c["kind"] != "search_wave"])
        search = _covered([w for w in mine if w["kind"] == "search_wave"])
        print(_row(s["stage"], s["start_ms"], s["duration_ms"], span,
                   f"model {model / 1000:.0f}s · search {search / 1000:.0f}s"))
    last = max(sections, key=lambda s: s["start_ms"] + s["duration_ms"], default=None)
    if last:
        print(f"\nends last: {last['stage']} at {(last['start_ms'] + last['duration_ms']) / 1000:.1f}s")
    stage = args.stage or (last or {}).get("stage")
    if not stage:
        return 0
    inner = sorted((e for e in events if e["kind"] != "section" and e.get("stage") == stage),
                   key=lambda e: e["start_ms"])
    print(f"\ninside {stage}:")
    for e in inner:
        if e["kind"] == "search_wave":
            note = (f"{e.get('queries', 0)} queries, {e.get('cached', 0)} cached, "
                    f"queued {e.get('queued_ms', 0) / 1000:.1f}s"
                    + (f", {e['timed_out']} TIMED OUT" if e.get("timed_out") else ""))
            label = "search wave"
        else:
            note = (f"{e.get('reasoning_tokens', 0)} thinking"
                    + (" · cached" if e.get("cached") else "")
                    + (f" · {e.get('attempts')} attempts" if (e.get("attempts") or 1) > 1 else "")
                    + ("" if e.get("ok", True) else f" · FAILED {e.get('reason', '')}"))
            label = e["kind"]
        print(_row(label, e["start_ms"], e["duration_ms"], span, note))
    busy = _covered(inner)
    sec = next((s for s in sections if s["stage"] == stage), None)
    if sec:
        print(f"\n{busy / 1000:.1f}s of {sec['duration_ms'] / 1000:.1f}s covered by a recorded call or wave; "
              f"the rest is page fetches, waits on other branches' futures, and Python")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
