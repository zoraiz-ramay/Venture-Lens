import React, { useEffect, useState } from "react";
import { IxButton, IxToggleButton } from "@siemens/ix-react";
import { iconAi } from "@siemens/ix-icons/icons";
import Icon from "../../components/Icon.jsx";
import { Loading } from "../../components/widgets.jsx";
import ErrorBox from "../../components/ErrorBox.jsx";

/* "How I read it": the problem restated, and the few questions whose answers would most change
   which startups fit. Each choice re-ranks what was found straight away (POST /refine); none of
   them searches again, so "search the market again" is offered separately, once the brief has
   actually changed.

   The facets are questions about the reviewer's problem, never claims about a company — which is
   why a model may write them. */
export default function Interpretation({ interpretation, problem, canRefine, refinement, refining, refineError,
  onRefine, onSearchAgain }) {
  const [answers, setAnswers] = useState(refinement?.answers || {});
  useEffect(() => { setAnswers(refinement?.answers || {}); }, [refinement]);
  const facets = interpretation?.facets || [];
  const choose = (facet, option) => {
    const next = { ...answers };
    if (next[facet] === option) delete next[facet]; else next[facet] = option;
    setAnswers(next);
    onRefine(next);
  };
  const chosen = facets.filter((f) => refinement?.answers?.[f.id]).map((f) => `${f.label}: ${refinement.answers[f.id]}`);
  return (
    <section className="solve-read" aria-labelledby="solve-read-title">
      <div className="solve-read-head">
        <span className="solve-ai" aria-hidden="true"><Icon icon={iconAi} size={16} /></span>
        <h2 id="solve-read-title">How I read your problem</h2>
      </div>
      <p className="solve-problem">{problem}</p>
      {!interpretation && <Loading text="Reading your problem…" />}
      {interpretation?.restatement && <p className="solve-restatement">{interpretation.restatement}</p>}
      {interpretation && !interpretation.restatement && (
        <p className="muted">No model is available to restate it, so results are matched on your own words.</p>
      )}
      {facets.length > 0 && (
        <div className="solve-facets">
          <p className="muted">
            {canRefine ? "Sharpen it — each choice re-ranks the results." : "You can sharpen the results once the search has finished."}
          </p>
          {facets.map((f) => (
            <div key={f.id} className="solve-facet" role="group" aria-label={f.label}>
              <span className="solve-facet-label">{f.label}</span>
              {f.options.map((o) => (
                // aria-label is required, not decoration: without an icon, ix-toggle-button names
                // its inner button from the icon name and a screen reader hears "Unknown".
                <IxToggleButton key={o} variant="subtle-primary" pressed={answers[f.id] === o} aria-label={o}
                  disabled={!canRefine || refining} onClick={() => choose(f.id, o)}>{o}</IxToggleButton>
              ))}
            </div>
          ))}
        </div>
      )}
      {refining && <p className="muted" role="status">Re-ranking with your choices…</p>}
      {refineError && <ErrorBox message={refineError} hint="The results below are from before this choice." />}
      {chosen.length > 0 && !refining && (
        <div className="solve-refined">
          <span className="muted">Ranked for: {chosen.join(" · ")}</span>
          <IxButton variant="secondary" onClick={() => onSearchAgain(refinement.brief)}>Search the market again with this</IxButton>
        </div>
      )}
    </section>
  );
}
