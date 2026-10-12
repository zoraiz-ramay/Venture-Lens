import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import SolvePage from "./SolvePage.jsx";
import { ResearchProvider } from "../../research.jsx";
import { api } from "../../api.js";
import { findShadowRole } from "../../test/shadow.js";

/* Working with a search over time: it is kept with its shortlist and follow-ups, reopened from the
   reviewer's list after its job has gone, narrowed by band, acted on per row, and taken out of
   the app with its sources. */

vi.mock("../../api.js", () => ({ api: {
  tracxnStatus: vi.fn(), tracxnConnect: vi.fn(), tracxnDisconnect: vi.fn(), startJobs: vi.fn(), job: vi.fn(),
  solveRefine: vi.fn(), solveAsk: vi.fn(), solveSearches: vi.fn(), solveKeep: vi.fn(), solveShortlist: vi.fn(),
  solveForget: vi.fn(),
} }));

const ixButton = (name) => [...document.querySelectorAll("ix-button")].find((b) => b.textContent === name);
const toggle = (name) => [...document.querySelectorAll("ix-toggle-button")].find((b) => b.textContent === name);

const SENSEI = { id: "app:1", name: "Sensei Labs", source: "applications", band: "strong",
  reason: "It detects failures before they happen.", why_quote: "We detect anomalies in machine data before breakdowns.",
  description: "Anomaly detection for machines.",
  facts: { stage: { value: "Growth market stage", source: "application" } } };
const VISION = { id: "tracxn:visionco", name: "VisionCo", source: "tracxn", band: "possible", reason: "Same factories.",
  description: "Camera inspection.", facts: {}, run_id: 12, website: "https://vision.example" };
const FACETS = [{ id: "deployment", label: "Deployment", options: ["On-prem", "Cloud"] }];
const RESULT = { problem: "predict failures", interpretation: { restatement: "Predictive maintenance.", facets: FACETS },
  applicants: [SENSEI], market: [VISION], sources: [{ provider: "tracxn", status: "used" }], method: "llm" };

function respondWith(record) {
  api.startJobs.mockImplementation(async (body) => ({ jobs: [{ id: "job1", kind: "solve", query: body.problem, ...record }] }));
  api.job.mockImplementation(async () => ({ id: "job1", kind: "solve", query: "predict failures", ...record }));
}
const kept = (over = {}) => ({ id: "job1", problem: "predict failures", result: RESULT, shortlist: [], thread: [], ...over });

const mount = (path = "/workspace") => render(
  <MemoryRouter initialEntries={[path]}>
    <ResearchProvider userId="test">
      <Routes>
        <Route path="/workspace" element={<SolvePage />} />
        <Route path="/startup/:id" element={<p>Profile page</p>} />
      </Routes>
    </ResearchProvider>
  </MemoryRouter>);

async function search(container) {
  await userEvent.type(await findShadowRole(container, "textbox", { name: "Describe your problem" }), "predict failures");
  fireEvent.click(ixButton("Find startups"));
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  api.tracxnStatus.mockResolvedValue({ connected: false });
  api.solveSearches.mockResolvedValue({ searches: [] });
  api.solveKeep.mockResolvedValue(kept());
  api.solveShortlist.mockImplementation(async (id, ids) => ({ shortlist: ids }));
  api.solveForget.mockResolvedValue({});
});

describe("Solve a Problem, day to day", () => {
  it("saves the shortlist with the search, and finds it again after a reload", async () => {
    respondWith({ status: "complete", result: RESULT });
    const first = mount();
    await search(first.container);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Sensei Labs" }));
    await waitFor(() => expect(api.solveShortlist).toHaveBeenLastCalledWith("job1", ["app:1"]));
    expect(within(screen.getByRole("region", { name: "Your shortlist" })).getByText(/Saved with this search/)).toBeInTheDocument();
    first.unmount();

    sessionStorage.clear();                      // another tab: the session knows nothing of the job
    api.job.mockRejectedValue(Object.assign(new Error("gone"), { status: 404 }));
    api.solveKeep.mockResolvedValue(kept({ shortlist: ["app:1"] }));
    mount("/workspace?job=job1");
    const sensei = await screen.findByRole("checkbox", { name: "Select Sensei Labs" });
    await waitFor(() => expect(sensei).toBeChecked());
    expect(screen.getByRole("checkbox", { name: "Select VisionCo" })).not.toBeChecked();
    expect(within(screen.getByRole("region", { name: "Your shortlist" })).getByText("1")).toBeInTheDocument();
  });

  it("saves what was picked while the search was still running, once it has finished", async () => {
    respondWith({ status: "running", partial: { applicants: [{ ...SENSEI, band: null }], applicants_ranked: false } });
    const { container } = mount();
    await search(container);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Sensei Labs" }));
    expect(screen.getByRole("region", { name: "Your shortlist" })).toHaveTextContent("Saved once the search finishes");
    expect(api.solveShortlist).not.toHaveBeenCalled();
    respondWith({ status: "complete", result: RESULT });
    await waitFor(() => expect(api.solveShortlist).toHaveBeenCalledWith("job1", ["app:1"]), { timeout: 4000 });
    expect(screen.getByRole("checkbox", { name: "Select Sensei Labs" })).toBeChecked();
  });

  it("lists past problems and reopens one as it was left — refined, shortlisted, with its follow-ups", async () => {
    api.solveSearches.mockResolvedValue({ searches: [{ id: "old1", problem: "detect surface defects", applicants: 1, market: 1,
      shortlisted: 1, refined: true, created_at: "2026-10-01T09:00:00+00:00", updated_at: "2026-10-02T09:00:00+00:00" }] });
    api.job.mockRejectedValue(Object.assign(new Error("gone"), { status: 404 }));
    api.solveKeep.mockResolvedValue(kept({ id: "old1", problem: "detect surface defects", shortlist: ["tracxn:visionco"],
      result: { ...RESULT, refinement: { answers: { deployment: "On-prem" }, note: "", brief: "x" } },
      thread: [{ role: "user", text: "Which sell to OEMs?" },
        { role: "assistant", text: "VisionCo does [1].", provider: "run", source: "This shortlist", evidence: [] }] }));
    mount();
    const link = await screen.findByRole("link", { name: "detect surface defects" });
    expect(screen.getByText(/1 applied · 1 from the market · 1 shortlisted · sharpened/)).toBeInTheDocument();
    await userEvent.click(link);
    expect(await screen.findByText("Ranked for: Deployment: On-prem")).toBeInTheDocument();
    await waitFor(() => expect(api.solveKeep).toHaveBeenCalledWith("old1"));
    expect(screen.getByRole("checkbox", { name: "Select VisionCo" })).toBeChecked();
    expect(screen.getByText("Which sell to OEMs?")).toBeInTheDocument();
    expect(screen.getByText("This shortlist")).toBeInTheDocument();
  });

  it("says plainly when a search to reopen is gone, and offers a new one", async () => {
    api.job.mockRejectedValue(Object.assign(new Error("gone"), { status: 404 }));
    api.solveKeep.mockRejectedValue(Object.assign(new Error("Not found"), { status: 404 }));
    mount("/workspace?job=missing");
    expect(await screen.findByRole("alert")).toHaveTextContent(/no longer available/);
    expect(ixButton("New problem")).toBeTruthy();
  });

  it("forgets a past problem", async () => {
    api.solveSearches.mockResolvedValue({ searches: [{ id: "old1", problem: "detect surface defects", updated_at: "2026-10-02" }] });
    mount();
    await screen.findByRole("link", { name: "detect surface defects" });
    await userEvent.click(await findShadowRole(document.body, "button", { name: /^Forget/ }));
    await waitFor(() => expect(api.solveForget).toHaveBeenCalledWith("old1"));
    await waitFor(() => expect(screen.queryByRole("link", { name: "detect surface defects" })).toBeNull());
  });

  it("narrows both lists by band, and says how many it hides", async () => {
    respondWith({ status: "complete", result: RESULT });
    const { container } = mount();
    await search(container);
    await screen.findByText("VisionCo");
    fireEvent.click(toggle("Strong"));
    await waitFor(() => expect(screen.queryByText("VisionCo")).toBeNull());
    expect(screen.getByText("Sensei Labs")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "From the market (0 of 1)" })).toBeInTheDocument();
    expect(screen.getByText("None of these match the filter above.")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 2")).toBeInTheDocument();
    fireEvent.click(ixButton("Show all"));
    expect(await screen.findByText("VisionCo")).toBeInTheDocument();
  });

  it("offers each startup's next step on its row, named for the startup", async () => {
    respondWith({ status: "complete", result: RESULT });
    const { container } = mount();
    await search(container);
    expect(await screen.findByRole("link", { name: "Evaluate in depth: Sensei Labs" }))
      .toHaveAttribute("href", "/startup/new?name=Sensei%20Labs");
    expect(screen.getByRole("link", { name: "Open its evaluation: VisionCo" })).toHaveAttribute("href", "/startup/12");
  });

  it("keeps a shortlisted startup that a refinement hides, and says so", async () => {
    respondWith({ status: "complete", result: RESULT });
    api.solveRefine.mockResolvedValue({ ...RESULT, applicants: [], market: [VISION],
      unrefined: { applicants: [SENSEI], market: [VISION] },
      refinement: { answers: { deployment: "Cloud" }, note: "", brief: "predict failures\nConstraints: Deployment: Cloud." } });
    const { container } = mount();
    await search(container);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Sensei Labs" }));
    fireEvent.click(toggle("Cloud"));
    await screen.findByText("Ranked for: Deployment: Cloud");
    expect(screen.queryByRole("checkbox", { name: "Select Sensei Labs" })).toBeNull();
    const bar = screen.getByRole("region", { name: "Your shortlist" });
    expect(bar).toHaveTextContent("1 shortlisted · 1 hidden by your choices");
    expect(api.solveShortlist).toHaveBeenLastCalledWith("job1", ["app:1"]);
  });

  it("copies the shortlist with each fact's source", async () => {
    const writeText = vi.fn().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    respondWith({ status: "complete", result: RESULT });
    const { container } = mount();
    await search(container);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Sensei Labs" }));
    fireEvent.click(ixButton("Copy list"));
    await waitFor(() => expect(writeText).toHaveBeenCalledOnce());
    const text = writeText.mock.calls[0][0];
    expect(text).toMatch(/^Shortlist for: predict failures/);
    expect(text).toContain("1. Sensei Labs — Strong match · Applied to Siemens");
    expect(text).toContain("Development stage: Growth market stage (Their application (self-reported))");
    expect(await screen.findByText("Copied 1 startup with their sources.")).toBeInTheDocument();
  });
});
