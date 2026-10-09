import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { IxModal, IxModalContent, IxModalHeader } from "@siemens/ix-react";
import FitComparison from "./FitComparison.jsx";
import MarketScorePanel from "./MarketScorePanel.jsx";
import { FitCard, MarketCard, SfsCard, TeamCard, TractionCard } from "./ScoreCards.jsx";
import TeamPanel from "./TeamPanel.jsx";
import TractionPanel from "./TractionPanel.jsx";

/* Scoring & Fit's five blocks, two to a row, by weight: Siemens Fit (35%) and Traction (30%), then
   Team & Ecosystem (20%) and Market (15%), then SFS, which is outside the total and shown whole.

   A block is its compact card (ScoreCards); selecting it opens the full panel it replaces in an iX
   modal over the page, so the grid never moves and the detail never pushes the other blocks down.
   The modal is rendered inline rather than through iX's modal service: the panels read the router
   (?pillar=, ?dept=) and the service would mount them outside it. The panel renders bare inside —
   the card keeps the section id, so the rail, the scroll-spy and shared #anchors are unchanged. */
const BLOCKS = [
  { key: "routes", id: "scoring-routes", title: "Siemens Fit", Card: FitCard, Panel: FitComparison },
  { key: "traction", id: "scoring-traction", title: "Traction", Card: TractionCard, Panel: TractionPanel },
  { key: "team", id: "scoring-team", title: "Team & Ecosystem", Card: TeamCard, Panel: TeamPanel },
  { key: "market", id: "scoring-market", title: "Market", Card: MarketCard, Panel: MarketScorePanel },
  // Shown whole in its box: there is no more to open (ScoreCards.SfsCard).
  { key: "sfs", id: "scoring-supplementary", title: "SFS financing", Card: SfsCard, Panel: null },
];

// The block a `#section` anchor names, if it has more to show.
const byHash = (hash) => BLOCKS.find((b) => b.Panel && `#${b.id}` === hash)?.key || null;

export default function ScoreGrid({ res, runId = null, detailed = false }) {
  const [params, setParams] = useSearchParams();
  const modal = useRef(null);
  // Arriving at a block — a rail entry, a shared #anchor — opens it; a shared link that names a
  // route (`?pillar=`) opens Siemens Fit, where that route lives.
  const [open, setOpen] = useState(() => byHash(window.location.hash) || (params.get("pillar") ? "routes" : null));
  const block = BLOCKS.find((b) => b.key === open);

  useEffect(() => {
    // A link click as well as the hash change: clicking the rail entry already in the address bar
    // (say, after closing that popup) changes no hash and would otherwise do nothing.
    const onClick = (e) => { const a = e.target.closest?.("a[href^='#']"); const key = a && byHash(a.getAttribute("href")); if (key) setOpen(key); };
    const onHash = () => { const key = byHash(window.location.hash); if (key) setOpen(key); };
    document.addEventListener("click", onClick);
    window.addEventListener("hashchange", onHash);
    return () => { document.removeEventListener("click", onClick); window.removeEventListener("hashchange", onHash); };
  }, []);
  // The modal is shown once it is in the DOM; iX restores focus to what opened it when it closes.
  useEffect(() => { if (block) modal.current?.showModal?.(); }, [block]);

  // Closing also takes the popup out of the address bar — the route it explored (`?pillar=`) and the
  // block anchor that opened it. Left there, they read as a shared link the next time this grid
  // mounts, and switching back to Scoring & Fit from another view reopened the popup.
  const close = () => {
    const back = block;
    setOpen(null);
    if (byHash(window.location.hash)) {
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    }
    if (params.has("pillar")) {
      setParams((old) => { const next = new URLSearchParams(old); next.delete("pillar"); return next; }, { replace: true });
    }
    if (back) requestAnimationFrame(() => document.querySelector(`#${back.id} .score-card-open`)?.focus());
  };
  return (
    <>
      <div className="score-grid">
        {BLOCKS.map((b) => (
          <div key={b.key} className="score-cell">
            <b.Card res={res} onOpen={b.Panel ? () => setOpen(b.key) : null} />
          </div>
        ))}
      </div>
      {block && (
        <IxModal key={block.key} ref={modal} size="full-width" closeOnBackdropClick className="score-dialog"
          onDialogClose={close} onDialogDismiss={close}>
          <IxModalHeader>{block.title}</IxModalHeader>
          <IxModalContent>
            <block.Panel res={res} runId={runId} detailed={detailed} inDialog />
          </IxModalContent>
        </IxModal>
      )}
    </>
  );
}
