---
paths:
  - "benchmarks/**"
  - "scripts/gates.sh"
---

## Measuring a scoring change instead of arguing about it

`py -3 -m benchmarks.routing_eval` replays the **current** engine over every stored run and scores
the result against human labels in `benchmarks/labels.json`. `benchmarks/replay.py` rebuilds the
inputs from `result_json` and re-runs `score_startup` / `route` — no network, no model, no cost —
so a change to the decision layer is measurable across the whole history in a second.

What is deliberately *not* replayed: enrichment, extraction, fit and trend. Those are the model's
reading of the web at the time, and re-running them would move the inputs under the experiment.

**The labels are the gap, and only a reviewer can close it.** `--seed` adds an unlabelled row per
company; the report leads with coverage and refuses to compute anything until a `pillar` is filled
in, because a precision of 1.00 over three labels is not a result. Once ~30 are labelled, swap the
advisory line in `scripts/gates.sh` for `--min-f1 <floor>` and make a routing regression fail.
