---
paths:
  - "ui/**"
---

## Frontend map

Routes (`ui/src/App.jsx`): `/` Home, a bare search landing that evaluates whatever you type ·
`/workspace` **Solve a Problem** (`pages/solve/`, see below) · `/explore` the companies grid, labelled **Databases** (route deliberately
unchanged so saved-view links keep resolving) · `/startup/:id` Profile · `/saved` · `/alerts` ·
`/ask` · `/settings` · `/admin` (rail entry hidden unless `/api/auth/me` reports `is_admin`; the
page itself explains a 403 rather than 404ing, so a shared link is diagnosable). API client:
`ui/src/api.js`. Shared state: `ui/src/state.jsx`.

The Profile is **one scrolling page with a section rail**, not a tab set: Profile → Scoring & Fit →
Market & Risk → Evidence, each anchored and scroll-spied (`SECTIONS` / `useScrollSpy` in
`Profile.jsx`). A rail entry is rendered only when its anchor exists (`present`), so no destination
scrolls nowhere.

Icons are Siemens iX, via `ui/src/components/Icon.jsx`. It inlines the SVG and repaints it in
`currentColor` because the shipped glyphs carry `fill='none'` and expect the host to paint them —
an `<img>` or a CSS mask renders them blank. Do not reintroduce Unicode or emoji glyphs in chrome.

The six weight sliders (`ui/src/components/WeightSliders.jsx`) and the single
`se.whatIfWeights.v1` behind them are shared by the profile what-if and Explore's portfolio
re-weighting on purpose: "my weighting" is something a reviewer has, not a per-screen setting.

`ui/src/tokens.css` is the **single source of truth for design tokens** — chrome (product shell),
canvas (data surface), text, semantic, pillar ramp, type scale. Components reference tokens, never
raw colours. `docs/ui-inventory.json` is generated from source by `scripts/ui_inventory.py`; read
it instead of recalling the component list from memory.

## Design contract: Siemens iX owns styling and information architecture

Siemens iX (`@siemens/ix`) is now authoritative for both styling **and** information architecture.
The Tracxn-modelled shell — icon rail, top command bar, collapsible secondary side-nav, dense data
canvas — is being retired in favour of iX's own shell and layout primitives. Where an old Tracxn
rule had a purpose, here is what replaces it, not just what's deleted:

- **App shell.** The fixed icon rail + top bar + conditional side-nav stack (`App.jsx`) is replaced
  by iX's own shell composition (`IxApplication` as the single root, holding the application header,
  application menu and content components as children rather than hand-rolled fixed-position
  siblings — see `docs/ix/guidance.md`). The exact shell composition is a Phase 3 decision; this
  entry records the authority, not the final component list.
- **Command bar.** The Ctrl/Cmd-K global search-and-slash-command palette has no iX equivalent and
  needs a fresh IA decision in Phase 3 rather than a re-skin.
- **Dense data tables.** iX ships no `IxTable`/`IxBasicTable` (verify any component name against
  `docs/ix/components.md` before it enters a design) — Explore's sticky-column, customisable,
  URL-state-persisted grid needs an explicit Phase 3 decision on what iX primitive(s) it's rebuilt
  from.
- **Pillar colour ramp.** The hand-derived four-way Connect/Collaborate/Empower/Pass ramp has no
  anchor in iX's palette; Phase 3 decides whether it stays a hand-derived app-level overlay or is
  redrawn onto an iX-supported scale.
- **Dark chrome over a light canvas.** iX has no "dark chrome, light workspace" construct. This
  seam either gets an iX-sanctioned equivalent or an explicit, documented exception in Phase 3 —
  it does not survive by default just because it existed before.

Rules that are mechanically enforced by `scripts/ix_lint.mjs`, not left to judgement: no raw
hex/rgb outside `tokens.css`; interactive elements need accessible names; every data view needs
visible loading, empty and error states.

## Frontend source-file rules

- Target <= 300 lines per hand-written source file; 400 is the hard ceiling.
- Split by coherent responsibility, never by line count. Do not manufacture wrapper components to
  get under the limit — a 40-line component that only forwards props is worse than a long file.
- `styles.css` and `tokens.css` are exempt: they are the app's single stylesheet and its token
  source, and slicing them per-component would invent an architecture nothing else follows.
- Prefer existing components, utilities and tokens. Do not add a dependency for something a
  browser API does cleanly (the Overview's scroll-spy is ~140 lines of IntersectionObserver).
- Keep configuration in one place. `ui/src/pages/profile/sections.js` is the only list of the
  Overview's sections; the rail, the scroll-spy and the tests all read it.
- Clean up observers, subscriptions and listeners. Every one of them has a disconnect test.
- Run `bash scripts/gates.sh` before finishing. Do not reformat unrelated code.

## The profile reads top to bottom, and that is load-bearing

Every view under `ui/src/pages/profile/` is a single column of `<section>`s, with the rail beside
it (`ProfileSectionNav` + `useScrollSpy` + `ProfileLayout`), modelled on Tracxn's profile
navigation.

They used to be two-column `grid2`s. **Do not put them back.** A table of contents needs a reading
order, and in two columns "Team & ecosystem" sits beside "Executive summary" at the same scroll
position — there is no single current section to mark and the indicator flickers between the pair.

**One deliberate exception: Scoring & Fit's score blocks** (`scoring/ScoreGrid.jsx`). The product
owner asked for Siemens Fit · Traction, Team & Ecosystem · Market and SFS as equal-height boxes two
to a row; the whole box is the control, and it opens the full panel it replaces in an iX modal
(`IxModal`, rendered inline so the panels keep the router), one at a time, so the grid never moves.
The panel renders bare in the popup (`Section bare`) and the box keeps the id, rail entry and
landmark; a rail click or a shared `#anchor` opens the box's popup. The cost above is accepted
knowingly: the two boxes of a row share a top, so the rail marks the later one of the pair. Do not
spread the pattern to other views. SFS has no popup — its verdict is a finding nobody should have
to open — and the old "Total score" panel is gone: it repeated the summary's total and component
tiles (its formula is in the summary's detailed view; `#scoring-total` still lands there).
iX moves an `aria-label` off the `<ix-modal>` host, so tests find a popup by its header text.

**The rail is the page's only navigation.** A pipeline ribbon (`Input › Enrich › … › Route`) and a
tab bar used to stack above it. The ribbon rendered all seven steps as done on every finished run,
so it reported nothing a reader could act on while costing ~70px of sticky chrome; two navigation
systems for one page was the other half of the problem. The Ask tab went with them — the ✦
Assistant button opens the same conversation in the dock, from any view.

Consequences worth knowing before changing any of it:

- `ui/src/pages/profile/sections.js` is the **only** list of views and sections. A view's `id` is
  also its `?tab=` value, deliberately unchanged from the old tab labels so permalinks a reviewer
  already shared still resolve.
- Exactly one rail group is open, and the open group **is** the rendered view. Rail state and
  `?tab=` are the same fact; do not add a second source of truth.
- A group header is a disclosure (`aria-expanded` + `aria-controls`), not a tab — a tablist's
  children must be tabs, and these own a list. Sections are real anchors.
- The rail must never be `display: none`. It was hidden below 1000px back when the tab bar carried
  navigation there; now that would leave a phone with no way to reach Scoring & Fit at all. Below
  1000px it becomes a full-width block above the content — same markup, same state.
- `Section` takes its accessible name from the registry. Six unnamed `<section>`s in a row are read
  out as "region, region, region".

Three numbers have to stay in step, and there is one source for each:

- `--profile-sticky-h` is *measured at runtime* by `useStickyOffset`, because `.profile-head` is
  itself sticky and its height depends on how long the company's summary is. On a real profile it
  is ~408px, which is why the scroll-spy's activation band is a fraction of the space *below* the
  chrome and not of the viewport: a percentage of the viewport produced a negative root rect and
  the observer silently stopped firing.
- `.profile-section { scroll-margin-top }` puts a clicked section a few pixels below that line;
  `CROSS_TOLERANCE_PX` in `useScrollSpy.js` must cover that gap, or a section scrolled to by a
  click counts as not yet reached and the marker snaps back to its neighbour.
- The active rule is "the last section whose top has crossed the line" — the same in both scroll
  directions. Not "first intersecting entry" (IntersectionObserver's entry order is unspecified)
  and not "most visible" (that tracks panel height, so a two-chip panel can never win).
