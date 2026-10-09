---
paths:
  - "core/trend.py"
  - "core/market.py"
  - "core/market_signals.py"
  - "core/web.py"
---

## The market landscape rides in the trend stage's own wave

`core/trend.py` stage 1 has always asked the model for competitor and funding queries, and stage 3
threw everything except the prose away. `_market_landscape` now extracts named competitors, funded
peers, market size and active investors from the same results.

Its five queries go into the **same** `_ddg_many` call as the trend queries, because `_ddg_many`
caps concurrency at 10 — so ten queries are one round trip and the landscape adds no search time.
The extraction runs concurrently with the momentum call, both under `copy_context()`.

Both calls read the **same** evidence, and that is deliberate: they used to read different slices,
which produced a page contradicting itself — "no CAGR figures are cited" as the stated basis for a
momentum score, directly above a cited CAGR of 31.7%.

Grounding is the same bar as everywhere else, and both halves matter: every entry needs a real
`http` source, **and** its name has to appear in the evidence text. A model asked for competitors
in a niche will list the ones it remembers rather than the ones in the results, and a fabricated
competitor beside a link is indistinguishable from a real one. The startup is also filtered out of
its own landscape in code — Celonis came back at the top of its own process-mining competitor list,
because in results about a company's own niche that company genuinely is the most prominent name.

**The market size is grounded too, and read as a report states it.** It used to be the one
landscape field with no check, and was often missing although the web had it. What changed, at no
extra model or search call:
- Size is searched under the **parent market** stage 1 now names ("chemical recycling"), the way
  reports are titled. The niche label returned CAGR definitions and calculators.
- Snippets are read whole (a 200-character cut lost Phena's CAGR). The landscape and trend results
  have separate budgets. The top two size results are fetched as pages and cut to their figure
  sentences (`_read_size_pages`, `web.fetch_url`).
- The model returns the **base-year** `value` / `as_of` and the forecast apart (`forecast_value`,
  `forecast_year`). `market.size_level` reads the first amount, never the largest. The largest was
  the forecast, and put Wandelbots' 2032 figure in the size band.
- Every number in `value` and `cagr` must appear in the results, and `source_url` must be a URL the
  search returned. A "None" from the model is stored empty, never as the text "None". The name
  check reads the same rows the prompt did.

`landscape` absent and `landscape` present-but-empty are different statements, and the UI renders
them differently: "not researched on this run" versus "the search named no competitor".
