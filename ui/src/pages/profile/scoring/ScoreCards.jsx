import React from "react";
import { MiniBar, RadarChart, RingGauge } from "../../../components/charts.jsx";
import Section from "../Section.jsx";
import { pillarRows } from "./presentation.js";
import SfsPanel from "./SfsPanel.jsx";
import { pts } from "./TractionDetail.jsx";

/* The compact face of each Scoring & Fit block: the headline number and the chart that explains it.
   The whole box opens the full panel in a popup (ScoreGrid). Nothing here is new information — every
   figure is the one the full panel shows — so a reviewer scanning five boxes sees the same facts as
   one reading five panels, without the panels' detail in between.
   The box's one control is a button stretched over it (styles.css `.score-card-open`): a heading
   cannot sit inside a <button>, and the box keeps its heading for the outline and screen readers. */
function Card({ id, title, sub, onOpen, children }) {
  return (
    <Section id={id} className={`panel score-card${onOpen ? " openable" : ""}`}>
      <div className="score-card-head"><h3>{title}</h3>{sub && <span className="muted">{sub}</span>}</div>
      <div className="score-card-body">{children}</div>
      {onOpen && <button type="button" className="score-card-open" aria-haspopup="dialog"
        aria-label={`Open ${title} details`} onClick={onOpen} />}
    </Section>
  );
}

// A block the run has not produced yet says so, rather than drawing an empty chart.
const notYet = (res, what) => <p className="muted" role="status">{res.streaming ? `${what} is being assessed…` : "Not assessed on this run."}</p>;

export function FitCard({ res, onOpen }) {
  const fit = res.assessment?.siemens_fit;
  const rows = pillarRows(res);
  const legacy = rows.every((r) => r.state === "legacy");
  return (
    <Card id="scoring-routes" title="Siemens Fit" onOpen={onOpen}
      sub={fit?.winner ? `Best route: ${fit.winner}${fit.partial ? " · partial" : ""}` : "Best of three routes, 0–100"}>
      {legacy ? <p className="muted" role="status">Legacy run: routes are assessed for a department.</p> : <div className="score-card-chart">
        <RingGauge value={fit?.score ?? null} size={104} color="var(--chart-fit)"
          sub={fit?.score != null ? "of 100" : res.streaming ? "pending" : "not assessed"}
          label={fit?.score != null ? `Siemens Fit ${fit.score} of 100` : "Siemens Fit not available"} />
        {/* Alternatives, not parts of a whole: one bar per route beside the best one's ring. */}
        <ul className="score-card-bars" aria-label="Routes">{rows.map((r) => (
          <li key={r.name}><span>{r.name}</span>
            <MiniBar value={r.value} color="var(--chart-fit)" label={r.value != null ? `${r.name} ${r.value} of 100` : `${r.name} ${r.role}`} />
            <span className="muted">{r.value != null ? r.value : r.role}</span></li>))}</ul>
      </div>}
    </Card>
  );
}

export function TractionCard({ res, onOpen }) {
  const t = res.traction;
  const readable = t && Array.isArray(t.divisions);
  return (
    <Card id="scoring-traction" title="Traction" onOpen={onOpen}
      sub={readable && t.confidence != null ? `${Math.round(t.confidence * 100)}% of the points had evidence` : "Funding · Customers · Revenue · Employees"}>
      {!readable ? notYet(res, "Traction") : <div className="score-card-chart">
        <RingGauge value={t.score_0_100} size={104} color="var(--chart-traction)" sub="of 100"
          label={t.score_0_100 != null ? `Traction ${Math.round(t.score_0_100)} of 100` : "Traction not scored"} />
        <ul className="score-card-bars" aria-label="Traction divisions">{t.divisions.map((d) => {
          const unknown = d.status === "unknown";
          return (<li key={d.id}><span>{d.label}</span>
            <MiniBar value={unknown ? null : d.points} max={d.max} color="var(--chart-traction)"
              label={unknown ? `${d.label}: no evidence, excluded` : `${d.label}: ${pts(d.points)} of ${d.max}`} />
            <span className="muted">{unknown ? "excluded" : `${pts(d.points)}/${d.max}`}</span></li>);
        })}</ul>
      </div>}
    </Card>
  );
}

export function TeamCard({ res, onOpen }) {
  const t = res.assessment?.team_ecosystem || res.team_ecosystem;
  const scored = t?.status === "assessed";
  return (
    <Card id="scoring-team" title="Team & Ecosystem" onOpen={onOpen}
      sub={scored ? `${t.points}/20 · ${t.band}` : "Four criteria, 0–5 each"}>
      {!t ? notYet(res, "Team & Ecosystem")
        : !scored ? <p role="status"><span className="verdict unassessed">Not assessed</span> <span className="muted">{t.message}</span></p>
          : <div className="score-card-chart center">
            <RadarChart size={190} max={5} axes={t.criteria.map((c) => ({ label: c.label, value: c.score }))}
              label={`Team and ecosystem: ${t.criteria.map((c) => `${c.label} ${c.score} of 5`).join(", ")}`} />
          </div>}
    </Card>
  );
}

export function MarketCard({ res, onOpen }) {
  const m = res.market;
  const scored = m?.status === "assessed";
  return (
    <Card id="scoring-market" title="Market" onOpen={onOpen}
      sub={scored ? `${m.points}/15 · ${m.band}` : "Size, growth and strategic relevance"}>
      {!m ? notYet(res, "Market")
        : !scored ? <p role="status"><span className="verdict unassessed">Not assessed</span> <span className="muted">{m.message}</span></p>
          : <div className="score-card-chart">
            <RingGauge value={m.score_0_100} size={104} color="var(--chart-market)" sub="of 100"
              label={`Market ${Math.round(m.score_0_100)} of 100`} />
            <ul className="score-card-bars" aria-label="Market criteria">{m.criteria.map((c) => (
              <li key={c.id}><span>{c.label}</span>
                <MiniBar value={c.score} max={5} color="var(--chart-market)" label={`${c.label}: ${c.score} of 5`} />
                <span className="muted">{c.score}/5</span></li>))}</ul>
          </div>}
    </Card>
  );
}

/* SFS has no compact face: the verdict, the line and what is still missing are a few lines, and
   "a financing line applies" is a finding a reader should not have to open anything to see. The
   old #scoring-sfs and #scoring-breakdown anchors still land here, so shared links keep working. */
export function SfsCard({ res }) {
  return (
    <Card id="scoring-supplementary" title="SFS financing" sub="Siemens Financial Services · not part of the weighted total">
      <span id="scoring-sfs" className="anchor-alias" aria-hidden="true" />
      <span id="scoring-breakdown" className="anchor-alias" aria-hidden="true" />
      <SfsPanel rt={res.routing || {}} />
    </Card>
  );
}
