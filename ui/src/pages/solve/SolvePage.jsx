import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { IxButton, IxMessageBar } from "@siemens/ix-react";
import { useResearch } from "../../research.jsx";
import ErrorBox from "../../components/ErrorBox.jsx";
import { useSolveSession } from "./useSolveSession.js";
import ProblemInput from "./ProblemInput.jsx";
import Interpretation from "./Interpretation.jsx";
import CandidateList from "./CandidateList.jsx";
import CompareTable from "./CompareTable.jsx";
import ShortlistAsk from "./ShortlistAsk.jsx";
import { PROVIDERS } from "./labels.js";

const STATUS = { used: "used", empty: "no matches", unavailable: "unavailable, fell back", empty_or_unavailable: "no matches",
  not_connected: "not connected", skipped: "not needed", not_configured: "not configured", disabled: "off" };
// Said only when true: without the embedding index the search falls back to shared words, and a
// reviewer should know a near-synonym may have been missed.
const RETRIEVAL = { semantic: "Matched by meaning against every application.",
  words: "Matched on shared words only — similar wording may be missed." };
const MAX_COMPARE = 4;
const MAX_EVALUATE = 10;

/* Solve a Problem, top to bottom: the problem and how it was read, the startups that applied to
   Siemens (within seconds), the market (when its search finishes), then whatever the reviewer
   does with a shortlist — compare it, ask about it, or evaluate it in depth.

   One column on purpose. Each step follows from the one above it, and a reviewer who only wants
   the first answer can stop reading after the first list. */
export default function SolvePage() {
  const s = useSolveSession();
  const { view, job } = s;
  const research = useResearch();
  const nav = useNavigate();
  const [selected, setSelected] = useState([]);
  const [comparing, setComparing] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => { setSelected([]); setComparing(false); setNotice(""); }, [job?.id]);

  const all = useMemo(() => [...(view.applicants || []), ...(view.market || [])], [view.applicants, view.market]);
  // A refinement can drop a selected startup; a selection never points at a row that is gone.
  useEffect(() => {
    setSelected((ids) => {
      const kept = ids.filter((id) => all.some((c) => c.id === id));
      return kept.length === ids.length ? ids : kept;
    });
  }, [all]);
  const picked = selected.map((id) => all.find((c) => c.id === id)).filter(Boolean);
  const toggle = (id) => setSelected((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));

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
      {job && <IxButton variant="secondary" onClick={s.newProblem}>New problem</IxButton>}
    </div>
    <IxMessageBar type="warning" persistent className="solve-dev-notice">
      <strong>Under development.</strong> This page is still being built: results and features may change, and
      some may not work as expected. For a full assessment, evaluate a startup in depth.
    </IxMessageBar>
  </>);
  if (!job) {
    return <div className="solve-page">{head}
      <ProblemInput problem={s.problem} setProblem={s.setProblem} onSubmit={() => s.start()} starting={s.starting}
        error={s.startError} autoFocus={s.focusInput} /></div>;
  }

  const failed = job.status === "error";
  const sources = (s.result?.sources || []).filter((x) => x.provider !== "applications");
  return (
    <div className="solve-page">
      {head}
      <Interpretation interpretation={view.interpretation} problem={job.query} canRefine={job.status === "complete"}
        refinement={s.result?.refinement} refining={s.refining} refineError={s.refineError}
        onRefine={s.refine} onSearchAgain={(brief) => s.start(brief)} />
      {failed && <div className="solve-failed">
        <ErrorBox message={job.error || "The search failed."} />
        <IxButton variant="secondary" onClick={() => s.start(job.query)}>Search again</IxButton>
      </div>}
      {!failed && <>
        <CandidateList id="solve-applicants" title="Startups that applied to Siemens" items={view.applicants}
          intro={RETRIEVAL[view.retrieval?.method] || ""}
          pending={!view.applicants} pendingText="Searching the applications…" ranked={view.applicantsRanked}
          emptyText="No application matches this problem yet. The market search below may still find someone."
          selected={selected} onToggle={toggle} />
        <CandidateList id="solve-market" title="From the market" items={view.marketDone ? view.market : null}
          intro={sources.length ? sources.map((x) => `${PROVIDERS[x.provider] || x.provider}: ${STATUS[x.status] || x.status}`).join(" · ") : ""}
          pending={!view.marketDone} pendingText={`Searching Tracxn, GlassDollar and the web… ${s.elapsed}s`}
          ranked emptyText={s.result?.refinement
            ? "Nothing the market search found fits these choices. Loosen one, or search the market again with them."
            : "Nothing in the market search fits closely. Try sharpening the problem above."}
          selected={selected} onToggle={toggle} />
      </>}
      {comparing && picked.length >= 2 && <CompareTable candidates={picked.slice(0, MAX_COMPARE)} onClose={() => setComparing(false)} />}
      {job.status === "complete" && all.length > 0 && (
        <ShortlistAsk ask={(q, history) => s.ask(q, history, selected)} selectedNames={picked.map((c) => c.name)} />
      )}
      {notice && <p className="muted" role="status">{notice}</p>}
      {picked.length > 0 && (
        <div className="solve-selection" role="region" aria-label="Selected startups">
          <span><strong>{picked.length}</strong> selected</span>
          <IxButton disabled={picked.length < 2} onClick={() => setComparing(true)}>
            {picked.length > MAX_COMPARE ? `Compare first ${MAX_COMPARE}` : `Compare (${picked.length})`}</IxButton>
          <IxButton variant="secondary" onClick={evaluate}>
            {picked.length > MAX_EVALUATE ? `Evaluate first ${MAX_EVALUATE}` : `Evaluate (${picked.length})`}</IxButton>
          <IxButton variant="tertiary" onClick={() => { setSelected([]); setComparing(false); }}>Clear</IxButton>
        </div>
      )}
    </div>
  );
}
