import React from "react";

/* What this run changed against the company's earlier runs (result.carry_forward,
   core/carry_forward.py), in one line. Every count is of facts on record; nothing here is a score.
   Absent on a first evaluation: with nothing earlier, "everything is new" says nothing. */

const SLOT = {
  founders: "founders", programs: "programmes", investors: "investors", customers: "customers",
  revenue: "revenue", headcount_history: "headcount history", competitors: "competitors",
  market_size: "market size",
};
// Three runs in a row without it is a finding about the web, not one unlucky search.
const SETTLED_EMPTY = 3;

const isListCount = (v) => v && typeof v === "object" && "new" in v && "reconfirmed" in v && "carried" in v;

export default function RunChanges({ carryForward, factStatus, slotStatus }) {
  if (!carryForward?.prior_runs) return null;
  const lists = Object.values(carryForward).filter(isListCount);
  const sum = (k) => lists.reduce((n, v) => n + (Number(v[k]) || 0), 0);
  const stale = (factStatus || []).filter((s) => s.status === "unconfirmed").length;
  const absent = Object.entries(slotStatus || {})
    .filter(([slot, v]) => SLOT[slot] && v?.status === "empty" && v.empty_streak >= SETTLED_EMPTY)
    .map(([slot]) => SLOT[slot]);
  const parts = [`${sum("new")} new`, `${sum("reconfirmed")} found again`, `${sum("carried")} kept from earlier runs`];
  return (
    <p className="run-changes" aria-label="Changes since the last run">
      Compared with {carryForward.prior_runs} earlier run{carryForward.prior_runs === 1 ? "" : "s"}: {parts.join(" · ")}
      {carryForward.market_size_candidate && " · market size kept (a different figure was found once)"}
      {absent.length > 0 && ` · not found in the last ${SETTLED_EMPTY}+ runs: ${absent.join(", ")}`}
      {stale > 0 && ` · ${stale} fact${stale === 1 ? "" : "s"} whose source no longer states ${stale === 1 ? "it" : "them"}`}
    </p>
  );
}
