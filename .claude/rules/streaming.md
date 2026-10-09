---
paths:
  - "core/pipeline.py"
  - "api/main.py"
  - "ui/src/api.js"
  - "tests/test_evaluate_stream.py"
---

## A profile appears before its verdict

`pipeline.evaluate` takes `on_partial(section, data)` alongside `on_step`, and
`POST /api/evaluate/stream` delivers those over SSE. The returned result is unchanged and still
complete — `tests/test_evaluate_stream.py` pins that a streamed run is byte-identical to a plain
one, which is the property that matters. Two code paths producing two answers for one company
would be far worse than a slow page.

The five concurrent branches are collected with `as_completed`, not in a written order: they differ
by tens of seconds, and a summary that finished in three seconds used to sit unread until the
slowest branch returned.

**The header profile is emitted three times, and that is the whole feature.** Measured on a real
run, the deep-profile branch returned at 112s of 118s — a page waiting for it waits for the entire
evaluation. So `_header_profile(row, {}, source)` goes out the moment the company is resolved, the
researched version once the main extraction is grounded (`research_profile`'s `on_progress`,
before the recall nets, which take as long again), and the finished one when the branch returns.
This is only safe because `backfill_profile` fills BLANK fields and never overwrites, and the
recall nets only fill what extraction left blank: each version adds values, never changes one on
screen. The middle one comes from the profile branch's own thread, so every consumer of partials
must tolerate two threads emitting at once (`api/jobs.py` guards its merge for that reason).

Nothing waits for more than it reads. Summary, fit, profile and trend start when the company is
resolved; enrichment's search wave and site fetch run beside them, and only verification waits for
both. The profile branch takes them as futures and waits for each where it first reads it. Moving
work earlier must not change a prompt — a replay from cache stays byte-identical — so the evidence
is assembled in the same order as before (`enrich.combine`).

Anything a streaming run has not produced yet must say so rather than render its empty state. A
pillar pill with no pillar reads as a verdict of nothing; a Fit Score of 0 reads as a bad company.
Both are the `employees_history_status` mistake one level up.

`EventSource` is not usable here — it is GET-only and this must be a POST carrying the session
cookie and CSRF header — so the client is `fetch` plus a stream reader, falling back to
`api.evaluate` on any stream failure.

## Where a run's time goes, and what may start early

`scripts/waterfall.py <run_id>` (or `--json result.json`) draws a stored run from
`token_usage.timeline` (branch sections, search waves with their queue wait and timeouts) and the
call log's `start_ms`. Read it before moving work: on three cold runs the chain was resolve
(15–24s for a web-sourced company) → **trend** (last research branch to land, 6–23s after
profile) → a 16–21s pillars tail led by Empower's tool checks. Profile was not last.

Two edges were moved on that evidence, both without changing a prompt:
- Team & Ecosystem, the pillar concepts, Collaborate and Empower start before the trend lands
  when `pillar_match.evidence_settled_without(research, "trend")` — the 14k evidence budget runs
  out before `evidence_for` reaches the trend, so its records are never read (71 of 80 stored
  runs). Market, Connect (market records) and the model score still wait for it. Empower starts
  the moment fit lands. `tests/test_all_departments.py` pins that the result is identical.
- The profile branch starts its headcount and Siemens waves beside the base wave, and the
  headcount extraction beside the main extraction; they read nothing extraction produces.

The three long thinking calls (fit match, verification, trend momentum) are hedged
(`LLMClient.complete(hedge_after=…)`): a duplicate races a request still running at ~1.5× its
usual time. Same request, temperature 0 — the same contract as a retry, without discarding the
wait. Lowering their reasoning effort was measured to change scores; do not.

Two calls on that chain only copy or rewrite text and run without thinking: the web lookup's
extraction (`data.web_profile_row`, 7.3s → ~2s) and trend's query writer (5.9s → ~1.2s). Cold
runs went from ~69s to 47–56s. What is left on the chain: fit's match call (~29s of thinking)
then Empower, and trend's momentum call (20–28s); both are scoring calls.
