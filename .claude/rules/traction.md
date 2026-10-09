---
paths:
  - "core/traction.py"
  - "core/traction_lookup.py"
  - "core/text.py"
  - "core/config.py"
---

## Traction is a points rubric, not a judgement

`core/traction.py` scores the product owner's table — Funding 30 · Customers 30 · Revenue 30 ·
Employees 10, bands in `config.TRACTION_RUBRIC` — deterministically, from facts the run already
holds. It costs nothing per run, is recomputed for every stored run as it is read
(`with_traction`, called from `api/store.py` and `api/workspace.py`), and replaces the model's
holistic `dimensions.traction`; the model's number stays beside it as `traction_llm`, because the
gap between the two across the corpus is the calibration signal.

- **An unevidenced division is dropped, never scored zero**, and the rest are normalised over what
  remains. Confidence is the share of the 100 points that were evidenced (employees only → 10%),
  so a score normalised from one small division reads as thin rather than strong. A *sourced*
  "pre-revenue" is the opposite case: a real 0 that stays in the denominator.
- **The model supplies words, never numbers.** The one extraction call it rides
  (`_extract_commercial_posture`) returns a revenue *quote*, which must appear verbatim in the
  evidence, and Python parses the figure out of it (`text.parse_money`). A model asked for a
  revenue number rounds, converts and occasionally invents one.
- **Labelling a grounded customer "big name" is identifying, not evaluative** — the same kind of
  fact as a programme's prestige tier (`_grade_programs`). Whether a company is a customer at all
  stays with `_ground_customers`; the model may only label names already in that list, never add
  one, and `config.NOTABLE_COMPANIES` is the offline baseline it can upgrade but not overrule.
  This is not a third model-memory exception, and must not be widened into one.

- **The division panels look up more on request, and none of it scores.** `core/traction_lookup.py`
  (`POST /api/runs/{id}/lookup/funding|headcount`) fetches funding rounds with investor profiles, and
  sourced headcounts — the reviewer's Tracxn first, the model's own web search second, never its
  memory. The model only transcribes the gathered prose; a value is kept only if that prose states
  it, and a web row only if it cites a source the search used. The result is stored and served to
  the next reader (see "What a run produces is stored" below); it is never written into the run's
  `result_json`, so it cannot move a score.
