import { test, expect } from "@playwright/test";

// Self-contained: no company searches, credentials, or paid APIs are used. The job reports the
// applicants first (as the worker does) and the market on a later poll, so the journey covers the
// page while it is still filling in as well as once it is done.
const SENSEI = { id: "app:1", name: "Sensei Labs", source: "applications", band: "strong",
  reason: "It flags machine failures before they happen.", why_quote: "We detect anomalies in machine data before breakdowns.",
  description: "Anomaly detection for machines — retrofits onto existing PLCs.",
  facts: { stage: { value: "Growth market stage", source: "application" }, funding: { value: "€2.5M", source: "glassdollar_profile" } } };
const VISION = { id: "glassdollar:example-vision", name: "Example Vision", source: "glassdollar", band: "good",
  reason: "Inspects lines with cameras; would need adapting to failure prediction.", quote: "",
  description: "Visual inspection for production lines.", website: "https://example.com", facts: {} };
const INTERPRETATION = { restatement: "Predictive maintenance for brownfield lines, without replacing equipment.",
  keywords: ["predictive maintenance"], facets: [
    { id: "deployment", label: "Deployment", options: ["On-prem", "Cloud", "Either"] },
    { id: "maturity", label: "Maturity", options: ["Pilot is fine", "In production"] }] };

test("a problem becomes a shortlist that can be sharpened, compared and questioned", async ({ page }, testInfo) => {
  let polls = 0;
  let problem = "";
  const result = () => ({ problem, interpretation: INTERPRETATION, applicants: [SENSEI], market: [VISION], method: "llm",
    sources: [{ provider: "applications", status: "used" }, { provider: "tracxn", status: "not_connected" },
      { provider: "glassdollar", status: "used" }, { provider: "web", status: "skipped" }] });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body = {};
    if (path === "/api/auth/me") body = { authenticated: true, mode: "entra", user: {
      oid: "scouting-browser-test", name: "Test Reviewer", initials: "TR", email: "test@example.com", roles: [] } };
    else if (path === "/api/integrations/tracxn") body = { connected: false, configured: true };
    else if (path === "/api/my/searches") body = { runs: [] };
    else if (path === "/api/my/views") body = { views: [] };
    else if (path === "/api/my/watchlist") body = { companies: [] };
    else if (path === "/api/jobs") {
      problem = route.request().postDataJSON().problem;
      body = { jobs: [{ id: "problem-test", kind: "solve", query: problem, status: "running" }] };
    } else if (path === "/api/jobs/problem-test") {
      polls += 1;
      body = polls < 3
        ? { id: "problem-test", kind: "solve", query: problem, status: "running",
            partial: { interpretation: INTERPRETATION, applicants: [{ ...SENSEI, band: null }], applicants_ranked: false } }
        : { id: "problem-test", kind: "solve", query: problem, status: "complete", result: result() };
    } else if (path === "/api/solve/problem-test/refine") {
      body = { ...result(), applicants: [SENSEI], market: [],
        refinement: { answers: { deployment: "On-prem" }, note: "", brief: `${problem}\nConstraints: Deployment: On-prem.` } };
    } else if (path === "/api/solve/problem-test/ask") {
      body = { answer: "Sensei Labs is the further along of the two [1].", provider: "run", source: "This shortlist", note: "",
        evidence: [{ title: "Sensei Labs — development stage: Growth market stage", url: "" }] };
    }
    await route.fulfill({ json: body });
  });

  await page.goto("/workspace");
  await expect(page.getByRole("heading", { name: "Solve a Problem", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What would you like to solve?" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("problem-brief.png"), fullPage: true });

  await page.getByRole("button", { name: "Reduce downtime", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Describe your problem" })).toHaveValue(/legacy PLC/);
  await page.getByRole("button", { name: "Find startups", exact: true }).click();

  // Applicants first, with their fit still being checked and the market still searching.
  await expect(page.getByText("Sensei Labs", { exact: true })).toBeVisible();
  await expect(page.getByText(INTERPRETATION.restatement)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("problem-partial.png"), fullPage: true });

  // Then the market, ranked.
  await expect(page.getByText("Example Vision", { exact: true })).toBeVisible();
  await expect(page.getByText("Strong match")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("problem-results.png"), fullPage: true });

  await page.getByRole("checkbox", { name: "Select Sensei Labs" }).check();
  await page.getByRole("checkbox", { name: "Select Example Vision" }).check();
  await page.getByRole("button", { name: "Compare (2)" }).click();
  const table = page.getByRole("table");
  await expect(table.getByRole("row", { name: /Development stage/ })).toContainText("Growth market stage");
  await expect(table.getByRole("link", { name: "Evaluate to fill in →" }).first()).toHaveAttribute("href", /startup\/new\?name=/);
  await page.screenshot({ path: testInfo.outputPath("problem-compare.png"), fullPage: true });

  await page.getByRole("textbox", { name: "Ask a question about these startups" }).fill("Which is further along?");
  await page.getByRole("textbox", { name: "Ask a question about these startups" }).press("Enter");
  await expect(page.getByText("This shortlist")).toBeVisible();

  // Sharpening re-ranks; the market candidate that no longer fits drops out of the list.
  await page.getByRole("button", { name: "On-prem", exact: true }).click();
  await expect(page.getByText("Ranked for: Deployment: On-prem")).toBeVisible();
  await expect(page.getByText("Example Vision", { exact: true })).toHaveCount(0);

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("problem-refined.png"), fullPage: true });

  // Leaving and coming back reopens the search rather than an empty brief.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Explore a startup" })).toBeVisible();
  await page.goto("/workspace");
  await expect(page.getByText("Sensei Labs", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New problem" }).click();
  await expect(page.getByRole("heading", { name: "What would you like to solve?" })).toBeVisible();
});
