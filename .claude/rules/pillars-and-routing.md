---
paths:
  - "core/programs.py"
  - "core/route.py"
  - "core/score.py"
  - "core/empower.py"
  - "core/departments.py"
  - "core/fit.py"
  - "core/trend.py"
  - "core/judgment.py"
  - "scripts/dimension_variance.py"
  - "ui/src/scoring/**"
---

## Two gates decide a pillar, and they answer different questions

`core/score.py` produces the six dimensions and three route scorecards; those say how **strong** a
startup is. `core/programs.py` holds Siemens' **published** Connect / Collaborate / Empower
criteria and says whether the programme would actually take it. A pillar has to clear both, and
the criteria layer can only ever remove a route the scorecards admitted — which is what keeps the
browser what-if (`ui/src/scoring/routing.js`) a sound answer to the question it asks. **Do not
mirror the criteria client-side**: they read evidence, not weights, so no reviewer weighting can
move them.

Routing also reports `portfolio_stance` — complementary / integrates / adjacent / **competes** —
as an outcome in its own right. A startup that substitutes a Siemens product used to be visible
only as a 0.55 multiplier on `siemens_fit`, which usually pushed it under the alignment gate, so a
scout read "Pass" and never learned the reason was a product Siemens already sells. That is the
most strategically interesting thing an evaluation can find and it was being expressed as a
slightly lower number.

`assess_pillar` returns three states, and collapsing them to two throws away the useful half:

- `blocked` — wrong programme, and no further evidence changes that. A product classified a
  `substitute` of a Siemens tool cannot be listed beside it as a Marketplace partner offering.
- `unproven` — nothing disqualifies it, but a requirement is unevidenced. `next_steps` names each.
- `eligible` — every required criterion met. Outranks `unproven` in the primary-pillar sort
  regardless of scorecard, so the headline never prefers a higher-scoring guess.

Empower's unconditional append in `route.py` is still there and still the reason a scorecard-only
run can never be `Pass`; the criteria gate is what makes `Pass` reachable again.

Two things hang off the pillars beyond the criteria, and both are derived rather than researched —
no extra search, no extra completion, so they cost nothing per run:

- `core/empower.py` turns evidence the run already holds into what a scout can act on today: which
  Xcelerator bundle to offer, the investment signals (stage, investors, headcount growth, top-tier
  programme, corroborated customers, market momentum) each carrying the source URL of the fact
  behind it, and templated approach angles. The angles are **templated, not generated** — a model
  asked for outreach ideas writes fluent suggestions naming a product the company does not make.
- `core/departments.py` ships an **empty** `DEPARTMENTS` registry. Collaborate is a venture-client
  programme, so the real question is which department would buy, and only Siemens can supply that.
  `assess_departments` reports `configured: False` and the UI says "not yet configured" — never an
  empty list of departments, which would read as every department having declined. Same rule as
  `employees_history_status` and the SFS `unassessed` state. Filling it in is data, not code.

**SFS is a lender, not a grant.** `assess_sfs` requires evidenced financeability — an asset the
startup sells or needs, contracted/recurring revenue, a project with an offtake, or Series A+
backing — and then names the product line (vendor finance / equipment & technology finance /
project finance / corporate lending). The rule that matters: **a startup's customers being
capital-intensive is not a reason to recommend SFS.** The old one-line judgement inside the
profile extraction prompt confused the two and returned true for all 18 stored runs, including two
pure software companies. Its fourth state, `unassessed`, is load-bearing — a run whose commercial
posture was never extracted knows nothing either way, and reporting that as "not relevant" is the
same mistake `employees_history_status` exists to prevent.

Everything in `core/profile.py` **transcribes** evidence; nothing there judges. Judgements live in
`core/programs.py`, over the `commercial` sub-profile (deployment, APIs, certifications, pricing,
hardware, revenue shape, funding stage, investors) that the profile stage evidences.

## Calibrate anything a model scores, and watch for dimensions going flat

Three scored quantities had independently collapsed to constants, and a green test suite could not
see any of them, because each individual score was arithmetically correct — only the distribution
across runs shows it. `scripts/dimension_variance.py` (advisory in `scripts/gates.sh`) is the
check; read its output rather than skipping past it.

Asked for a 0–100 confidence with no anchors, a model uses the top of the scale and nothing else:
the fit prompt returned 85–100 for all 54 matches it ever made, and trend momentum returned 90–92
for every niche. Both prompts now tie each band to an observable consequence and require the
answer to cite what it counted. **A model-produced number without a rubric is not a measurement.**
