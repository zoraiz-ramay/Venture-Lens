---
paths:
  - "core/solve.py"
  - "core/catalogs.py"
  - "api/routes_solve.py"
  - "api/jobs.py"
  - "ui/src/pages/solve/**"
  - "tests/test_solve_flow.py"
---

## Solve a Problem: results first, sharpened after

`core/solve.py` behind `/workspace`. A reviewer states a problem; within seconds they see the
startups that **applied to Siemens**, then the market (Tracxn → GlassDollar → web), and sharpen,
compare or question the shortlist from there. It runs as a job (`api/jobs.py`), and the worker
merges `interpretation` and `applicants` into `partial` as each lands — the page polls the job and
shows them while the market is still being searched.

- **Applicants are searched by meaning.** The applications workbook is embedded into
  `data/applications_index/` (`scripts/build_tool_index.py --catalog applications`, ~10s; rerun
  after any change to the workbook) and fused with word overlap, like tools. Two shared keywords
  used to be the bar, so "predict failures" missed "anomaly detection on machine data". Without
  the index it is words alone, and the page says so (`retrieval.method`).
- **An application's `status`, contact and referral never leave the server.** `core/catalogs.py`
  does not copy them into an entry, so no result built from one can carry them;
  `tests/test_solve_flow.py` pins it. The product owner asked for status not to be shown.
- **A band, not a number.** `rank` judges `strong | good | possible` against anchored definitions
  (what the description *states*), replacing a 0–100 relevance with no rubric. Offline, shared
  words can reach `good`, never `strong`.
- **The reason may be the model's; the evidence may not.** A `quote` the rank stage cites must be
  a verbatim span of the description it was shown, or it is dropped. Before ranking, an applicant
  shows `why_quote` — the pitch sentence sharing most with the problem, copied, not generated.
- **Facets are questions about the reviewer's problem**, never claims about a company, which is
  why a model may write them. `refined_brief` accepts only an offered option of an offered facet,
  in code. `POST /api/solve/{job}/refine` re-ranks what was found and searches **nothing** — the
  market is widened by starting a new search with the refined brief. The first refinement keeps
  `unrefined`, so clearing every choice restores the original result.
- **Follow-ups go through the assistant's path** (`POST /api/solve/{job}/ask` →
  `chat_assistant(run_facts=shortlist_facts(...), facts_subject=...)`): the shortlist's own facts
  first, then Tracxn, then the model's web search. Both follow-up routes read the search from the
  reviewer's own job record, never from the request.
- **Compare is not an endpoint.** Each candidate carries `facts` with their source — application
  answers (self-reported), the profile columns GlassDollar filled into the workbook, or an earlier
  evaluation of the same company (`api/routes_solve.attach_runs`, a read, never a search). A fact
  nobody stated is "—".
- **A search is kept, per reviewer.** `api/jobs.py` stores a finished solve job in `solve_searches`
  (`api/store.py`, newest 50 per reviewer, readable only by its owner); the page keeps it again on
  open. A kept copy is never overwritten by the job's older one, so a refinement or a shortlist
  survives, and refine/ask fall back to it once the job record has expired. The shortlist is saved
  as the reviewer picks; follow-ups are kept in the assistant's thread table under `solve:<id>`.
- **The market runs beside the applicants, and no source can fail the search.** Tracxn starts with
  the search; the web runs alongside the market only when fewer than 5 candidates make it certain to
  be needed. A source that errors or times out reports `unavailable` with a `reason`, never "no
  matches". Each ranked candidate says `judged_by` (`model | words | meaning`); a company found by
  several sources is listed once (name without legal form, or root homepage host). Prompts are
  unchanged by the concurrency, so a cached replay stays byte-identical (`tests/test_solve_speed.py`).
