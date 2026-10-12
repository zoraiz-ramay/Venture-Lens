# Comparisons & graphs: roadmap

As of 2026-10-12. Built from a codebase audit and a market scan. Nothing here is implemented yet.

**Recommendation:** build comparison in four steps. First fix how scores are served. Then ship a sourced
side-by-side compare table. Then add evaluation history and compare charts. Last, link competitors to our own
evaluations and map the portfolio. Every cell and every plotted point stays traceable to a source, and a gap is
shown as a gap, never as zero. That last property is what competing tools lack, and it is the reason to build this
in-house rather than lean on Tracxn or CB Insights.

## 1. Where we stand

Venture Lens cannot yet compare evaluated startups, but every stored run already holds the data a sourced
comparison needs.

**What exists today**

- **Databases grid** (`ui/src/pages/Explore.jsx`): rows can be multi-selected, but nothing uses the selection.
- **Solve's compare table** (`ui/src/pages/solve/CompareTable.jsx`): up to 4 applicants, application facts only, no scores.
- **One-run views only**: department ranking and pillar-vs-pillar, both on a single profile.
- **Charts**: hand-written SVG (`ui/src/components/charts.jsx`). No chart library is installed, and Siemens iX ships no chart component.

**What a run can be compared on.** These fields are as served after `core/assessment.py::hydrate`, which re-scores
every stored run on the current rubric.

| Area | Fields | Often empty when |
| --- | --- | --- |
| Total | `assessment.total`, four weighted components | Legacy runs; any component missing |
| Siemens Fit | Empower / Connect / Collaborate scores, bands, cited criteria | No pillar assessable |
| Fit per department | `departments.ranked` | Run not saved for all departments |
| Traction | 4 divisions: points, value, source, confidence | Revenue is the most often unknown |
| Team & Ecosystem | 4 criteria, 0–5 each | No model, or output failed validation |
| Market | Size, CAGR, relevance, 0–5 each | Older runs carry the model's number |
| Landscape | Competitors, funded peers, market size, each sourced | Runs before the landscape stage |
| Headcount | Dated points, each sourced | Young or little-known companies |
| Evidence strength | Verified/total facts, retrieval dates | Always present |

**Fix these before drawing any chart.** Each one would make a chart disagree with the profile, or plot a gap as zero:

1. `api/store.py::list_runs` serves the stale saved `final_score` while the profile re-derives the total.
2. Private lists report a missing total as 0 (`api/workspace.py:74`).
3. `RadarChart` draws a missing value as 0, against its own documented promise.
4. Legacy runs show the model's holistic number as Siemens Fit. They must never share an axis with current-rubric scores.
5. The key-figure tiles count runs, not companies, and "Avg Fit Score" actually averages the total (backlog UI-16).

The local database used for this plan held no runs, so the empty rates above are reasoned from the code. Phase 0
measures them on production.

## 2. What the market does

Market tools compare startups through a headline score with visible components, a peer benchmark, time series, and
2x2 or market-map visuals. Almost none show where a number came from or mark missing data.

| Tool | Compares on | Signature visual | Missing data |
| --- | --- | --- | --- |
| CB Insights | Mosaic (0–1000), Commercial Maturity (1–5) | ESP 2x2, market maps | Not documented |
| Crunchbase | Growth and Heat scores, competitors | Performance time series, landscape map | Gated by plan |
| PitchBook | Side-by-side comps, multiples | Comp tables, CSV/PNG export | Not documented |
| Dealroom | Signal score incl. completeness; peer growth benchmark | Score breakdown card | Ranges, completeness score |
| Specter | Headcount and traffic deltas | Table columns | Confidence per result |
| GlassDollar | Match against must-have criteria | Criteria grid, pilot board | Not documented |
| StartUs Insights | Chosen dimensions | Bubble, trend matrix, treemap | Users can edit data (breaks provenance) |

Lessons for the design:

- **Pick charts that are easy to read accurately.** Position on a shared axis reads more accurately than angle or
  area, so prefer dot plots and bars to overlaid radar charts.
- **If radar charts are used**, draw one small radar per startup with a fixed axis order. Never overlay them.
- **Show uncertainty explicitly**: hollow marks for weak evidence, "n sources" counts, ranges where a value is uncertain.
- **Keep data coverage separate from quality.** A sparse profile is not a weak company.
- **Anchor comparison to a decision.** Corporate venture-client programmes (BMW, Bosch, Siemens) compare against a
  problem, a department and a pillar, not against "the best startup".

## 3. Ranked opportunities

Each opportunity is scored 0–2 on five criteria from `contract/feature-rubric.md`: impact, evidence readiness,
frequency, trust and cost.

| # | Opportunity | Score /10 | Size | Verdict |
| --- | --- | --- | --- | --- |
| A | Evaluation history: what changed between refreshes | 9 | M | Build |
| B | Compare 2–5 evaluated startups, table first | 8 | M | Build |
| C | Compare charts: component dot plot, traction bars, department grid | 7 | M | Build after B |
| D | Link landscape competitors to our own evaluations | 7 | S–M | Build |
| E | Portfolio map in Databases: Traction × Siemens Fit | 7 | S–M | Build after phase 0 |
| F | Where this startup sits in the portfolio | 7 (weak) | S | Needs a product-owner decision |
| G | Funding vs. fit scatter | 6 | S | Only if coverage shows enough evidenced funding |
| H | Portfolio headcount growth | 5 | M | Not now: series too sparse |
| — | Pillar-mix donut, count dashboards | — | — | Reject: fails the rubric's "changes a decision" gate |
| — | Overlaid radar compare | — | — | Reject: hard to read accurately, and it currently draws gaps as 0 |

## 4. The top five, specified

**A. Evaluation history (M).** "Before I act on this Collaborate, did earlier evaluations agree, and what moved?"

- **API:** new `GET /api/runs/{id}/history`, built on `store.prior_runs_for`.
- **Same company or not:** decided by domain, not name, so namesakes are excluded.
- **Data:** each run is re-scored by `hydrate` on the current rubric, capped at 30 runs.
- **UI:** an "Evaluation history" section under Scoring & Fit, shown only when there are 2 or more runs.
  - A total-over-time line with a pillar label at each point.
  - One sparkline per component.
  - A table that lists replaced values with their sources.
- **Missing data:** a pending total is a break in the line with a hollow marker, never 0.

**B. Compare table (M).** "Show me three shortlisted startups side by side, every number traceable."

- **Entry points:**
  - "Compare (n)" in the Databases toolbar, for 2–5 selected rows.
  - Solve's shortlist footer.
  - The peer view (D).
- **Route:** `/explore/compare?ids=…`, so the URL is the state.
- **API:** no new endpoint. Fetch each run and reuse the profile's presentation helpers, so compare and profile cannot
  disagree.
- **Rows:**
  - header and pillar;
  - total and components;
  - Siemens Fit per pillar;
  - Collaborate per department;
  - traction divisions with badges;
  - team;
  - market;
  - SFS financing;
  - header facts with provenance;
  - evidence strength and age.
- **Missing data:** "—" when unknown, "pending" when incomplete, "Not assessed" when not assessed. Legacy columns are
  shown but not scored.
- **No model-written verdicts.**

**C. Compare charts (M, after B).** All three are SVG on iX chart tokens:

- **Component dot plot:** one marker per startup, with a letter as well as a colour so colour is never the only cue.
- **Traction division bars:** earned, evidenced-not-earned, and hatched where not evidenced.
- **Department × startup Collaborate grid:** 0–9 per cell.

Each chart carries an aria-label that states every value. Threshold lines are drawn only where the engine defines a
band.

**D. Peer cross-reference (S–M).** "Two of this startup's cited competitors are already evaluated; show me."

- **API:** new `GET /api/runs/{id}/peers` matches landscape names to stored runs.
- **Labelling:** a match is labelled "matched by name", and the matched domain is shown.
- **Amounts:** parsed in Python, with an assumed currency flagged.
- **UI:** an "Evaluated" chip on competitors, plus "Compare with evaluated competitors", which opens B.

**E. Portfolio map (S–M).** "Which of my startups are high-fit and well-evidenced, and which are high-fit but thin?"

- **Chart:** x is Traction and y is Siemens Fit. Filled markers are well evidenced, hollow markers are thin.
- **Data:** the same filtered rows as the grid.
- **Missing axes:** rows missing an axis go into a "Not plotted" list with the reason.
- **Small lists:** the map is hidden below 8 rows.
- **Placement:** a tab in Databases (`?show=map`).

## 5. Roadmap

| Phase | Work | Gate to the next phase |
| --- | --- | --- |
| 0. Foundations (S each) | Fix the five defects in section 1; add `scripts/field_coverage.py` (advisory, run on production); correct `docs/data-and-scoring-map.md` §7–8 | Grid totals match the profile; coverage numbers known |
| 1. Compare table | B, through the feature cycle | Product owner approves the proposal |
| 2. History and charts | A (independent, could move to phase 1), C (needs B) | Compare in daily use |
| 3. Peers and portfolio | D (needs B), E (needs phase 0) | Coverage supports the axes |
| 4. Gated extras | G funding scatter; F portfolio position; round-size strip | Coverage results and product-owner decisions |

## 6. Guardrails every comparison keeps

- **Missing is never zero.** Never plot or average a missing value as 0. That covers a pending total, an excluded
  traction division, an unassessed pillar and a legacy run.
- **Separate legacy numbers.** Model-scored legacy numbers never share an axis with current-rubric scores.
- **No model-written verdicts.** No "A beats B" text from the model; summaries are templated from the numbers.
- **No invented lines.** No threshold or quadrant lines the engine does not define.
- **Honest peer matches.** Peers are labelled "matched by name". A peer's round is not a startup's total raised, so
  the two are never plotted on one axis unlabelled.
- **Age from the source.** Evidence age is computed from `retrieved_at`.
- **Unverified stays unverified.** Model-recalled HQ and links stay labelled "unverified" in every cell.
- **Exports keep sources.** Exports carry their source list.

## 7. Decisions needed from the product owner

- [ ] Approve the compare-table proposal (B) so the feature cycle can start.
- [ ] May reviewers see a team-wide score distribution (F), or only their own list? Lists are private today.
- [ ] Add contract rows for the new journeys (PROF-13 history, EXP-10 compare, CMP-01…). Agents may not edit `contract/feature-inventory.md`.
- [ ] Run `field_coverage.py` against the production database once phase 0 lands.

## 8. Sources

- [CB Insights Mosaic](https://www.cbinsights.com/mosaic-score) · [Commercial Maturity](https://www.cbinsights.com/commercial-maturity/?lv=true) · [Market Map Maker](https://www.cbinsights.com/research/team-blog/market-map-maker-autobuild-is-here)
- [Crunchbase predictions and insights](https://support.crunchbase.com/hc/en-us/articles/39699456887699)
- [PitchBook comps](https://pitchbook.com/blog/how-to-build-better-comps-with-pitchbook)
- [Dealroom Signal](https://dealroom.co/knowledge/Navigate/navigate-dealroom-signal)
- [Specter](https://tryspecter.com/) · [Harmonic](https://harmonic.ai/platform) · [GlassDollar](https://glassdollar.com/)
- [StartUs Insights data visualisation](https://www.startus-insights.com/innovators-guide/data-visualization-innovation/)
- [Cleveland & McGill, graphical perception](https://vis.arc.vt.edu/%7Enpolys/projects/safas/science.pdf) · [Radar chart critique](https://blog.scottlogic.com/2011/09/23/a-critique-of-radar-charts) · [Kiviat Defense (2023)](https://par.nsf.gov/biblio/10536528)
- [FT Visual Vocabulary](https://github.com/Financial-Times/chart-doctor/tree/main/visual-vocabulary) · [Uncertainty visualization survey](https://arxiv.org/pdf/2301.07687)
- [Siemens for Startups](https://news.siemens.com/es-es/siemens-for-startups/) · [BMW Startup Garage](https://www.bmwgroup.com/en/innovation/innovation-network/startup-garage.html) · [Open Bosch](https://bosch.ventures/open-bosch) · [Bundl venture client guide](https://www.bundl.com/knowledge/articles/how-to-implement-a-venture-client-program-inside-a-large-corporation)

Vendor feature detail comes mostly from public marketing and help pages; several help centres required a login, so
treat vendor rows as indicative.
