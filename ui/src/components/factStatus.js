import { createContext } from "react";

/* The source re-check's verdicts for this run's carried facts (result.fact_status, core/recheck.py).
   Provided once by the profile page, so every ConfirmedTag can say when a carried fact's own
   source no longer states it without each list threading the run through its props. */
export const FactStatusContext = createContext([]);

const norm = (s) => String(s || "").trim().toLowerCase();

export function statusFor(statuses, item) {
  if (!item || !Array.isArray(statuses)) return null;
  return statuses.find((s) => norm(s.name) === norm(item.name)
    && (!item.source_url || s.source_url === item.source_url)) || null;
}
