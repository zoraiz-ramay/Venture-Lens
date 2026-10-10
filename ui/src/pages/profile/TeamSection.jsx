import React, { useEffect, useState } from "react";
import ConfirmedTag from "../../components/ConfirmedTag.jsx";
import ErrorBox from "../../components/ErrorBox.jsx";
import { Spec, ExtLink } from "../../components/widgets.jsx";
import Section from "./Section.jsx";
import { dedupePeople, withoutListed } from "./people.js";
import { useLookup } from "./scoring/TractionLookup.jsx";

/* Team & ecosystem: the founders and the external validation behind them — programmes and
   investors. They are searched once: a full refresh of the evaluation reuses what any earlier run
   found (core/profile.research_profile(known_team)), and this section's own Refresh is where they
   are searched again (api lookup/team). What it finds is stored and merged in after the run's own
   entries, so the evaluation's citations keep pointing at the same people, and it is carried into
   the next full evaluation. Until the stored record arrives the run's own lists are shown. */
const NewTag = ({ item }) => (item?.new ? <span className="sig-new">New</span> : null);

export default function TeamSection({ res, researching }) {
  const runId = res.streaming ? null : res.run_id ?? null;
  const [state, load] = useLookup(runId, "team");
  const [shown, setShown] = useState(null);           // kept on screen while a Refresh searches
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => { if (state.data) { setShown(state.data); setRefreshing(false); } }, [state.data]);
  useEffect(() => { if (state.error) setRefreshing(false); }, [state.error]);

  const dp = res.deep_profile || {};
  const lists = shown || { founders: dp.founders, advisors: dp.advisors, programs: dp.programs,
    investors: dp.commercial?.investors };
  const founders = dedupePeople((lists.founders || []).filter((f) => f?.name));
  const advisors = withoutListed(dedupePeople((lists.advisors || []).filter((a) => a?.name)), founders);
  const programs = (lists.programs || []).filter((x) => x?.name);
  const investors = (lists.investors || []).filter((x) => x?.name);
  const refresh = () => { setRefreshing(true); load(true); };

  return (
    <Section id="profile-team-ecosystem">
      <div className="team-head">
        <h3>Team &amp; ecosystem</h3>
        {runId != null && (
          <span className="team-head-meta">
            {shown?.retrieved_at && <span className="fd-stored" title={shown.retrieved_at}>Searched {String(shown.retrieved_at).slice(0, 10)}</span>}
            <button type="button" className="link-btn" onClick={refresh} disabled={refreshing}>
              {refreshing ? "Searching…" : "Refresh"}</button>
          </span>
        )}
      </div>
      {refreshing && <p className="muted" role="status">Searching again for founders, programmes and investors…</p>}
      {state.error && <ErrorBox message={state.error} />}
      {founders.length === 0 && advisors.length === 0 && programs.length === 0 && investors.length === 0 && (
        <p className="muted" style={{ margin: 0 }}>
          {researching ? "Still researching founders, advisors and programmes…" : "No researched team data."}
        </p>
      )}
      {founders.map((f, i) => (
        <Spec key={`f${i}`} k="Founder">
          {f.name} — {f.role || "founder"}
          {f.background && <span className="muted"> · {f.background}</span>}{" "}
          {f.linkedin && <ExtLink href={f.linkedin}>LinkedIn</ExtLink>} <ConfirmedTag item={f} /><NewTag item={f} />
        </Spec>
      ))}
      {advisors.map((a, i) => (
        <Spec key={`a${i}`} k="Advisor">{a.name} — {a.role || "advisor"}{a.affiliation ? `, ${a.affiliation}` : ""} <ConfirmedTag item={a} /><NewTag item={a} /></Spec>
      ))}
      {programs.length > 0 && (
        <Spec k="Programmes">
          {programs.map((x, i) => {
            // A membership found only on the company's own site is a claim, not a verified fact —
            // several such programmes publish no searchable member directory — so it is marked.
            const claimed = String(x.confidence || "").toLowerCase() === "self_asserted";
            return (
              <span key={i} className="chip"
                    title={claimed ? `${x.type} — company-claimed, not independently corroborated` : `${x.type} — independently corroborated`}>
                {x.name}{claimed && <span className="muted"> · claimed</span>} <ConfirmedTag item={x} /><NewTag item={x} />
              </span>
            );
          })}
        </Spec>
      )}
      {investors.length > 0 && (
        <Spec k="Investors">
          {investors.map((x, i) => (
            <span key={i} className="chip">
              {x.source_url ? <ExtLink href={x.source_url}>{x.name}</ExtLink> : x.name} <ConfirmedTag item={x} /><NewTag item={x} />
            </span>
          ))}
        </Spec>
      )}
    </Section>
  );
}
