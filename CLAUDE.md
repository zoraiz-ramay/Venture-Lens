# Siemens Startup Evaluation Agent

Evaluates startups for Siemens partnership decisions: enrich from web evidence → verify → score six
weighted dimensions → route to a pillar (Connect / Collaborate / Empower / Pass). A FastAPI backend
wraps a pure-Python engine; a React SPA is the product surface.

## The one rule everything else serves

**Every displayed fact must be traceable to a source, or it must not be displayed.**

This app informs partnership decisions, so an invented number is worse than a blank field. The
codebase enforces this in several places, and changes must not weaken them:

- `core/profile.py` — `_program_grounded` requires a program name and the company name to
  co-occur in a *single* result before a membership counts; `_ground_customers` requires a
  relationship phrase in a short window; `_clean_employee_series` drops any datapoint without an
  `http` source; `_clean_source_url` rejects a "source" that is not a URL.
- `core/data.py` — `web_profile_row` deliberately does **not** fill verifiable fields (funding,
  founded year, employees, HQ, customers) from model memory. It used to, and produced a funding
  round of "SAR 3.75 million" for makkook.ai that exists nowhere on the web.
- There are **two** sanctioned exceptions, both in `profile.py`, both last-resort passes that run
  only after GlassDollar and every web pass have come back empty, both stamping `*_origin="llm"`,
  carrying no source URL, and labelled **unverified** rather than web-sourced by the UI:
  - `_recall_hq_offline` — headquarters, added when Location became a headline tile.
  - `_recall_links_offline` — the LinkedIn and Crunchbase profile URLs, added because a company
    outside GlassDollar routinely showed both rows blank. A URL is riskier than a place name: it
    is a claim that a page *exists*, and a near-namesake's profile sends a reviewer to the wrong
    company. So a recalled URL is not stored as written — it must parse as a real profile path on
    the right host and its slug must pass the same `_identity_forms` gate `_extract_links` applies
    to a searched result, and the canonical URL is rebuilt from that slug.

  Do not widen this to a third field, and note what both have in common: they fill a field that is
  *identifying* (where the company is, where its public profile lives), never one that is
  *evaluative*. Nothing that feeds a score may come from model memory.
- `core/score.py` — a self-asserted program membership scores at a discount to an independently
  corroborated one; a source URL alone is not evidence.
- **Known gap, not a third exception:** `core/data.py::_knowledge_profile_row` — the fallback when
  the web lookup returns zero hits — still asks the model for HQ, founded year, funding, employees
  and customers, and its `"_source": "llm_knowledge"` marker is read by nothing. It predates the
  rule above and is a defect to fix, not a precedent to copy.

If a field cannot be evidenced, leave it empty and let the UI show "—".

## Architecture

| Path | Role |
|---|---|
| `core/` | The engine. Pure Python, no web framework, no `api/` imports. |
| `core/programs.py` | Siemens' published programme criteria + the SFS gate. Data and pure predicates; no I/O. |
| `api/` | FastAPI wrapper + SQLite persistence (`api/store.py`, S3-backed). |
| `ui/` | React 18 + Vite SPA (the product surface). |
| `tests/` | pytest, backend + engine. |
| `data/` | `glassdollar_applications.xlsx` (429 rows), `runs.db`. |

`core/` must never import `api/` — the engine runs standalone from scripts and tests. The web
result cache is injected the other way round: `api/main.py` calls `core.web.install_cache(...)`.

Pipeline: `core/pipeline.py::evaluate` → `enrich` → (verify ‖ summarize ‖ fit ‖ profile ‖ trend,
concurrent) → score (`core/judgment.py`, `core/traction.py`, and per department the pillars, Team &
Ecosystem and Market) → route (`core/assessment.py::route`). Every `core/` module's docstring says
where it sits in that flow; read it before the code.

The older scorecard layer — `core/score.py::score_startup`, `core/route.py::route`, `core/empower.py`,
`core/departments.py`, `core/programs.py::assess_all_pillars` — is **not called by the live
pipeline** (only `route._portfolio_stance` is). It is kept for `benchmarks/replay.py`, stored-run
replays and the UI what-if mirror (`ui/src/scoring/`, pinned by `tests/test_whatif_*_parity.py`).
`core/__init__.py` re-exports only the names actually used as `core.<name>`.

## Read before editing

The design rationale behind each subsystem lives in `.claude/rules/`, one file per area. Each loads
automatically when you open a file it covers; read it yourself before changing that area if it has
not loaded. The grounding gates and fail-closed rules in them are deliberate — do not weaken them.

- `.claude/rules/data-sources.md` — GlassDollar as the first source, and how its values are provenanced and merged.
- `.claude/rules/pillars-and-routing.md` — Scorecards vs. programme criteria, portfolio stance, Empower/departments/SFS, and calibrating model-scored numbers.
- `.claude/rules/traction.md` — The deterministic traction points rubric and the funding/headcount lookups.
- `.claude/rules/siemens-fit.md` — Siemens Fit as three pillar assessments per department: Empower tools, Connect v5, Collaborate needs, total and market.
- `.claude/rules/market-landscape.md` — The trend stage's market landscape: competitors, funded peers, grounded market size.
- `.claude/rules/siemens-contacts.md` — Relevant Siemens Contact via the Directory API, ranked as an inference.
- `.claude/rules/streaming.md` — Streaming partial results over SSE; the header profile emitted three times; nothing waits for more than it reads.
- `.claude/rules/benchmarks.md` — Replaying stored runs to measure a routing change against human labels.
- `.claude/rules/carry-forward.md` — Folding prior runs into a refresh without dropping evidence; evidence age.
- `.claude/rules/assistant.md` — The assistant: run brief first, Tracxn second, model web search third.
- `.claude/rules/storage.md` — What a run stores row by row, enrichments, and per-reviewer workspaces.
- `.claude/rules/telemetry-auth.md` — OpenTelemetry export rules and the Entra ID backend-for-frontend auth, fail-closed.
- `.claude/rules/solve.md` — Solve a Problem: applicants by meaning, bands not numbers, refine and follow-ups.
- `.claude/rules/frontend.md` — Frontend map, the Siemens iX design contract, source-file rules, and the profile's reading order.

## Token hygiene

- Grep first, then Read with `offset`/`limit`. Never read whole: `ui/src/styles.css`,
  `core/profile.py`, `api/store.py`, `docs/ix/*.md`, `docs/ui-inventory.json`.
- Inspect a visual failure through the diff images in `ui/test-results/`, not the baselines.
- Prefer direct tools over subagents for small tasks; every subagent starts cold and pays for its
  own context.

## Running it

```bash
AUTH_MODE=stub SESSION_BACKEND=memory ADMIN_UPNS=e2e.reviewer@siemens.com \
  py -3 -m uvicorn api.main:app --port 8000    # backend (single process — see below)
cd ui && npm run dev                           # frontend on :5173
py -3 -m pytest tests/ -q                      # backend tests
bash scripts/gates.sh                          # ALL gates (run before finishing any change)
```

`ADMIN_UPNS` is what makes `/admin` reachable in dev — `e2e.reviewer@siemens.com` is the stub
principal's UPN. Leave it unset to exercise the forbidden path.

The e2e suite needs the backend in stub mode, and in **one** process: `SESSION_BACKEND=memory`
splits across gunicorn workers, so a request landing on the other worker looks signed out.

The backend serves the API only — `/` returns 404 by design. The UI is `:5173` in dev.

`.env` at the repo root holds `GEMINI_API_KEY` and, in the Siemens environment,
`GLASSDOLLAR_API_KEY` (both gitignored). Without either the engine degrades — keyword-only
extraction, xlsx + web instead of the live database — rather than failing.

**One build, two environments; no code changes between them.** Production keys live in AWS
Secrets Manager (`docker-apps/startup-evaluation-agent-hydra`, named in `.gitlab-ci.yml`). The CI
hub maps only a fixed set of them to env vars; the rest arrive inside the `APP_SECRETS` /
`AWS_SECRET_JSON` JSON blob, which `core/config.py` exports into the environment at import without
overriding anything already set. `OPENAI_API_KEY` present → the Siemens gateway (`LLM_MODEL`, default `gpt-6-luna`,
`google/gemini-embedding-2`); absent → `GEMINI_API_KEY` from `.env`. The vector indexes ship one
file per embedding model, so both find theirs. `runs.db` restores from and syncs to S3 when the
`HYDRA_DATA_AWS_*` keys are set; locally it is just a file. `load_secrets.py` is not called by
`start.sh` — the blob export is the path that works.

## Testing

Three layers, and they are not interchangeable:

- `tests/` — pytest for engine and API behaviour.
- `ui/src/**/*.test.jsx` — Vitest + Testing Library. Render the component and assert **behaviour**.
- `ui/e2e/` — Playwright journeys + visual regression at four breakpoints.

**Never assert on source text.** Tests of the form `assert "sticky-header" in open(file).read()`
were generated by a previous agent system; they verify nothing, are satisfied by pasting a string,
and all of them rotted into permanent failures. They have been replaced. Do not reintroduce the
pattern.

Visual baselines in `ui/e2e/__screenshots__/` are the record of "the layout still works". Agents
can update them.

## Conventions

- Comments explain **why**, especially where the code looks odd on purpose (the grounding gates
  above are all non-obvious and all deliberate). Do not add narration of what the next line does.
- Prefer editing existing modules over adding new ones; the engine is deliberately small.
- One concern per commit.

## A cut-off reply is a silent data loss

`core/llm.py::complete` checks `finish_reason`. A reply that stops at its token ceiling is half a
JSON object; `parse_json` rejects it and the caller drops to its offline fallback with no error
recorded, and the profile comes back keyword-only. The floor is `LLM_MIN_BUDGET` (4096, a ceiling
rather than a charge), a truncated reply is re-asked at up to `LLM_MAX_BUDGET`, and a truncated
reply is never cached. Do not lower these to "size" a call.
