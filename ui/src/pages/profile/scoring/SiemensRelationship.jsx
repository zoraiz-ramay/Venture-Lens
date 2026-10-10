import React from "react";
import { ExtLink } from "../../../components/widgets.jsx";

/* An existing relationship with Siemens, as the evidence states it (core/profile._siemens_relationship).
   Shown on Collaborate because it is where it counts: one linked engagement adds a point to the
   total (core/pillars.with_relationship_bonus), and the reviewer must see which one and open it.
   Every engagement the run found is listed, including those that earned nothing: an application-only
   mention has no link, so it is shown as a claim and says why it did not count. */
const KIND = { investment: "investment", pilot: "pilot", joint_project: "joint project", programme: "programme",
  supplier: "supplier", partner: "partner", customer: "customer", uses_product: "uses" };

export function engagementText(e) {
  const kind = KIND[e.type] || e.type;
  const what = e.type === "uses_product" && e.product ? `${kind} ${e.product}` : kind;
  return `${e.unit || "Siemens"}: ${what}${e.year ? ` (${e.year})` : ""}`;
}

/* The run-level list (the deep profile's), so the header badge and every department read one source. */
export function sourcedEngagements(res) {
  return (res?.deep_profile?.siemens_engagements || []).filter((e) => /^https?:/.test(e?.source_url || ""));
}

// Strongest first: what a reviewer most needs to know about the relationship leads the sentence.
const LEAD = [
  ["customer", (u) => `${u} is a customer`], ["investment", (u) => `${u} has invested`],
  ["pilot", (u) => `Piloted with ${u}`], ["joint_project", (u) => `Joint project with ${u}`],
  ["supplier", (u) => `Supplies ${u}`], ["partner", (u) => `Partners with ${u}`],
  ["programme", (u) => `In a ${u} programme`], ["uses_product", () => "Uses Siemens products"],
];
const ALSO = { customer: "customer", investment: "investment", pilot: "pilot", joint_project: "joint project",
  supplier: "supplier", partner: "partner", programme: "programme", uses_product: "uses Siemens products" };

/** The relationship in one short sentence, built from sourced engagements only — templated, never
 *  generated — with the source of the one it leads with. {text, url}; "None found" without any. */
export function relationSentence(res) {
  const eng = sourcedEngagements(res);
  const lead = LEAD.find(([type]) => eng.some((e) => e.type === type));
  if (!lead) return { text: "None found", url: "" };
  const [type, say] = lead;
  const mine = eng.filter((e) => e.type === type);
  const named = [...new Set(mine.map((e) => e.unit).filter((u) => u && u !== "Siemens"))];
  const year = mine.map((e) => e.year).filter(Boolean).sort().pop();
  const detail = [named.join(", "), year].filter(Boolean).join(", ");
  const others = LEAD.map(([t]) => t).filter((t) => t !== type && eng.some((e) => e.type === t)).map((t) => ALSO[t]);
  const text = `${say("Siemens")}${detail ? ` (${detail})` : ""}${others.length ? `; also ${others.join(", ")}` : ""}.`;
  return { text, url: mine[0].source_url };
}

/* The sentence with its source, as the profile summary and Collaborate both show it. */
export function RelationLine({ res }) {
  const { text, url } = relationSentence(res);
  return <>{text}{url && <> <ExtLink href={url}>Source</ExtLink></>}</>;
}

export default function SiemensRelationship({ res, pillar }) {
  const all = res?.deep_profile?.siemens_engagements || [];
  const bonus = Boolean(pillar?.relationship_bonus);
  if (!all.length) {
    return (
      <section className="opp-other" aria-labelledby="siemens-relationship">
        <h4 id="siemens-relationship">Relation with Siemens</h4>
        <p>None found</p>
      </section>
    );
  }
  return (
    <section className="opp-other" aria-labelledby="siemens-relationship">
      <h4 id="siemens-relationship">Relation with Siemens</h4>
      <p className="relation-line"><RelationLine res={res} /></p>
      <p className="muted">
        {bonus ? `Adds +1 to Collaborate (criteria ${pillar.model_total}/9, total ${pillar.total}/9).`
          : "Listed for the reviewer; none of these has a source link, so none adds to the score."}
      </p>
      <ul className="tool-cards">{all.map((e, i) => (
        <li key={`${e.unit}-${e.type}-${e.product}-${i}`} className="tool-card">
          <span className="tool-card-head"><strong>{engagementText(e)}</strong>
            {e.claimed && <span className="kind-tag">claimed</span>}</span>
          {e.quote && <p>“{e.quote}”</p>}
          <small className="muted">
            {e.source_url ? <ExtLink href={e.source_url}>Source</ExtLink> : "Stated in the application only · no source link, not counted"}
            {e.claimed && e.source_url && " · the company's own statement"}
          </small>
        </li>))}</ul>
    </section>
  );
}
