---
paths:
  - "core/glassdollar_api.py"
  - "core/profile.py"
  - "core/provenance.py"
  - "core/data.py"
  - "core/web.py"
  - "api/main.py"
  - "tests/test_glassdollar_first.py"
---

## Data sources: GlassDollar first, the web for the rest

`core/glassdollar_api.py` is the live REST client (`GLASSDOLLAR_API_KEY`, base
`https://actions-api.glassdollar.com`). It is the **first** source everywhere: `_evaluate`
resolves a name through it, then a domain via `get_company_by_domain`, and only then falls back
to `data.web_profile_row`; `/api/search` lists its hits ahead of the local xlsx.

What it can and cannot answer decides how much scraping is left:

- **It answers** name, website, domain, HQ, founded year, headcount, total funding,
  LinkedIn/Crunchbase URLs, referenced customers, descriptions, tags.
- **It does not answer** business model, development stage (Siemens pitch-form fields it does not
  expose), founders, advisors, programs, parent group, headcount history, trend, verification,
  Siemens-tool fit, scoring or routing. Those still need the web pipeline.

`profile._seed_from_database` takes the three headline fields it does answer *before* the recall
nets run, so `_recover_headline_facts` — a whole search wave plus an extraction call, and the most
fragile part of the chain — skips itself. The database also **wins over** the main wave's
extraction, not merely over a blank. Two rules hold there: a researched `*_source` URL is dropped
when the two disagree (it evidences the value that lost), and the headcount never enters
`employees_over_time`, whose `_clean_employee_series` gate requires an `http` source per datapoint.

A GlassDollar value has no URL, so it is `source_type: private` in `core/provenance.py` — not
`public` (which would be demoted to `inferred` for lacking a URL) and not `self_reported` (the API
corroborates across sources rather than repeating the pitch form). The xlsx keeps `glassdollar_db`
/ `self_reported`, because that one *is* the application form.

The xlsx stays: it carries the pitch-form answers and decks the API does not expose, and it is the
only source when no key is set. **The key only resolves inside the Siemens network**, so nothing
about the live API can be verified from a laptop or CI — `tests/test_glassdollar_first.py` drives
the client contract with a fake and says so at the top.

## The search engine is switchable; the evidence is always a page

`SEARCH_BACKEND=ddg` scrapes DuckDuckGo. `SEARCH_BACKEND=grounded` (or `gemini`) lets the model's own
web search — Gemini's Google Search grounding, or the gateway's `web_search` tool, whose
`web_search_call.action.sources` list every page searched — find pages (`web._grounded_search`).
`auto`, the default, is `grounded` on the Siemens gateway and `ddg` elsewhere: from the deployed
network DuckDuckGo timed out on every request (~21s per search after three attempts, and a third of
the enrichment wave abandoned), and one Wandelbots run went from 109s to 47s with more customers and
Siemens engagements found — but ~834k tokens instead of ~36k, since each gateway search bills its
results as ~8-10k input tokens. Either way the engine **throws the model's answer away**,
resolves Google's redirect link (`_resolve_grounding_redirect` — the only off-site redirect the
engine follows), fetches each page through the SSRF-guarded `_fetch_with_url`, and builds each
hit's `body` from at most two ~200-character passages of the page that share the query's words.
Callers see the same `{title, href, body}` either way.

Why not the answer: every grounding gate asks whether *independent* text supports a claim; with
the model's prose as that text the model would be checking itself. Why snippet-sized passages: a
whole page as one "result" would make "programme and company in ONE result" true of any long page.
A page that cannot be fetched is dropped, never described. Each search is one grounded Gemini call
(billed and rate-limited like any other), so it runs under `SEARCH_CONCURRENCY`, not the
DuckDuckGo slots. LinkedIn and Crunchbase usually refuse the fetch, and their snippets are where
headcount and funding lines come from under DuckDuckGo — compare field coverage before switching.

It is not faster. The grounded call takes ~3s, but opening Google's redirect and downloading each
page takes 4–6s when a ten-query wave fetches ~40 pages at once, so a wave lands at ~12s, the same
as DuckDuckGo. Capping the downloads at 4s dropped every page and left trend scoring on no
evidence (measured October 2026). Using it for trend alone was tried and removed for that reason.

## People and investors meet the same co-occurrence bar

A founder, key-team member or advisor is kept only where ONE search result, or one of the company's
own pages (a `__site__` pseudo-hit), names both the person and the company (`profile._ground_people`,
accents folded). Investors additionally need their own http source. The extraction reads a corpus
of many results, so without this a namesake or a half-remembered name entered the team and
flickered between refreshes. People already on record are carried by `core/carry_forward.py`.
