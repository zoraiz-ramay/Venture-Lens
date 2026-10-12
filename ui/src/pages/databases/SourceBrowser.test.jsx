import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api.js";
import { AppProvider } from "../../state.jsx";
import Explore from "../Explore.jsx";
import SourceBrowser from "./SourceBrowser.jsx";
import { findShadowText, getAllShadowRole, getShadowRole } from "../../test/shadow.js";

const COLUMNS = [{ key: "name", label: "Tool" }, { key: "description", label: "Description" },
  { key: "url", label: "URL" }];
const SUMMARY = {
  id: "tools", label: "Siemens tools", about: "Siemens' software and hardware catalogue.",
  feeds: "Empower and the fit stage.", file: "siemens_tools.csv", modified: "2026-10-01T09:00:00+00:00",
  available: true, reason: "", rows: 60, checksum: "ab".repeat(32), columns: COLUMNS,
};
const row = (n) => ({ name: `Tool ${n}`, description: `What tool ${n} does. `.repeat(3).trim(), url: `https://example.com/${n}` });
const page = (over = {}) => ({ ...SUMMARY, q: "", total: 60, offset: 0, limit: 25,
  items: Array.from({ length: 25 }, (_, i) => row(i + 1)), ...over });

function Where() {
  return <output data-testid="where">{useLocation().search}</output>;
}

function renderBrowser(search = "?source=tools") {
  return render(
    <MemoryRouter initialEntries={[`/explore${search}`]}>
      <SourceBrowser sourceId="tools" />
      <Where />
    </MemoryRouter>,
  );
}

const where = () => new URLSearchParams(screen.getByTestId("where").textContent);

describe("SourceBrowser", () => {
  it("shows what the source is, its file's identity and its rows", async () => {
    vi.spyOn(api, "source").mockResolvedValue(page());
    renderBrowser();
    const facts = await screen.findByRole("region", { name: "About Siemens tools" });
    expect(within(facts).getByText("siemens_tools.csv")).toBeInTheDocument();
    expect(within(facts).getByText("60")).toBeInTheDocument();
    expect(within(facts).getByText("ab".repeat(32))).toBeInTheDocument();
    expect(within(facts).getByText("Empower and the fit stage.")).toBeInTheDocument();

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["Details", "Tool", "Description", "URL"]);
    expect(within(table).getAllByRole("row")).toHaveLength(26);
    expect(within(table).getByRole("link", { name: "https://example.com/1" }))
      .toHaveAttribute("rel", "noopener noreferrer");
    expect(api.source).toHaveBeenCalledWith("tools", { q: "", offset: 0, limit: 25 });
  });

  it("renders only the columns the server names", async () => {
    vi.spyOn(api, "source").mockResolvedValue(page({
      columns: [{ key: "name", label: "Tool" }],
      items: [{ name: "Teamcenter", contact_email: "someone@example.com" }], total: 1 }));
    renderBrowser();
    expect(await screen.findByText("Teamcenter")).toBeInTheDocument();
    expect(screen.queryByText("someone@example.com")).toBeNull();
  });

  it("shows a loading state until the page arrives", async () => {
    let resolve;
    vi.spyOn(api, "source").mockReturnValue(new Promise((r) => { resolve = r; }));
    renderBrowser();
    expect(screen.getByLabelText("Loading rows")).toBeInTheDocument();
    resolve(page());
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.queryByLabelText("Loading rows")).toBeNull();
  });

  it("says so when the request fails", async () => {
    vi.spyOn(api, "source").mockRejectedValue(new Error("Unknown data source."));
    renderBrowser();
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown data source.");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("explains an empty search and an unavailable file", async () => {
    vi.spyOn(api, "source").mockResolvedValue(page({ q: "zzz", total: 0, items: [] }));
    const { container, unmount } = renderBrowser("?source=tools&search=zzz");
    expect(await findShadowText(container, "No rows match")).toBeInTheDocument();
    unmount();

    api.source.mockResolvedValue(page({ available: false, reason: "tools catalog file not found", rows: 0, total: 0, items: [] }));
    const second = renderBrowser();
    expect(await findShadowText(second.container, "tools catalog file not found")).toBeInTheDocument();
  });

  it("searches on the server once typing pauses, and keeps the search in the URL", async () => {
    vi.spyOn(api, "source").mockResolvedValue(page());
    const { container } = renderBrowser("?source=tools&page=2");
    await screen.findByRole("table");
    await userEvent.type(getShadowRole(container, "textbox", { name: /search this source/i }), "plm");
    await waitFor(() => expect(where().get("search")).toBe("plm"));
    expect(where().get("page")).toBeNull();                    // a new search starts at page 1
    await waitFor(() => expect(api.source).toHaveBeenLastCalledWith("tools", { q: "plm", offset: 0, limit: 25 }));
    // One request for the typed words, not one per keystroke.
    expect(api.source.mock.calls.filter(([, o]) => o.q && o.q !== "plm")).toHaveLength(0);
  });

  it("pages on the server and puts the page in the URL", async () => {
    vi.spyOn(api, "source").mockResolvedValue(page({ offset: 25 }));
    const { container } = renderBrowser("?source=tools&page=2");
    await screen.findByRole("table");
    expect(api.source).toHaveBeenCalledWith("tools", { q: "", offset: 25, limit: 25 });
    await waitFor(() => expect(getAllShadowRole(container, "button").some((b) => b.textContent.trim() === "3")).toBe(true));
    fireEvent.click(getAllShadowRole(container, "button").find((b) => b.textContent.trim() === "3"));
    await waitFor(() => expect(where().get("page")).toBe("3"));
    await waitFor(() => expect(api.source).toHaveBeenLastCalledWith("tools", { q: "", offset: 50, limit: 25 }));
  });

  it("opens a row to show every cell in full", async () => {
    vi.spyOn(api, "source").mockResolvedValue(page());
    renderBrowser();
    const toggle = await screen.findByRole("button", { name: "Show the full row for Tool 2" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide the full row for Tool 2" })).toHaveAttribute("aria-expanded", "true");
    // The cell appears twice now: clamped in the grid, whole in the details.
    expect(screen.getAllByText(row(2).description)).toHaveLength(2);
  });
});

describe("Databases tabs on the Explore page", () => {
  beforeEach(() => {
    vi.spyOn(api, "departments").mockResolvedValue({ departments: [] });
    vi.spyOn(api, "myRuns").mockResolvedValue({ runs: [] });
    vi.spyOn(api, "views").mockResolvedValue({ views: [] });
    vi.spyOn(api, "sources").mockResolvedValue({ sources: [SUMMARY, { ...SUMMARY, id: "needs", label: "Department needs", rows: 52 }] });
    vi.spyOn(api, "source").mockResolvedValue(page());
  });

  const renderExplore = (search = "") => render(
    <MemoryRouter initialEntries={[`/explore${search}`]}>
      <AppProvider><Explore /><Where /></AppProvider>
    </MemoryRouter>,
  );

  it("keeps the evaluated runs as the default view and lists every source as a tab", async () => {
    renderExplore("?q=acme");
    expect(await screen.findByRole("tab", { name: /Siemens tools/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Department needs/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Evaluated startups/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: /customise columns/i })).toBeInTheDocument();
    expect(api.source).not.toHaveBeenCalled();
  });

  it("switches to a source from its tab, keeping the grid's filters for the way back", async () => {
    renderExplore("?q=acme");
    fireEvent.click(await screen.findByRole("tab", { name: /Siemens tools/ }));
    await waitFor(() => expect(where().get("source")).toBe("tools"));
    expect(where().get("q")).toBe("acme");
    expect(await screen.findByRole("region", { name: "About Siemens tools" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /customise columns/i })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /Evaluated startups/ }));
    await waitFor(() => expect(where().get("source")).toBeNull());
    expect(where().get("q")).toBe("acme");
    expect(await screen.findByRole("button", { name: /customise columns/i })).toBeInTheDocument();
  });

  it("opens a shared link straight onto the source", async () => {
    renderExplore("?source=tools&search=plm");
    expect(await screen.findByRole("region", { name: "About Siemens tools" })).toBeInTheDocument();
    expect(api.source).toHaveBeenCalledWith("tools", { q: "plm", offset: 0, limit: 25 });
  });

  it("leaves the grid usable when the sources cannot be listed", async () => {
    api.sources.mockRejectedValue(new Error("Request failed (500)"));
    renderExplore();
    expect(await screen.findByRole("alert")).toHaveTextContent("The data sources could not be listed");
    expect(screen.getByRole("tab", { name: /Evaluated startups/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /customise columns/i })).toBeInTheDocument();
  });
});
