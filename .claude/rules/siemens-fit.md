---
paths:
  - "core/pillar_match.py"
  - "core/pillars.py"
  - "core/tool_check.py"
  - "core/tool_search.py"
  - "core/assessment.py"
  - "core/market.py"
  - "core/department_needs.py"
  - "core/departments.py"
  - "core/catalogs.py"
  - "core/team_ecosystem.py"
  - "api/assessments.py"
  - "scripts/build_tool_index.py"
  - "scripts/compare_tool_retrieval.py"
  - "ui/src/pages/profile/scoring/**"
---

## Siemens Fit is three pillar assessments, scored per department

`core/pillar_match.py` (model half) and `core/pillars.py` (pure half) score Empower against
`siemens_tools.csv`, Collaborate against the selected department's needs, and Connect against
`data/siemens_xcelerator_data.xlsx` — three criteria each, 0–3, against the product owner's
anchors. Siemens Fit is `round(100 × best assessed pillar total / 9)`; the route is decided from
the pillar bands by `recommend`, and no model can override it (`decision_research` runs only for
department-less engine calls).

- **The pillar match thinks at `medium`** (`pillar_match.PILLAR_REASONING`). Gemini 2.5 Flash
  judged it without thinking; Gemini 3.5 Flash Lite without thinking scored Connect well under
  Flash on identical research, and at `medium` came back within Flash's own run-to-run range.
  `validate_match` restores a catalog id written without its namespace (`di-02-1` →
  `need:di-02-1`) only when exactly one shortlisted id fits — Flash Lite drops the prefix often.
- **The model proposes; `validate_match` decides.** Integer scores 0–3, citations that exist,
  catalog ids that were shortlisted, a positive score only with both, and the third criterion
  (actionability / ecosystem value) capped at 1 unless the pillar's statement names a matched entry
  and carries a next step. Thematic similarity alone cannot earn a route.
- **An Empower tool must exist before it is recommended.** `siemens_tools.csv` has carried names
  that are not Siemens products. `core/tool_check.py` runs one small web search per cited tool;
  `not_found` re-runs the match without it (`pillar_match._with_real_tools`, two rounds), and
  `unchecked` — no search available, or an unreadable answer — keeps the tool, because a failed
  search is not evidence of absence. Checks are cached only when conclusive, stored in
  `tool_checks`, and the `not_found` ones are the admin page's "could not be verified" list:
  fixing that list is a catalog edit, not code.
  The check cache is **shared** (`web._cached(shared=True)`): neither a Refresh nor a private
  Tracxn run bypasses it, because a tool's existence is not evidence about the startup — a
  refresh used to re-search every cited tool, 5–10 gateway web searches per run. Conclusive checks
  live 180 days (`TOOL_CHECK_TTL_DAYS`), not the evidence cache's 7. A tool already on record as
  `not_found` is dropped from Empower's shortlist before the match (`tool_check.known_missing`),
  so only a tool cited for the first time can still cost a check and a re-match. The catalog has
  ~2,900 tools, so it is checked as tools are cited, never swept.
- **Tool shortlists are semantic + word search** (`core/tool_search.py`), for both the fit stage
  (80) and Empower (20). Word overlap alone showed Wandelbots a shortlist without Tecnomatix or
  Process Simulate but with a cybersecurity certification that shared the word "industrial". The
  catalog is embedded once into `data/tool_index/` (`scripts/build_tool_index.py`, ~30s; rerun
  after any change to `siemens_tools.csv`), the startup once per run, and the two rankings are
  fused by reciprocal rank over the top 20 of each (`FUSE_DEPTH`, measured by
  `scripts/compare_tool_retrieval.py`). The index is used only when catalog checksum, model and
  size all match; otherwise — or when the provider cannot embed — it is words alone and the pillar
  says so (`retrieval.method == "words"`). Each index folder holds one file pair per embedding
  model (`vectors.<model>.npy` / `meta.<model>.json`): `gemini-embedding-001` for local runs on
  Gemini, `google_gemini-embedding-2` for the Siemens gateway in production. Rebuilding after a
  catalog change means rebuilding **both** — the gateway one from inside the Siemens network.
- **Empower ranks up to 5 tools** (`recommended_tools`), held to the criteria's bar by
  `pillars._recommended_tools` (shortlisted, relation, reason, a real citation) and existence-checked;
  one that cannot be found is dropped without a re-match. They never move the score. Fit also lists
  up to 5, but everything that decides from `fit.matches` reads the first one or three.
- **Connect is Siemens partnering with the startup itself, and asks first how many Xcelerator
  sellers already sell the same thing** (rubric v5). The sellers are embedded into
  `data/xcelerator_index/` (`scripts/build_tool_index.py --catalog xcelerator`, ~10s; rerun after
  any change to the workbook), the 30 nearest the startup's offering are fused with word overlap
  (`config.CONNECT_NEIGHBOURS`), and the model labels each `same | different` by **what it sells**
  in the Connect call it already makes — shared buyers or industries are `different`. The model
  never gives the count. Similarity alone cannot count: on the stored index every seller sits
  within 0.58–0.70 of the startup, and Radical Dot's third-nearest was a battery recycler. A
  skipped seller counts as different, because a "same" moves the startup into the crowded case.
  - **Crowded (2+ same):** Connect scores from the count alone — 2–3 → 3/9, 4–7 → 2, 8–11 → 1,
    12+ → 0 (`config.CONNECT_CROWDED`, `pillars.crowded_level`), always No match. The criteria
    are still computed and shown, marked `counted: False`.
  - **Open (0–1 same):** Industry & topic fit (model; 2+ cites an industry and a topic) + Market
    signals (Python: one point per `market:` record `pillar_match.market_records` builds from the
    trend stage's cited size, CAGR and funded peers, `config.CONNECT_GOOD_MARKET` — never the
    model's call) + Offering (model, third, so it gates Strong). Offering needs research citations
    but **no catalog id**: what a startup sells is a fact about the startup, and requiring one
    zeroed Celonis's process-mining offering. With exactly one same seller, Offering reaches 3
    only with a cited differentiator against it.
  - The 5 nearest same sellers are named and the rest are "+ N more" (`similar` on the pillar; "or
    more" when all 30 searched were same). Tune the bands from `connect.similar_count` in
    `scripts/dimension_variance.py`, not by argument.
  - v2–v4 scored an Ecosystem gap and an Ecosystem value, and v3 an ecosystem **audience** of
    sellers who would resell the startup — removed because Siemens partners with it directly.
    Stored runs keep those rows. The UI renders them as history, restated as a Siemens partnership
    (`ui/src/pages/profile/scoring/connectOpportunity.js`): the audience is one line of names, never
    a paragraph per seller, with a pointer to re-evaluate. A plain search re-assesses a stored run
    whose `assessment_key` predates the rubric, so re-searching the company is enough.
  - `pillars.connect_case` turns the same facts into the verdict the opportunity map's third step
    ("Partnership case") shows — "Partnering makes sense" / "Worth exploring" / "does not make
    sense", from the band — with each point for or against and its source: templated, never
    generated.
- **Collaborate ranks a department's needs by meaning** (`data/needs_index/`, all ~50 needs of the
  requirements workbook, `scripts/build_tool_index.py --catalog needs`; rerun after any change to
  it). Ranked once per evaluation in `prepare_pillars`, filtered per department. Not fused with
  words as tools are: needs have no product name to quote, and on Radical Dot words put "Defect
  Detection" above "Advanced Recycling". The top three are kept as `closest_needs`, which the map
  lists first when nothing matched; they never move the score.
- **Unassessed is not a no-match.** No grounded concept, no catalog, no model or output that fails
  validation → `unassessed`. Only assessed pillars feed Siemens Fit, and Pass needs all three.
- **A Collaborate at 0/9 is not a route.** When every department scores 0, `build_all` sets
  `recommended: null` with `basis: "no_collaborate_match"` (distinct from
  `no_collaborate_assessment`), and `pillars.siemens_fit` leaves a 0/9 Collaborate out, so the fit
  comes from Empower or Connect — or stays pending, never 0, if neither was assessed. It still
  counts as assessed for Pass. Both are re-derived on read, so stored runs follow the rule.
- **A run is one startup assessed for every department.** Nobody picks a department before
  searching — that asked the reviewer to guess what the evaluation exists to find. Research,
  Empower and Connect are department-independent and run once; Collaborate is matched against each
  department's needs in parallel (`pillar_match.for_department`), and `assessment.build_all` heads
  the run with the department whose needs it answers best (`departments.recommended`; null, never
  a default, when no Collaborate could be assessed or every department scored 0/9). The run's own `department` / `assessment` are
  the recommended one's, so single-department readers keep working; `departments.ranked` holds every
  department's full assessment, hydrated on read, and the Scoring tab switches between them with
  `?dept=` and no call. Stored under `runs.department_id = '*'`, with `assessment_key` covering the
  rubric, the shared catalogs and EVERY department's needs — one department's needs changing makes
  the run stale. Fresh research from an older run is re-assessed (`pipeline.assess_departments`)
  and always saved as a new run. One-department runs and runs with no department are history,
  readable and never rewritten.
- **An existing Siemens relationship adds +1 to Collaborate** (`pillars.with_relationship_bonus`,
  applied in `pillar_match.assess_pillar`, so every path — fresh, re-assessed, one department or
  all — gets it before packaging computes Siemens Fit and the route). Engagements come from
  `profile._siemens_relationship`: deterministic, no model, `_rel_grounded`'s bar — the company,
  Siemens (a unit from `programs.SIEMENS_UNITS`, or a product: a curated brand, or any catalog
  tool name beside the word "Siemens") and an engagement phrase in ONE result — with two vetoes
  around the Siemens mention: employment ("previously at Siemens", a founder's CV) and comparison
  ("alternative to", "vs"). Types: customer, pilot, joint project, partner, supplier, investment
  (next47, SFS), programme (Xcelerator), and **uses a Siemens product**. Product owner's rules:
  self-asserted counts (the startup's own site; labelled `claimed`), past engagements count, every
  type and unit counts the same, and it applies even to a 0/9 Collaborate (→ 1/9) — never to an
  unassessed one. **Only an engagement with an http source earns the point**; an application-only
  mention is listed, says it did not count, and earns nothing. The model's criteria are untouched;
  `model_total` and `relationship_bonus` sit beside them, the band is re-derived (6 → 7 is Strong
  only with Actionability ≥ 2), and the route's reasons say a point was added. The point is the
  same for every department, so a tie goes to the department whose unit the startup works with
  (`assessment._collaborate_rank`). Stored research has no `siemens_engagements` until the
  company's next full research, so a re-assessed old run gets no bonus — never a wrong one.
- **Collaborate reads Siemens' stated needs** from `data/Startup_Evaluator_Departmental_Requirements.xlsx`
  (`core/department_needs.py`): one catalog entry per capability, with its category, description and
  keywords; the 15 sharing most concepts with the startup go to the model when a department states
  more. Such a department is `source: workbook`, not demo. Without the file, departments fall back
  to five example keywords and Collaborate is **provisional** — scored, included, labelled so. An
  admin's `PUT /api/departments/{id}` overrides either. Any change to the needs changes the checksum.

`core/assessment.py` computes `total = 0.30 traction + 0.35 Siemens Fit + 0.20 (Team & Ecosystem
points × 5) + 0.15 market` on every read (`hydrate`); any missing component leaves it pending, never
0. The model's holistic `final_score` and `siemens_fit` are kept as `llm_final_score` /
`llm_siemens_fit` and shown only as diagnostics.

**Market** (`core/market.py`, 15%) is three criteria 0–5: size and growth are banded in Python from
the figure the trend stage cited (`trend.landscape.market_size`, EUR bands; CAGR bands), and only
without a cited figure may the model judge them — capped at 2. Strategic relevance is the model's,
and 4+ must name a Siemens Xcelerator industry or topic. `market_score = points ÷ 15 × 100` replaces
the model's holistic market number in the total (kept as `llm_market`); a department run from
before the rubric keeps the model's number and says so (`market_method`).
