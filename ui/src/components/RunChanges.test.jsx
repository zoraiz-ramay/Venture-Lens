import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import RunChanges from "./RunChanges.jsx";
import ConfirmedTag from "./ConfirmedTag.jsx";
import { FactStatusContext } from "./factStatus.js";

describe("RunChanges", () => {
  it("sums the per-list counts and names a held market size and stale facts", () => {
    render(<RunChanges
      carryForward={{ prior_runs: 3, founders: { new: 0, reconfirmed: 2, carried: 0 },
        "landscape.competitors": { new: 1, reconfirmed: 3, carried: 4 },
        market_size_candidate: { value: "USD 3.7 billion" }, fields_carried: ["crunchbase_url"] }}
      factStatus={[{ name: "UVC Partners", status: "unconfirmed" }, { name: "Xpreneurs", status: "supported" }]} />);
    const line = screen.getByLabelText("Changes since the last run");
    expect(line).toHaveTextContent("Compared with 3 earlier runs: 1 new · 5 found again · 4 kept from earlier runs");
    expect(line).toHaveTextContent("market size kept");
    expect(line).toHaveTextContent("1 fact whose source no longer states it");
  });

  it("names the kinds of fact no run has found three times in a row", () => {
    render(<RunChanges carryForward={{ prior_runs: 2 }}
      slotStatus={{ customers: { status: "empty", empty_streak: 3 }, revenue: { status: "empty", empty_streak: 1 },
        founders: { status: "found" } }} />);
    const line = screen.getByLabelText("Changes since the last run");
    expect(line).toHaveTextContent("not found in the last 3+ runs: customers");
    expect(line).not.toHaveTextContent("revenue");
  });

  it("says nothing on a first evaluation", () => {
    const { container } = render(<RunChanges carryForward={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ConfirmedTag", () => {
  it("labels a carried fact whose source no longer states it", () => {
    const item = { name: "UVC Partners", source_url: "https://press.example/round", last_confirmed_at: "2026-10-01" };
    render(
      <FactStatusContext.Provider value={[{ name: "UVC Partners", source_url: "https://press.example/round", status: "unconfirmed" }]}>
        <ConfirmedTag item={item} />
      </FactStatusContext.Provider>);
    expect(screen.getByText(/last confirmed/)).toBeInTheDocument();
    expect(screen.getByText("source no longer states this")).toBeInTheDocument();
  });

  it("adds nothing for a first miss or without a re-check", () => {
    const item = { name: "Xpreneurs", last_confirmed_at: "2026-10-01" };
    render(
      <FactStatusContext.Provider value={[{ name: "Xpreneurs", status: "unsupported" }]}>
        <ConfirmedTag item={item} />
      </FactStatusContext.Provider>);
    expect(screen.queryByText("source no longer states this")).toBeNull();
  });
});
