#!/usr/bin/env python3
"""Report how much the collected facts change between evaluations of the same company.

`dimension_variance.py` asks whether a dimension tells companies apart; this asks the opposite
question of the data underneath: does re-evaluating ONE company bring back the same founders,
investors, programmes, competitors and headline values? Measured on Radical Dot (three Refreshes,
October 2026), the headline values held, but the founders went 2 → 3 → 5 for a company with two
founders (the model wrote "Alexandre", "Alex (surname not stated)", "Andi (surname not stated)"),
and each run found a different two competitors and never the same one twice.

Two views per list, because carry-forward hides instability:

* **found** — what the run itself found (items without ``last_confirmed_at``). This is the
  collection's own repeatability.
* **shown** — what the run displayed, carried items included. Growth here is fine; a drop or a
  duplicate is not.

Usage:
    py -3 scripts/fact_stability.py                 # every company with 2+ runs
    py -3 scripts/fact_stability.py "Radical Dot"   # one company
    py -3 scripts/fact_stability.py --report        # advisory: always exit 0
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core.carry_forward import degraded  # noqa: E402

DB = os.getenv("RUNS_DB", os.path.join(os.path.dirname(__file__), "..", "data", "runs.db"))

# (label, path into the result, the item field that names it)
LISTS = [
    ("founders", ("deep_profile", "founders"), "name"),
    ("key_team", ("deep_profile", "key_team"), "name"),
    ("advisors", ("deep_profile", "advisors"), "name"),
    ("investors", ("deep_profile", "commercial", "investors"), "name"),
    ("active_investors", ("trend", "landscape", "active_investors"), "name"),
    ("programs", ("deep_profile", "programs"), "name"),
    ("competitors", ("trend", "landscape", "competitors"), "name"),
    ("funded_peers", ("trend", "landscape", "funded_peers"), "company"),
]
SCALARS = [
    ("hq", ("profile", "hq")),
    ("founded", ("profile", "founded_year")),
    ("funding", ("profile", "funding")),
    ("employees", ("profile", "employees_count")),
    ("website", ("profile", "website")),
    ("linkedin", ("deep_profile", "linkedin_url")),
    ("crunchbase", ("deep_profile", "crunchbase_url")),
    ("market_size", ("trend", "landscape", "market_size", "value")),
]


def _get(obj, path):
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _names(items, field: str, fresh_only: bool) -> list[str]:
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if fresh_only and item.get("last_confirmed_at"):
            continue
        name = _norm(item.get(field) or item.get("name"))
        if name:
            out.append(name)
    return out


def _jaccard(a: set, b: set) -> float | None:
    return None if not a and not b else len(a & b) / len(a | b)


def _possible_duplicates(names: list[str]) -> list[tuple[str, str]]:
    """People listed twice under different forms: one name's first token is a prefix of another's."""
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ta, tb = a.split()[0], b.split()[0]
            if a != b and len(min(ta, tb, key=len)) >= 3 and (ta.startswith(tb) or tb.startswith(ta)):
                pairs.append((a, b))
    return pairs


def load(db_path: str, company: str = "") -> dict[str, list[dict]]:
    """Every company's runs, oldest first."""
    if not os.path.exists(db_path):
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id, company, result_json FROM runs ORDER BY id").fetchall()
    finally:
        con.close()
    runs: dict[str, list[dict]] = defaultdict(list)
    for rid, name, payload in rows:
        if company and _norm(name) != _norm(company):
            continue
        try:
            result = json.loads(payload or "{}")
        except ValueError:
            continue
        if degraded(result):
            continue          # most model calls failed: the app ignores it too (carry_forward.degraded)
        result["_run_id"] = rid
        runs[name].append(result)
    return runs


def analyse(results: list[dict]) -> dict:
    """Per-list Jaccard between consecutive runs (found and shown), flickers, changed scalars."""
    report = {"runs": [r["_run_id"] for r in results], "lists": {}, "scalars": {}, "duplicates": []}
    for label, path, field in LISTS:
        found = [set(_names(_get(r, path), field, True)) for r in results]
        shown = [set(_names(_get(r, path), field, False)) for r in results]
        flicker = sorted(n for n in set().union(*found)
                         if any(n in found[i - 1] and n not in found[i] and any(n in f for f in found[i + 1:])
                                for i in range(1, len(found))))
        report["lists"][label] = {
            "found_counts": [len(s) for s in found],
            "shown_counts": [len(s) for s in shown],
            "found_jaccard": [_jaccard(found[i - 1], found[i]) for i in range(1, len(found))],
            "shown_dropped": [sorted(shown[i - 1] - shown[i]) for i in range(1, len(shown))],
            "flicker": flicker,
        }
    for result in results:
        people = [n for label in ("founders", "key_team", "advisors")
                  for n in _names(_get(result, ("deep_profile", label)), "name", False)]
        report["duplicates"].extend(p for p in _possible_duplicates(people) if p not in report["duplicates"])
    for label, path in SCALARS:
        values = [_get(r, path) for r in results]
        if len({json.dumps(v, sort_keys=True, default=str) for v in values}) > 1:
            report["scalars"][label] = values
    return report


def _fmt(values) -> str:
    return "[" + ", ".join("—" if v is None else f"{v:.2f}" for v in values) + "]"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("company", nargs="?", default="")
    parser.add_argument("--report", action="store_true", help="advisory: always exit 0")
    args = parser.parse_args()

    companies = {name: rs for name, rs in load(DB, args.company).items() if len(rs) >= 2}
    if not companies:
        print("fact stability: no company has 2+ stored runs yet — nothing to compare.")
        return 0
    problems = 0
    for name, results in sorted(companies.items()):
        rep = analyse(results)
        print(f"\n{name} — runs {rep['runs']}")
        for label, row in rep["lists"].items():
            if not any(row["found_counts"]) and not any(row["shown_counts"]):
                continue
            line = (f"  {label:13} found {row['found_counts']} jaccard {_fmt(row['found_jaccard'])}"
                    f" · shown {row['shown_counts']}")
            dropped = [d for d in row["shown_dropped"] if d]
            if dropped:
                line += f" · DROPPED {dropped}"
                problems += 1
            if row["flicker"]:
                line += f" · flicker {row['flicker']}"
            print(line)
        for a, b in rep["duplicates"]:
            print(f"  possible duplicate person: {a!r} / {b!r}")
            problems += 1
        for label, values in rep["scalars"].items():
            print(f"  {label} changed: {[str(v)[:40] if v else '—' for v in values]}")
            problems += 1
    print(f"\nfact stability: {problems} finding(s) across {len(companies)} compan"
          f"{'y' if len(companies) == 1 else 'ies'}.")
    return 0 if args.report or not problems else 1


if __name__ == "__main__":
    sys.exit(main())
