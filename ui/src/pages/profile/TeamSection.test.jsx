import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/* Founders and external validation are searched once and reused; the section's own Refresh is
   where they are searched again, and what it adds is marked. */
vi.mock("../../api.js", () => ({ api: { runLookup: vi.fn() } }));

import { api } from "../../api.js";
import TeamSection from "./TeamSection.jsx";

const ADA = { name: "Ada Lovelace", role: "CEO", source_url: "https://ada.test" };
const RUN = { run_id: 7501, deep_profile: { founders: [ADA], programs: [], commercial: { investors: [] } } };

beforeEach(() => vi.clearAllMocks());

describe("TeamSection", () => {
  it("shows the run's founders at once, then the stored record, without searching", async () => {
    let resolve;
    api.runLookup.mockReturnValue(new Promise((r) => { resolve = r; }));
    render(<TeamSection res={RUN} />);
    expect(screen.getByText(/Ada Lovelace/)).toBeInTheDocument();
    expect(api.runLookup).toHaveBeenCalledWith(7501, "team", false);
    resolve({ founders: [ADA], advisors: [], key_team: [], programs: [],
      investors: [{ name: "Point Nine", source_url: "https://p9.test" }], retrieved_at: "2026-10-08T10:00:00+00:00", from_store: true });
    expect(await screen.findByRole("link", { name: "Point Nine" })).toHaveAttribute("href", "https://p9.test");
    expect(screen.getByText("Searched 2026-10-08")).toBeInTheDocument();
  });

  it("searches again on Refresh and marks what it found", async () => {
    api.runLookup.mockResolvedValueOnce({ founders: [ADA], programs: [], investors: [], retrieved_at: "", from_store: false });
    render(<TeamSection res={{ ...RUN, run_id: 7502 }} />);      // the lookup is cached per run
    await waitFor(() => expect(api.runLookup).toHaveBeenCalledTimes(1));
    api.runLookup.mockResolvedValueOnce({ founders: [ADA, { name: "Grace Hopper", role: "CTO", new: true }], programs: [],
      investors: [], retrieved_at: "2026-10-09T10:00:00+00:00" });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(api.runLookup).toHaveBeenLastCalledWith(7502, "team", true);
    expect(await screen.findByText(/Grace Hopper/)).toBeInTheDocument();
    expect(screen.getByText("New")).toBeInTheDocument();
  });

  it("shows partners and links a founder's background to the page it was read from", async () => {
    api.runLookup.mockResolvedValueOnce({
      founders: [{ ...ADA, background: "PhD, University of Cambridge", background_source: "https://cam.test/ada" }],
      programs: [], investors: [], retrieved_at: "", from_store: true,
      partners: [{ name: "TUM Catalysis Research Center", type: "research", source_url: "https://tum.test/crc" }] });
    render(<TeamSection res={{ ...RUN, run_id: 7503 }} />);
    expect(await screen.findByRole("link", { name: "TUM Catalysis Research Center" })).toHaveAttribute("href", "https://tum.test/crc");
    expect(screen.getByText(/PhD, University of Cambridge/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Source" })).toHaveAttribute("href", "https://cam.test/ada");
  });
});
