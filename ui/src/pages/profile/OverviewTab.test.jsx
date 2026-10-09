import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

/* The profile streams in before its research finishes, so an empty section can mean "not found
   yet". These pin that the page only states a finding once the finished profile has arrived. */
vi.mock("../../api.js", () => ({ api: { runBusinessFlow: vi.fn() } }));

import OverviewTab from "./OverviewTab.jsx";

const base = { company: "Acme", score: {}, profile: { company_name: "Acme" }, deep_profile: {} };

describe("OverviewTab while the profile is still being researched", () => {
  it("says the team and headcount are still being researched, not that none were found", () => {
    render(<OverviewTab res={{ ...base, streaming: true, profile_researching: true }} />);
    expect(screen.getByText(/Still researching founders/)).toBeInTheDocument();
    expect(screen.getByText(/Still researching the headcount history/)).toBeInTheDocument();
    expect(screen.queryByText(/No researched team data/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No cited headcount history/)).not.toBeInTheDocument();
  });

  it("states the empty findings once the finished profile has arrived", () => {
    render(<OverviewTab res={{ ...base, streaming: true, profile_researching: false }} />);
    expect(screen.getByText(/No researched team data/)).toBeInTheDocument();
    expect(screen.getByText(/No cited headcount history/)).toBeInTheDocument();
  });

  it("treats a stored run as finished whatever it carries", () => {
    render(<OverviewTab res={{ ...base, profile_researching: true }} />);
    expect(screen.getByText(/No researched team data/)).toBeInTheDocument();
  });
});
