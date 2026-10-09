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

export default function SiemensRelationship({ res, pillar }) {
  const all = res?.deep_profile?.siemens_engagements || [];
  if (!all.length) return null;
  const bonus = Boolean(pillar?.relationship_bonus);
  return (
    <section className="opp-other" aria-labelledby="siemens-relationship">
      <h4 id="siemens-relationship">Existing Siemens relationship</h4>
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
