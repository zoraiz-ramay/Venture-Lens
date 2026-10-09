import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import SolvePage from "./SolvePage.jsx";
import { ResearchProvider } from "../../research.jsx";
import { api } from "../../api.js";
import { findShadowRole } from "../../test/shadow.js";

vi.mock("../../api.js", () => ({ api: {
  tracxnStatus: vi.fn(), tracxnConnect: vi.fn(), tracxnDisconnect: vi.fn(), startJobs: vi.fn(), job: vi.fn(),
  solveRefine: vi.fn(), solveAsk: vi.fn(),
} }));

const ixButton = (name) => [...document.querySelectorAll("ix-button")].find((b) => b.textContent === name);
const toggle = (name) => [...document.querySelectorAll("ix-toggle-button")].find((b) => b.textContent === name);

const SENSEI = { id: "app:1", name: "Sensei Labs", source: "applications", band: "strong",
  reason: "It detects failures before they happen.", why_quote: "We detect anomalies in machine data before breakdowns.",
  description: "Anomaly detection for machines.",
  facts: { stage: { value: "Growth market stage", source: "application" } } };
const VISION = { id: "tracxn:visionco", name: "VisionCo", source: "tracxn", band: "possible", reason: "Same factories.",
  quote: "", description: "Camera inspection.", facts: {}, run_id: 12 };
const INTERPRETATION = { restatement: "Predictive maintenance for brownfield lines.", keywords: ["predictive maintenance"],
  facets: [{ id: "deployment", label: "Deployment", options: ["On-prem", "Cloud"] }] };
const RESULT = { problem: "predict failures", interpretation: INTERPRETATION, applicants: [SENSEI], market: [VISION],
  sources: [{ provider: "applications", status: "used" }, { provider: "tracxn", status: "used" }], method: "llm" };

function respondWith(record) {
  api.startJobs.mockImplementation(async (body) => ({ jobs: [{ id: "job1", kind: "solve", query: body.problem, ...record }] }));
  api.job.mockImplementation(async () => ({ id: "job1", kind: "solve", query: "predict failures", ...record }));
}

const mount = (path = "/workspace") => render(
  <MemoryRouter initialEntries={[path]}>
    <ResearchProvider userId="test">
      <Routes>
        <Route path="/workspace" element={<SolvePage />} />
        <Route path="/startup/:id" element={<p>Profile page</p>} />
      </Routes>
    </ResearchProvider>
  </MemoryRouter>);

async function search(container, text = "predict failures") {
  await userEvent.type(await findShadowRole(container, "textbox", { name: "Describe your problem" }), text);
  fireEvent.click(ixButton("Find startups"));
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  api.tracxnStatus.mockResolvedValue({ connected: false });
});

describe("Solve a Problem", () => {
  it("starts with one question and nothing else to read", async () => {
    const { container } = mount();
    expect(screen.getByRole("heading", { name: "What would you like to solve?" })).toBeInTheDocument();
    expect(await findShadowRole(container, "textbox", { name: "Describe your problem" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "Example problems" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /applied to Siemens/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: /How I read/ })).toBeNull();
  });

  it("says it is under development, before and after a search", async () => {
    respondWith({ status: "complete", result: RESULT });
    const { container } = mount();
    expect(screen.getByText("Under development.")).toBeInTheDocument();
    await search(container);
    await screen.findByText("Sensei Labs");
    expect(screen.getByText("Under development.")).toBeInTheDocument();
  });

  it("fills an example without searching until asked", async () => {
    respondWith({ status: "complete", result: RESULT });
    const { container } = mount();
    fireEvent.click(ixButton("Reduce downtime"));
    expect(api.startJobs).not.toHaveBeenCalled();
    const input = await findShadowRole(container, "textbox", { name: "Describe your problem" });
    await waitFor(() => expect(input.value).toMatch(/legacy PLC/));
    fireEvent.click(ixButton("Find startups"));
    await waitFor(() => expect(api.startJobs).toHaveBeenCalledOnce());
    expect(api.startJobs.mock.calls[0][0]).toMatchObject({ kind: "solve", problem: expect.stringMatching(/legacy PLC/) });
  });

  it("shows applicants while the market is still being searched, and says the fit is still being checked", async () => {
    respondWith({ status: "running", partial: { applicants: [{ ...SENSEI, band: null }], applicants_ranked: false } });
    const { container } = mount();
    await search(container);
    expect(await screen.findByText("Sensei Labs")).toBeInTheDocument();
    expect(screen.getByText("Checking fit…")).toBeInTheDocument();
    expect(screen.getByText(/Searching Tracxn, GlassDollar and the web/)).toBeInTheDocument();
    // Nothing still pending is rendered as an empty result.
    expect(screen.queryByText(/Nothing in the market search fits/)).toBeNull();
    expect(screen.getByText("“We detect anomalies in machine data before breakdowns.”")).toBeInTheDocument();
    expect(screen.getByText("From its application")).toBeInTheDocument();
  });

  it("re-ranks when a facet is chosen, without starting a new search", async () => {
    respondWith({ status: "complete", result: RESULT });
    api.solveRefine.mockResolvedValue({ ...RESULT, applicants: [], market: [VISION],
      refinement: { answers: { deployment: "On-prem" }, note: "", brief: "predict failures\nConstraints: Deployment: On-prem." } });
    const { container } = mount();
    await search(container);
    expect(await screen.findByText("Predictive maintenance for brownfield lines.")).toBeInTheDocument();
    fireEvent.click(toggle("On-prem"));
    await waitFor(() => expect(api.solveRefine).toHaveBeenCalledWith("job1", { deployment: "On-prem" }, ""));
    expect(await screen.findByText("Ranked for: Deployment: On-prem")).toBeInTheDocument();
    expect(screen.queryByText("Sensei Labs")).toBeNull();
    expect(api.startJobs).toHaveBeenCalledOnce();
  });

  it("compares selected startups fact by fact, with each fact's source and a dash for the unknown", async () => {
    respondWith({ status: "complete", result: RESULT });
    const { container } = mount();
    await search(container);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Sensei Labs" }));
    expect(ixButton("Compare (1)")).toHaveAttribute("disabled");
    await userEvent.click(screen.getByRole("checkbox", { name: "Select VisionCo" }));
    fireEvent.click(ixButton("Compare (2)"));
    const table = await screen.findByRole("table");
    const stage = within(table).getByRole("row", { name: /Development stage/ });
    expect(within(stage).getByText("Growth market stage")).toBeInTheDocument();
    expect(within(stage).getByText("Their application (self-reported)")).toBeInTheDocument();
    expect(within(stage).getByText("—")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Open its evaluation →" })).toHaveAttribute("href", "/startup/12");
    expect(within(table).getByRole("link", { name: "Evaluate to fill in →" })).toHaveAttribute("href", "/startup/new?name=Sensei%20Labs");
  });

  it("answers follow-ups about the selected startups and labels where the answer came from", async () => {
    respondWith({ status: "complete", result: RESULT });
    api.solveAsk.mockResolvedValue({ answer: "Sensei Labs is furthest along [1].", provider: "run", source: "This shortlist",
      evidence: [{ title: "Sensei Labs — development stage", url: "" }], note: "" });
    const { container } = mount();
    await search(container);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Sensei Labs" }));
    expect(screen.getByText(/About the 1 you selected: Sensei Labs/)).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "Ask a question about these startups" }), "Which is furthest along?{Enter}");
    await waitFor(() => expect(api.solveAsk).toHaveBeenCalledWith("job1", "Which is furthest along?", [], ["app:1"]));
    expect(await screen.findByText("This shortlist")).toBeInTheDocument();
    expect(screen.getByText(/Sensei Labs is furthest along/)).toBeInTheDocument();
  });

  it("stops its elapsed-time clock when the page is left mid-search", async () => {
    respondWith({ status: "running", partial: {} });
    const { container, unmount } = mount();
    await search(container);
    await screen.findByText(/Searching the applications/);
    const cleared = vi.spyOn(globalThis, "clearInterval");
    unmount();
    expect(cleared).toHaveBeenCalled();
    cleared.mockRestore();
  });

  it("keeps the brief after the search cannot start", async () => {
    api.startJobs.mockRejectedValue(new Error("The evaluation queue is full."));
    const { container } = mount();
    await search(container, "inspection");
    expect(await screen.findByText(/queue is full/)).toBeInTheDocument();
    expect((await findShadowRole(container, "textbox", { name: "Describe your problem" })).value).toBe("inspection");
  });

  it("offers a retry when the search itself failed", async () => {
    respondWith({ status: "error", error: "Research failed. Please retry this startup." });
    const { container } = mount();
    await search(container);
    expect(await screen.findByText(/Research failed/)).toBeInTheDocument();
    expect(ixButton("Search again")).toBeTruthy();
  });
});
