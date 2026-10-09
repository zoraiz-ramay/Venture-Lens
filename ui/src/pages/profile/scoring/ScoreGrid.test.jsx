import { afterEach, describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

/* Scoring & Fit as five compact blocks; selecting one opens the full panel it replaces in a popup
   over the page. What is pinned: every block shows its headline figure; the whole block is the
   control; the popup holds exactly that panel, one at a time, without moving the grid or
   duplicating a section id; closing returns focus to the block; and arriving by the rail, a shared
   #anchor or a ?pillar= link opens the block it names. */
vi.mock("../../../api.js", () => ({ api: {} }));

import ScoreGrid from "./ScoreGrid.jsx";

const crit = (ids, max) => ids.map(([id, label, score]) => ({ id, label, score, anchor: `${label} anchor`, rationale: "r",
  evidence: [], catalog: [], max }));
const RUN = {
  company: "Acme", department: { id: "di", label: "Digital Industries" },
  routing: { version: "pillar-route-v1", pillar: "Empower", sfs_relevant: true, sfs_line: "Equipment finance",
    sfs_lines: [{ line: "Equipment finance", fit: "strong", rationale: "Sells hardware." }] },
  assessment: {
    pillars: { Empower: { status: "assessed", total: 7, band: "strong", criteria: crit([["tool_fit", "Tool fit", 3],
      ["benefit_fit", "Benefit fit", 2], ["actionability", "Actionability", 2]], 3), notes: [] } },
    siemens_fit: { score: 78, winner: "Empower", partial: true, status: "assessed" },
    team_ecosystem: { status: "assessed", points: 12, band: "Moderate", score_0_100: 60,
      criteria: crit([["founder_experience", "Founder experience", 3], ["domain_expertise", "Domain expertise", 3],
        ["external_validation", "External validation", 3], ["strategic_network", "Strategic network", 3]], 5) },
  },
  traction: { version: "traction-rubric-v2", score_0_100: 55, confidence: 0.7, earned: 38.5, available_max: 70, fx_as_of: "2026-10-01",
    divisions: [{ id: "funding", label: "Funding", status: "evidenced", points: 22.5, max: 30, value: "€3M" },
      { id: "revenue", label: "Revenue", status: "unknown", points: 0, max: 30 }] },
  market: { status: "assessed", points: 9, band: "Moderate", score_0_100: 60, figures: {},
    criteria: crit([["market_size", "Market size", 3], ["market_growth", "Market growth", 3], ["strategic_relevance", "Strategic relevance", 3]], 5) },
};
afterEach(() => { window.history.replaceState(null, "", "/"); });

const renderGrid = (entry = "/startup/1") =>
  render(<MemoryRouter initialEntries={[entry]}><ScoreGrid res={RUN} runId={1} /></MemoryRouter>);

const dialog = () => document.querySelector("ix-modal");
const dismiss = () => fireEvent(dialog(), new CustomEvent("dialogDismiss"));

describe("ScoreGrid", () => {
  it("shows the five blocks, each with its headline figure and no separate button text", () => {
    renderGrid();
    for (const name of ["Siemens Fit", "Traction", "Team & Ecosystem", "Market"]) {
      const open = screen.getByRole("button", { name: `Open ${name} details` });
      expect(open).toHaveAttribute("aria-haspopup", "dialog");
      expect(open).toHaveTextContent("");                       // the whole box is the control
    }
    expect(screen.queryByText(/Show details/)).toBeNull();
    expect(screen.getByRole("img", { name: "Siemens Fit 78 of 100" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Traction 55 of 100" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Revenue: no evidence, excluded" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Market 60 of 100" })).toBeInTheDocument();
    expect(screen.getByText("12/20 · Moderate")).toBeInTheDocument();
    // SFS is shown whole: its verdict and line need no opening.
    const sfs = screen.getByRole("region", { name: "SFS financing" });
    expect(within(sfs).getByText("Sells hardware.")).toBeInTheDocument();
    expect(within(sfs).queryByRole("button")).toBeNull();
    expect(dialog()).toBeNull();
  });

  it("opens one panel at a time in a popup, leaving the grid and its ids as they were", () => {
    renderGrid();
    const ids = () => ["scoring-routes", "scoring-traction", "scoring-team", "scoring-market", "scoring-supplementary"]
      .map((id) => document.querySelectorAll(`#${id}`).length);
    fireEvent.click(screen.getByRole("button", { name: "Open Siemens Fit details" }));
    expect(document.querySelector("ix-modal-header")).toHaveTextContent("Siemens Fit");
    expect(within(dialog()).getByRole("group", { name: "Compare and explore partnership routes" })).toBeInTheDocument();
    expect(ids()).toEqual([1, 1, 1, 1, 1]);                      // the panel is bare inside the popup
    expect(screen.getByRole("img", { name: "Traction 55 of 100" })).toBeInTheDocument();   // grid still there
    dismiss();
    fireEvent.click(screen.getByRole("button", { name: "Open Traction details" }));
    expect(document.querySelectorAll("ix-modal")).toHaveLength(1);
    expect(within(dialog()).getByRole("list", { name: "Traction divisions" })).toBeInTheDocument();
  });

  it("closes on dismissal and returns focus to the block that opened it", async () => {
    renderGrid();
    fireEvent.click(screen.getByRole("button", { name: "Open Market details" }));
    dismiss();
    expect(dialog()).toBeNull();
    await new Promise((r) => requestAnimationFrame(r));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Open Market details" }));
  });

  it("opens on Siemens Fit when a shared link names a route", () => {
    renderGrid("/startup/1?pillar=Empower");
    expect(document.querySelector("ix-modal-header")).toHaveTextContent("Siemens Fit");
  });
});

describe("ScoreGrid navigation", () => {
  const link = (href) => { const a = document.createElement("a"); a.href = href; a.textContent = href; document.body.appendChild(a); return a; };

  it("opens a block when the rail links to it, even the entry already in the address bar", () => {
    renderGrid();
    const a = link("#scoring-market");
    fireEvent.click(a);
    expect(document.querySelector("ix-modal-header")).toHaveTextContent("Market");
    dismiss();
    fireEvent.click(a);                                 // same hash again: still opens
    expect(document.querySelector("ix-modal-header")).toHaveTextContent("Market");
    a.remove();
  });

  it("opens the block a shared #anchor names", () => {
    window.history.replaceState(null, "", "/startup/1#scoring-team");
    renderGrid();
    expect(document.querySelector("ix-modal-header")).toHaveTextContent("Team & Ecosystem");
  });

  it("stops listening once the grid is gone", () => {
    const doc = vi.spyOn(document, "removeEventListener");
    const win = vi.spyOn(window, "removeEventListener");
    const { unmount } = renderGrid();
    unmount();
    expect(doc).toHaveBeenCalledWith("click", expect.any(Function));
    expect(win).toHaveBeenCalledWith("hashchange", expect.any(Function));
    doc.mockRestore(); win.mockRestore();
  });
});
