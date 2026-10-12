---
paths:
  - "api/store.py"
  - "api/workspace.py"
  - "api/interests.py"
  - "core/traction_lookup.py"
  - "core/business_flow.py"
---

## What a run produces is stored, and served before it is searched again

`result_json` stays the record of a run; `api/store.py` also writes what it holds row by row, and
what is fetched *after* a run (on request) goes in too — Tracxn or web alike, the product owner
confirmed storing Tracxn data is permitted.

- `save_run` → `_replace_children`: people, programmes, customers, evidence facts, tool matches,
  the evaluation's own investors (`provider='research'`), every scored criterion
  (`assessment_criteria`: pillars, Team & Ecosystem, market — per-run history) and the Empower
  `tool_checks`. `backfill_assessment_records` fills these once for runs saved before the tables
  existed; it skips any run that already has criteria rows, so a restart never doubles history.
- `save_enrichment(company, kind, payload)` → `enrichments` (raw history, every fetch) plus the
  normalised table for its kind: `funding_rounds` + `investors`, `headcounts`, `market_signals`.
  The lookup and business-flow endpoints read `latest_enrichment` first and search only on
  `refresh`; the UI says "Saved {date}" on a stored copy. `business_flow` is per run (it is written
  from that run's research); the rest are per company.
- **A partial result is not stored.** A signals search with a failed domain, or a lookup that fell
  through to the model's memory, is shown but not saved — otherwise the gap would be served as the
  answer until someone thought to refresh.

## Per-reviewer workspaces, one shared evaluation

The split that everything in `api/store.py` below `web_cache` exists for: **an evaluation is
shared, the record of who asked for it is private.**

- `searches` — one row per `/api/evaluate`, keyed on the Entra `oid`, with `served_from`
  (`cache` | `fresh`). This is both the reviewer's list (`GET /api/my/searches`, what Explore,
  Home and Tracking read) and the admin activity log. `duration_ms` is how long the reviewer
  waited, timed in `api/main.py` from the start of the evaluation (queue wait included) to the
  recorded search; the admin page's "Typical evaluation time" is the median of fresh rows only.
  It is NULL on rows from before it was timed, and shown as "—", never estimated from the token
  log, whose call times overlap.
- `company_aliases` — every string a company can be reached by: the typed query, the resolved
  name, the domain. Without it the cache-first lookup in `/api/evaluate` misses its own writes,
  because it searches on what was typed while `save_run` files the run under what the pipeline
  resolved. `save_run(result, aliases=[...])` records them; `latest_run_for_alias` reads them and
  falls back to the exact-name match for pre-alias rows.
- `sessions` — one row per sign-in, written from `create_session`. Redis holds only *live*
  sessions, so it can say who is online but never how many sessions there have been.
- `saved_views` — grid views keyed on the `oid`; they used to be `localStorage` and so belonged
  to a browser rather than a person.
- `chat_threads` — the assistant's conversations, one per reviewer and company (`thread_key` is
  the company name, casefolded; `""` is the dock with no startup open), newest `CHAT_KEEP` kept.
  `/api/ask` appends each answered turn itself, so an answer that lands after the reviewer moved
  on is there when they return; `GET`/`DELETE /api/my/chat` read and clear it. Keyed on the
  company rather than the run, so re-evaluating a startup resumes its thread. What the model reads
  is still only the short `history` the client sends.

Lists are strictly private: no parameter widens `/api/my/searches` to another principal. The
team-wide view is `/api/runs` and `/api/admin/*`, behind `require_admin`, which reads `ADMIN_UPNS`
(comma-separated). **Unset means nobody is an admin, never everybody** — same fail-closed rule as
the rest of `api/auth.py`.
