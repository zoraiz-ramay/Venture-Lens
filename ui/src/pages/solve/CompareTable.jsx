import React, { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { IxButton } from "@siemens/ix-react";
import { BANDS, FACT_ROWS, FACT_SOURCES, PROVIDERS, clip, evaluateHref, fact, quoteOf } from "./labels.js";

/* The selected startups side by side. Every cell is a fact the search already holds, with where it
   came from; nothing here is generated, and a fact nobody stated is "—" rather than a guess. A
   column that is mostly dashes is the cue to evaluate that startup in depth, so its footer says so. */

function Cell({ f }) {
  if (!f) return <td className="solve-cell-empty">—</td>;
  return <td title={f.value.length > 220 ? f.value : undefined}>{clip(f.value, 220)}
    <small className="muted">{FACT_SOURCES[f.source] || f.source}</small></td>;
}

export default function CompareTable({ candidates, onClose }) {
  // It opens below both result lists, where nobody would find it: move to it and put focus on its
  // heading, so a keyboard or screen-reader user lands where the click took them.
  const heading = useRef(null);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    heading.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, []);
  return (
    <section className="solve-compare" aria-labelledby="solve-compare-title">
      <div className="solve-group-head">
        <h2 id="solve-compare-title" ref={heading} tabIndex={-1}>Comparing {candidates.length} startups</h2>
        <IxButton variant="secondary" onClick={onClose}>Close comparison</IxButton>
      </div>
      {/* Wide on purpose: on a phone the table scrolls inside this box, never the page. */}
      <div className="solve-compare-scroll" tabIndex={0} role="region" aria-label="Comparison table">
        <table className="solve-compare-table">
          <thead>
            <tr><th scope="col"><span className="sr-only">Fact</span></th>
              {candidates.map((c) => <th scope="col" key={c.id}>{c.name}
                <small className="muted">{PROVIDERS[c.source] || c.source}</small></th>)}</tr>
          </thead>
          <tbody>
            <tr><th scope="row">How it fits your problem</th>
              {candidates.map((c) => {
                const q = quoteOf(c);
                return <td key={c.id}>
                  {c.band ? <span className={`solve-band ${c.band}`}>{BANDS[c.band]}</span> : "—"}
                  {c.reason && <p>{c.reason}</p>}
                  {q && <p className="solve-quote-inline">“{clip(q.text, 200)}” <small className="muted">{q.from}</small></p>}
                </td>;
              })}</tr>
            <tr><th scope="row">What it does</th>
              {candidates.map((c) => c.description
                ? <td key={c.id}>{clip(c.description, 240)}</td>
                : <td key={c.id} className="solve-cell-empty">—</td>)}</tr>
            {FACT_ROWS.map((row) => (
              <tr key={row[0]}><th scope="row">{row[1]}</th>
                {candidates.map((c) => <Cell key={c.id} f={fact(c, row)} />)}</tr>
            ))}
          </tbody>
          <tfoot>
            <tr><th scope="row"><span className="sr-only">Next step</span></th>
              {candidates.map((c) => <td key={c.id}>
                <Link to={evaluateHref(c)}>{c.run_id ? "Open its evaluation →" : "Evaluate to fill in →"}</Link></td>)}</tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
