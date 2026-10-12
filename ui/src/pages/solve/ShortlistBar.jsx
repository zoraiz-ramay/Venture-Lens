import React from "react";
import { IxButton } from "@siemens/ix-react";
import { csvName, downloadCsv, shortlistCsv, shortlistText } from "./exportShortlist.js";

export const MAX_COMPARE = 4;
export const MAX_EVALUATE = 10;

/* What the reviewer can do with the startups they kept, pinned to the bottom of the page while
   they read: compare them, evaluate them in depth, or take them out of the app with their sources.
   It also says whether the shortlist is saved, and how many of it the current choices hide — a
   kept startup a sharper brief no longer lists is still kept, not silently dropped. */
export default function ShortlistBar({ picked, hidden, done, saved, saveError, problem, refinement, facets,
  onCompare, onEvaluate, onClear, onNotice }) {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(shortlistText(picked, { problem, refinement, facets, origin }));
      onNotice(`Copied ${picked.length} ${picked.length === 1 ? "startup" : "startups"} with their sources.`);
    } catch {
      onNotice("The browser did not allow copying. Download the CSV instead.");
    }
  };
  // Said as it is: a shortlist the server has not kept would be lost with the tab.
  const status = saveError ? `Not saved: ${saveError}` : saved ? "Saved with this search"
    : done ? "Not saved — it will be lost when you leave" : "Saved once the search finishes";
  return (
    <div className="solve-selection" role="region" aria-label="Your shortlist">
      <span>
        <strong>{picked.length}</strong> shortlisted
        {hidden > 0 && <> · {hidden} hidden by your choices</>}
        <small className={saveError ? "solve-save-error" : "muted"}> · {status}</small>
      </span>
      <IxButton disabled={picked.length < 2} onClick={onCompare}>
        {picked.length > MAX_COMPARE ? `Compare first ${MAX_COMPARE}` : `Compare (${picked.length})`}</IxButton>
      <IxButton variant="secondary" onClick={onEvaluate}>
        {picked.length > MAX_EVALUATE ? `Evaluate first ${MAX_EVALUATE}` : `Evaluate (${picked.length})`}</IxButton>
      <IxButton variant="secondary" onClick={copy}>Copy list</IxButton>
      <IxButton variant="secondary" onClick={() => downloadCsv(shortlistCsv(picked, { origin }), csvName(problem))}>Download CSV</IxButton>
      <IxButton variant="tertiary" onClick={onClear}>Clear</IxButton>
    </div>
  );
}
