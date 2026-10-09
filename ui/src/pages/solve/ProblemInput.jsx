import React, { useEffect, useRef } from "react";
import { IxButton, IxTextarea } from "@siemens/ix-react";
import TracxnConnection from "../../components/TracxnConnection.jsx";
import ErrorBox from "../../components/ErrorBox.jsx";

const EXAMPLES = [
  ["Reduce downtime", "We need to predict failures in legacy PLC-controlled production lines without replacing existing equipment."],
  ["Improve quality", "We need to detect surface defects in electronics on a fast-moving production line using visual inspection."],
  ["Use energy better", "We need to optimise grid-scale battery performance and identify degradation early."],
  ["Protect operations", "We need to detect cyber threats in OT networks without disrupting industrial equipment."],
];

/* The page before anything has been asked: one question, one field, a few starting points. The
   data sources used to be explained here in a four-step sidebar before the reviewer had typed a
   word; that now shows where it means something, beside the results each source produced. */
export default function ProblemInput({ problem, setProblem, onSubmit, starting, error, autoFocus }) {
  const input = useRef(null);
  useEffect(() => { if (autoFocus) input.current?.focusInput?.(); }, [autoFocus]);
  const ready = problem.trim().length >= 3 && !starting;
  return (
    <section className="solve-start" aria-labelledby="solve-start-title">
      <h2 id="solve-start-title">What would you like to solve?</h2>
      <p className="muted">Describe what is getting in the way and the outcome you need. You will see startups that
        applied to Siemens within seconds, then the wider market, and can sharpen the results from there.</p>
      <IxTextarea ref={input} label="Describe your problem" value={problem} maxLength={2000}
        disabled={starting} textareaRows={4} textareaWidth="100%" resizeBehavior="vertical"
        placeholder="We need to reduce unplanned downtime on our production lines. It has to work with legacy PLCs…"
        onValueChange={(e) => setProblem(e.detail ?? "")}
        onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); onSubmit(); } }} />
      <div className="solve-start-submit">
        <span className="muted">Ctrl/⌘ Enter</span>
        <IxButton disabled={!ready} onClick={() => onSubmit()}>{starting ? "Starting…" : "Find startups"}</IxButton>
      </div>
      {error && <ErrorBox message={error} hint="Your problem is kept here. Try again." />}
      <div className="solve-examples" role="group" aria-label="Example problems">
        <span className="muted">Try</span>
        {EXAMPLES.map(([title, text]) => (
          <IxButton key={title} variant="secondary" disabled={starting}
            onClick={() => { setProblem(text); input.current?.focusInput?.(); }}>{title}</IxButton>
        ))}
      </div>
      <TracxnConnection compact returnTo="workspace" />
    </section>
  );
}
