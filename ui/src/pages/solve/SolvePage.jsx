import React, { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { IxButton, IxMessageBar } from "@siemens/ix-react";
import { useResearch } from "../../research.jsx";
import ErrorBox from "../../components/ErrorBox.jsx";
import { Loading } from "../../components/widgets.jsx";
import { useSolveSession } from "./useSolveSession.js";
import { useShortlist } from "./useShortlist.js";
import ProblemInput from "./ProblemInput.jsx";
import RecentSearches from "./RecentSearches.jsx";
import Interpretation from "./Interpretation.jsx";
import ResultFilter, { NO_FILTER, applyFilter } from "./ResultFilter.jsx";
import CandidateList from "./CandidateList.jsx";
import CompareTable from "./CompareTable.jsx";
import ShortlistAsk from "./ShortlistAsk.jsx";
import ShortlistBar, { MAX_COMPARE, MAX_EVALUATE } from "./ShortlistBar.jsx";
import { PROVIDERS } from "./labels.js";
import "./solve.css";

const STATUS = { used: "used", empty: "no matches", unavailable: "unavailable, fell back", empty_or_unavailable: "no matches",
  not_connected: "not connected", skipped: "not needed", not_configured: "not configured", disabled: "off" };
// Said only when true: without the embedding index the search falls back to shared words, and a
// reviewer should know a near-synonym may have been missed.
const RETRIEVAL = { semantic: "Matched by meaning against every application.",
  words: "Matched on shared words only — similar wording may be missed." };
const FILTERED = "None of these match the filter above.";

/* Solve a Problem, top to bottom: the problem and how it was read, the startups that applied to
   Siemens (within seconds), the market (when its search finishes), then whatever the reviewer
   does with a shortlist — compare it, ask about it, evaluate it in depth, or take it with them.
   A finished search is kept with its shortlist, so it can be reopened from "Your recent problems".

   One column on purpose. Each step follows from the one above it, and a reviewer who only wants
   the first answer can stop reading after the first list. */
export default function SolvePage() {
  const s = useSolveSession();
  const { view, job } = s;
  const shortlist = useShortlist(s.id, s.kept);
  const research = useResearch();
  const nav = useNavigate();
  const [comparing, setComparing] = useState(false);
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState(NO_FILTER);
  // Reset for a different search during render, not in an effect: an effect runs after the screen
  // has updated, so a band picked in that gap (the moment a search finishes) was silently undone.
  const [shownId, setShownId] = useState(s.id);
  if (shownId !== s.id) {
    setShownId(s.id); setComparing(false); setNotice(""); setFilter(NO_FILTER);
  }

  const all = useMemo(() => [...(view.applicants || []), ...(view.market || [])], [view.applicants, view.market]);
  // A shortlisted startup the current choices hide is still on the shortlist, so every candidate
  // this search has shown is remembered (and what it originally returned, for a reopened search).
  // Idempotent, so writing the ref during render is safe.
  const known = useRef({ id: null, byId: new Map() });
  if (known.current.id !== s.id) known.current = { id: s.id, byId: new Map() };
  for (const c of [...(view.unrefined?.applicants || []), ...(view.unrefined?.market || []), ...all]) {
    known.current.byId.set(c.id, c);
  }
  const picked = shortlist.ids.map((id) => known.current.byId.get(id)).filter(Boolean);
  const hidden = picked.filter((c) => !all.some((x) => x.id === c.id)).length;
  const applicants = applyFilter(view.applicants, filter, shortlist.ids);
  const market = applyFilter(view.marketDone ? view.market : null, filter, shortlist.ids);
  const clear = () => { shortlist.clear(); setComparing(false); };

  const evaluate = async () => {
    const names = picked.slice(0, MAX_EVALUATE).map((c) => c.name);
    try {
      const rows = await research.start({ names });
      if (rows.length === 1) nav(`/startup/new?name=${encodeURIComponent(rows[0].query)}&job=${rows[0].id}`);
      else setNotice(`Started ${rows.length} evaluations. They run in the background — follow them under Explore a startup.`);
    } catch (e) { setNotice(e.message); }
  };

  // Persistent, so it cannot be dismissed and forgotten: the page is live in production while it
  // is still being built, and a reviewer should weigh what it finds accordingly.
  const head = (<>
    <div className="page-head">
      <h1 className="page-title">Solve a Problem</h1>
      {(job || s.opening || s.openError) && <IxButton variant="secondary" onClick={s.newProblem}>New problem</IxButton>}
    </div>
    <IxMessageBar type="warning" persistent className="solve-dev-notice">
      <strong>Under development.</strong> This page is still being built: results and features may change, and
      some may not work as expected. For a full assessment, evaluate a startup in depth.
    </IxMessageBar>
  </>);
  if (s.opening || s.openError) {
    return <div className="solve-page">{head}
      {s.opening ? <Loading text="Opening your search…" /> : <ErrorBox message={s.openError} hint="Start it again as a new problem." />}
    </div>;
  }
  if (!job) {
    return <div className="solve-page">{head}
      <ProblemInput problem={s.problem} setProblem={s.setProblem} onSubmit={() => s.start()} starting={s.starting}
        error={s.startError} autoFocus={s.focusInput} />
      <RecentSearches /></div>;
  }

  const failed = job.status === "error";
  const done = job.status === "complete";
  const sources = (s.result?.sources || []).filter((x) => x.provider !== "applications");
  return (
    <div className="solve-page">
      {head}
      <Interpretation interpretation={view.interpretation} problem={job.query} canRefine={done}
        refinement={s.result?.refinement} refining={s.refining} refineError={s.refineError}
        onRefine={s.refine} onSearchAgain={(brief) => s.start(brief)} />
      {failed && <div className="solve-failed">
        <ErrorBox message={job.error || "The search failed."} />
        <IxButton variant="secondary" onClick={() => s.start(job.query)}>Search again</IxButton>
      </div>}
      {!failed && <>
        {done && all.length > 0 && <ResultFilter filter={filter} onChange={setFilter} total={all.length}
          shown={(applicants?.length || 0) + (market?.length || 0)} />}
        <CandidateList id="solve-applicants" title="Startups that applied to Siemens" items={applicants}
          total={view.applicants?.length} intro={RETRIEVAL[view.retrieval?.method] || ""}
          pending={!view.applicants} pendingText="Searching the applications…" ranked={view.applicantsRanked}
          emptyText="No application matches this problem yet. The market search below may still find someone."
          filteredText={FILTERED} selected={shortlist.ids} onToggle={shortlist.toggle} />
        <CandidateList id="solve-market" title="From the market" items={market} total={view.marketDone ? view.market?.length : null}
          intro={sources.length ? sources.map((x) => `${PROVIDERS[x.provider] || x.provider}: ${STATUS[x.status] || x.status}`).join(" · ") : ""}
          pending={!view.marketDone} pendingText={`Searching Tracxn, GlassDollar and the web… ${s.elapsed}s`}
          ranked emptyText={s.result?.refinement
            ? "Nothing the market search found fits these choices. Loosen one, or search the market again with them."
            : "Nothing in the market search fits closely. Try sharpening the problem above."}
          filteredText={FILTERED} selected={shortlist.ids} onToggle={shortlist.toggle} />
      </>}
      {comparing && picked.length >= 2 && <CompareTable candidates={picked.slice(0, MAX_COMPARE)} onClose={() => setComparing(false)} />}
      {done && all.length > 0 && (
        <ShortlistAsk key={job.id} ask={(q, history) => s.ask(q, history, shortlist.ids)} selectedNames={picked.map((c) => c.name)}
          savedTurns={s.kept?.thread} />
      )}
      {notice && <p className="muted" role="status">{notice}</p>}
      {picked.length > 0 && (
        <ShortlistBar picked={picked} hidden={hidden} done={done} saved={shortlist.saved} saveError={shortlist.saveError}
          problem={job.query} refinement={s.result?.refinement} facets={view.interpretation?.facets || []}
          onCompare={() => setComparing(true)} onEvaluate={evaluate} onClear={clear} onNotice={setNotice} />
      )}
    </div>
  );
}
