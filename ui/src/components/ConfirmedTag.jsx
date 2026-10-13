import React, { useContext } from "react";
import { FactStatusContext, statusFor } from "./factStatus.js";

/* Marks evidence an earlier evaluation found that this refresh did not find again.

   A refresh no longer drops sourced evidence (core/carry_forward.py): an investor, founder or
   competitor found once stays, with its own source link. What changes is how current it is, and
   presenting a months-old finding exactly like one this run re-confirmed would overstate it.
   Items found by this run carry no `last_confirmed_at`, so nothing renders for them.

   Each run also re-reads the cited page behind a carried fact (core/recheck.py). After two
   fetched misses in a row the fact is "unconfirmed": it stays on record, labelled, because a
   page that changed is not proof the fact is false — but a reviewer should not read it as current. */

const FORMAT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

export function confirmedLabel(iso) {
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? "" : `last confirmed ${FORMAT.format(when)}`;
}

export default function ConfirmedTag({ item }) {
  const status = statusFor(useContext(FactStatusContext), item);
  const label = item?.last_confirmed_at ? confirmedLabel(item.last_confirmed_at) : "";
  const stale = status?.status === "unconfirmed";
  if (!label && !stale) return null;
  return (
    <>
      {label && (
        <span className="confirmed-tag" title="Found by an earlier evaluation; this refresh did not find it again">
          {label}
        </span>
      )}
      {stale && (
        <span className="confirmed-tag confirmed-tag--stale"
          title="Its cited page was re-read on the last two runs and no longer states it">
          source no longer states this
        </span>
      )}
    </>
  );
}
