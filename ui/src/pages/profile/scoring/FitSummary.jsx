import React, { useState } from "react";
import { IxButton } from "@siemens/ix-react";
import { iconArrowRight } from "@siemens/ix-icons/icons";
import { api } from "../../../api.js";
import { RingGauge } from "../../../components/charts.jsx";
import Icon from "../../../components/Icon.jsx";
import Section from "../Section.jsx";
import EvidenceList from "./EvidenceList.jsx";
import { contributions, pillarRows, readableStatement, splitLead } from "./presentation.js";

const KEY = { traction: "var(--chart-traction)", siemens_fit: "var(--chart-fit)",
  team_ecosystem: "var(--chart-team)", market: "var(--chart-market)" };
const BAND_TEXT = { strong: "Strong match", review: "Worth a review", no_match: "No match" };

/* "7/9 · Strong match" for an assessed pillar; its state for one that is not. */
function strengthOf(pillar, row) {
  return pillar?.status === "assessed" ? `${pillar.total}/9 · ${BAND_TEXT[pillar.band] || pillar.band}` : row?.role || "";
}

/* The four components as tiles, colour-keyed to the blocks below. A component not scored
   says so — "pending" while the run streams, "not scored" after — never 0. */
function KpiStrip({ res }) {
  const c = contributions(res);
  return (
    <ul className="kpi-strip" aria-label="Score components">
      {c.rows.map((r) => (
        <li key={r.key} className="kpi-tile">
          <span className="kpi-label"><span className="key" style={{ background: KEY[r.key] }} />{r.label}</span>
          <strong className="kpi-value">{r.value != null ? Math.round(r.value) : <span className="muted">{res.streaming ? "pending" : "not scored"}</span>}</strong>
          <span className="muted">{r.earned != null ? `${r.earned} of ${r.max} points` : `up to ${r.max} points`}</span>
        </li>
      ))}
    </ul>
  );
}

/* The top of Scoring & Fit: one recommendation, one opportunity and one next step, beside the two
   numbers a reader looks for first. Built from the winning pillar's own statement and next step
   rather than the route's reason strings, which restate every criterion score; those stay in the
   audit disclosure of the detailed view.

   The statement is the model's prose, often several sentences with catalog ids in it. It used to be
   the headline whole — up to eight bold lines, "(need:si-02-3)" included — so the decision itself
   was the smallest thing on screen. Now the decision and its strength lead, the first sentence is
   the heading, the rest reads as body text, and the other routes carry their scores. */
export default function FitSummary({ res, runId, onAssessment, detailed = false }) {
  const rt = res.routing || {};
  const a = res.assessment;
  const fit = a?.siemens_fit;
  const pillarRoute = rt.version === "pillar-route-v1";
  const winner = pillarRoute ? a?.pillars?.[rt.pillar] : null;
  const rows = pillarRows(res);
  const best = rows.find((r) => r.role === "Recommended")?.value;
  const alternatives = rows.filter((r) => r.role === "Alternative");
  const equal = alternatives.length > 0 && alternatives.every((r) => r.value === best);
  const others = rows.filter((r) => r.role !== "Recommended");
  const total = typeof a?.total === "number" ? a.total : null;
  const next = winner?.next_step || rt.next_steps?.[0];
  const [lead, rest] = splitLead(readableStatement(winner?.statement, winner));
  const decided = rt.pillar !== "Pass" && rt.pillar !== "Defer";
  // Collaborate is the one route that belongs to a department; Empower and Connect do not.
  const forDept = rt.pillar === "Collaborate" ? res.department?.label : "";
  return (
    <Section id="scoring-summary">
      <span id="scoring-decision" className="anchor-alias" aria-hidden="true" />
      {/* The Total score panel's old anchor: its total and components are these. */}
      <span id="scoring-total" className="anchor-alias" aria-hidden="true" />
      {pillarRoute ? (
        <div className="fit-hero">
          <div className="fit-hero-text">
            <p className="fit-hero-route">
              <span className={`pill ${rt.pillar === "Defer" ? "pill-warn" : rt.pillar === "Pass" ? "pill-neutral" : "pill-ok"}`}>{decided ? `Recommended · ${rt.pillar}` : rt.pillar}</span>
              {decided && winner && <span className="fit-hero-strength">{strengthOf(winner)}{forDept ? ` · ${forDept}` : ""}</span>}
            </p>
            <h2 className="fit-opportunity">{lead || (rt.pillar === "Pass"
              ? "No pillar matched this department's criteria."
              : rt.pillar === "Defer" ? "Not enough to decide yet." : `${rt.pillar} is the strongest route.`)}</h2>
            {rest && <p className="fit-opportunity-more">{rest}</p>}
            {alternatives.length > 0 && <p className="muted">{alternatives.map((r) => r.name).join(" and ")} {alternatives.length > 1 ? "are" : "is"} {equal ? "an equally strong" : "also a strong"} alternative in this assessment.</p>}
            {next && <div className="next-step-box"><Icon icon={iconArrowRight} size={20} />
              <div><span className="eyebrow">Next step</span>{next}</div></div>}
            {others.length > 0 && (
              <ul className="route-chips" aria-label={decided ? "Other routes" : "Routes"}>
                {others.map((r) => (
                  <li key={r.name} className="route-chip" data-band={r.band || "none"}>
                    <span className="route-chip-dot" aria-hidden="true" />
                    <strong>{r.name}</strong> {strengthOf(a?.pillars?.[r.name], r)}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="fit-hero-rings">
            <figure>
              <RingGauge value={total} sub={total != null ? "of 100" : "pending"}
                center={total != null ? total.toFixed(1) : "—"}
                label={total != null ? `Total score ${total.toFixed(1)} out of 100` : "Total score pending"} />
              <figcaption className="eyebrow">Total score</figcaption>
            </figure>
            <figure>
              <RingGauge value={fit?.score ?? null} color="var(--chart-fit)"
                sub={fit?.winner ? `from ${fit.winner}` : res.streaming ? "pending" : "not assessed"}
                label={fit?.score != null ? `Siemens Fit ${fit.score} out of 100` : "Siemens Fit not available"} />
              <figcaption className="eyebrow">Siemens Fit{fit?.partial ? " · partial" : ""}</figcaption>
            </figure>
          </div>
        </div>
      ) : res.streaming ? (
        <p className="muted" role="status">The route is decided once all three pillars are assessed.</p>
      ) : <LegacyDecision rt={rt} runId={runId} onAssessment={onAssessment} />}
      {(pillarRoute || res.streaming) && <KpiStrip res={res} />}
      {pillarRoute && detailed && (
        <details className="blind"><summary>How the total is calculated</summary><div className="blind-body">
          <p>Total = 0.30 × Traction + 0.35 × Siemens Fit + 0.20 × Team &amp; Ecosystem + 0.15 × Market, each on
            0–100. It is only calculated when all four are scored; until then it is pending, never a partial sum.</p>
        </div></details>
      )}
      {pillarRoute && detailed && (
        <details className="blind"><summary>Route reasoning (audit)</summary>
          <div className="blind-body">
            {(rt.reasons || []).map((r, i) => <p key={i}>{r}</p>)}
            {rt.next_steps?.length > 1 && <ol>{rt.next_steps.map((s, i) => <li key={i}>{s}</li>)}</ol>}
            <EvidenceList items={rt.evidence} />
          </div>
        </details>
      )}
    </Section>
  );
}

function LegacyDecision({ rt, runId, onAssessment }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const decide = async () => {
    setBusy(true); setError("");
    try { const result = await api.decideRun(runId);
      if (result.routing?.status === "assessed") onAssessment?.(result);
      else setError(result.routing?.message || "Recommendation unavailable. Please retry.");
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  if (rt.version === "llm-decision-v2" && rt.status === "assessed") {
    return <div className="fit-headline"><div>
      <span className={`pill ${rt.pillar}`}>{rt.pillar}</span> <span className="muted">Legacy model recommendation</span>
      {(rt.reasons || []).map((r, i) => <p key={i}>{r}</p>)}
      {rt.next_steps?.[0] && <p className="fit-next"><span className="eyebrow">Next step</span>{rt.next_steps[0]}</p>}
    </div></div>;
  }
  return <div>
    <p className="muted">This run predates department assessments. Assess it for a department with the department switch above, or generate a legacy recommendation.</p>
    <IxButton variant="secondary" disabled={busy || !runId} onClick={decide}>{busy ? "Assessing partnership…" : "Generate legacy recommendation"}</IxButton>
    {error && <p role="alert">{error}</p>}
  </div>;
}
