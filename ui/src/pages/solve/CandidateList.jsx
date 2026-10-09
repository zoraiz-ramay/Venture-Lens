import React from "react";
import { Link } from "react-router-dom";
import { ExtLink, Loading } from "../../components/widgets.jsx";
import { BANDS, FACT_ROWS, FACT_SOURCES, PROVIDERS, evaluateHref, fact, quoteOf } from "./labels.js";

/* One group of results — the startups that applied to Siemens, or the market. A row reads as
   "name, how well it fits, the words that say so"; everything else waits behind Details, so a
   reviewer can scan eight rows without reading eight pitches. */

function Band({ band, ranked }) {
  if (!ranked) return <span className="solve-band pending">Checking fit…</span>;
  return band ? <span className={`solve-band ${band}`}>{BANDS[band]}</span> : null;
}

function Details({ c }) {
  const source = c.field_sources?.description || {};
  const facts = FACT_ROWS.map((row) => [row[1], fact(c, row)]).filter(([, f]) => f);
  return (
    <details className="solve-details">
      <summary>Details</summary>
      {c.description && <p>{c.description}</p>}
      <p className="muted solve-source">
        Description from {c.source === "applications" ? "its Siemens application"
          : PROVIDERS[source.provider] || PROVIDERS[c.source] || c.source}
        {source.url && <> · <ExtLink href={source.url}>source</ExtLink></>}
      </p>
      {facts.length > 0 && (
        <dl className="solve-facts">
          {facts.map(([label, f]) => (
            <div key={label}><dt>{label}</dt><dd>{f.value} <small className="muted">· {FACT_SOURCES[f.source] || f.source}</small></dd></div>
          ))}
        </dl>
      )}
      <div className="solve-row-actions">
        <Link to={evaluateHref(c)}>{c.run_id ? "Open its evaluation →" : "Evaluate in depth →"}</Link>
        {c.website && <ExtLink href={c.website} />}
      </div>
    </details>
  );
}

function Row({ c, ranked, checked, onToggle }) {
  const quote = quoteOf(c);
  const box = `pick-${c.id}`;
  return (
    <li className="solve-row">
      <input id={box} type="checkbox" className="solve-pick" checked={checked} onChange={() => onToggle(c.id)}
        aria-label={`Select ${c.name}`} />
      <div className="solve-row-main">
        <div className="solve-row-head">
          <label htmlFor={box} className="solve-name">{c.name}</label>
          <Band band={c.band} ranked={ranked} />
          {c.source !== "applications" && <span className="badge">{PROVIDERS[c.source] || c.source}</span>}
          {c.run_id && <span className="badge">Evaluated</span>}
        </div>
        {c.reason && <p className="solve-reason">{c.reason}</p>}
        {quote && <blockquote className="solve-quote"><p>“{quote.text}”</p><footer className="muted">{quote.from}</footer></blockquote>}
        <Details c={c} />
      </div>
    </li>
  );
}

export default function CandidateList({ id, title, intro, items, pending, pendingText, ranked, emptyText, selected, onToggle, footer }) {
  return (
    <section className="solve-group" aria-labelledby={id}>
      <div className="solve-group-head">
        <h2 id={id}>{title}{items ? ` (${items.length})` : ""}</h2>
        {intro && <span className="muted">{intro}</span>}
      </div>
      {pending && <div role="status"><Loading text={pendingText} /></div>}
      {!pending && items && items.length === 0 && <p className="muted solve-empty">{emptyText}</p>}
      {items && items.length > 0 && (
        <ul className="solve-list">
          {items.map((c) => <Row key={c.id} c={c} ranked={ranked} checked={selected.includes(c.id)} onToggle={onToggle} />)}
        </ul>
      )}
      {footer}
    </section>
  );
}
