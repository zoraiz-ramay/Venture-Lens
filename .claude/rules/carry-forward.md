---
paths:
  - "core/carry_forward.py"
  - "core/web.py"
  - "core/profile.py"
  - "core/pipeline.py"
  - "api/store.py"
  - "ui/src/pages/profile/people.js"
  - "ui/src/pages/profile/ConfirmedTag.jsx"
---

## A refresh adds evidence; it never quietly drops it

Every run is a fresh sample of the web, and the newest used to replace everything shown — Radical
Dot's investors went 8 → 3 → 0 → 2 → 6 across refreshes. `core/carry_forward.py` folds every stored
run of the company (`store.prior_runs_for`, oldest first) into the fresh one as each branch lands,
**before** anything is scored or emitted:

- Lists (people, programmes, investors, competitors, headcount points) are one entry per entity
  across this run and every earlier one. The earliest grounded copy is the base (so a spelling
  does not flip) and every later copy only fills its blanks — a thin fresh founder never replaces a
  richer carried one, and corroborated programme evidence beats a self-asserted claim either way
  round. Order never depends on which refresh found what: entities earlier runs knew first, by
  first-seen date then key; new ones after, by key. An item only earlier runs found keeps its own
  `source_url` and gains `last_confirmed_at`; the UI shows "last confirmed {date}"
  (`ConfirmedTag.jsx`). Carried items must still name an http source — older runs made under
  weaker gates must not resurrect what today's gates would drop.
- Single values: a blank or unsourced fresh value never replaces a sourced prior. When two sourced
  values disagree, the stored one (the most trusted earlier value: database > web > model recall)
  stands unless the fresh one is at least as trusted and is more trusted, names a later funding
  round, or was also found by the newest earlier run. A value seen once is kept in `history` as a
  candidate, so one lucky search no longer flips a headline value; a real change shows a refresh
  later. GlassDollar facts count as sourced (their `source_url` is "GlassDollar", not a URL).
- A run whose model calls all failed (`token_usage` failed_calls == calls) is not used as an
  earlier run: its keyword-fallback profile is no evidence that anything went missing.
- Dates carry_forward writes (`carried_fields`, `history[].replaced_at/found_at`) and
  `deep_profile.history` itself are excluded from scorer evidence (`judgment._NOT_RESEARCH`); in
  the records they gave every refresh a new prompt.
- Values that carry their own source are carried the same way: `customer_segment_grade` (it scores
  traction's Customers division when no customer is named — missing it flipped Radical Dot's
  Customers between evidenced and unknown across refreshes), `commercial.revenue`, certifications.
- Named reference customers have no URL of their own; one is carried only when an earlier run's
  verification claim sourced it and did not contradict it, with that source in
  `deep_profile.customer_evidence` (traction reads it) and its label in `customer_classes`.
- A market figure is current when its `as_of` year is within the last two calendar years and not in
  the future. An undated one counts by when its run found it (18 months). The old 18-month line
  dropped 2024 base figures in October 2026 but kept "2030" ones, because a forecast is always
  dated ahead. A future-dated figure is a forecast, not the size.
- People are one person by `text.same_person`, not by the name alone: equal name keys, names that
  differ only by initials ("KD Kutadgu Gokalp Demirci"), or the same profile link plus a shared
  name token. Names are cleaned first (`text.clean_person_name`: bracketed qualifiers such as
  "(surname not stated)" go). A first name alone joins the ONE full-name person it names — the same
  given name, a prefix of three letters or more, or a listed short form (Andi/Andreas) — and is
  dropped otherwise; Radical Dot's three refreshes had stored "Alexandre", "Alex (surname not
  stated)" and "Andi (surname not stated)" beside its two founders. `founders_first` drops from
  key_team and advisors anyone already listed as a founder. `ui/src/pages/profile/people.js`
  mirrors these rules, so stored runs display correctly unrewritten.
- The Team & Ecosystem panel lists every entity of a cited kind on record, cited ones first and
  marked. Showing only the model's citations made carried investors look lost: it cited a
  different two of fifteen each run.
- Identity first: priors are used only when their own domain matches (a directory page recorded as
  the website does not count as the company's domain), or, without one, the full normalised name.

It transcribes and never judges, like `core/profile.py`. Model outputs are recomputed over the
merged evidence. Stored runs are never rewritten.

## Evidence age is about the evidence, not the run

`Fact.retrieved_at` records when a search actually ran. For a result replayed from `web_cache` that
is the *original* search's timestamp, not this run's — `install_cache`'s optional `entry_getter`
is what carries it through, and `_ddg_many`'s `stats["cached_at"]` is what maps it back per query.
A re-evaluation can be reasoning over week-old results and now says so.

`freshness_days` is a live property and is **not serialised**. It used to be, as a stored constant
zero: every Fact is built during the run that gathers it, so its age is always 0 at write time, and
it then sat frozen in `result_json` while the run aged. Nothing read it, which is the only reason
nobody noticed. Age belongs to whoever is reading, against their own clock — the Evidence tab
computes it from `retrieved_at`.

One more of the same family, and the most expensive: `siemens_fit` blended
`0.3 × challenge_match` whenever the approved-challenge library was non-empty. With one unrelated
approved challenge recorded, that taxed every startup ~30% of its tool fit and accounted for
**all eight** `Pass` verdicts in the corpus. A demand-side match is now a bonus that can only
raise fit. `tests/test_siemens_fit_scoring.py` pins it.
