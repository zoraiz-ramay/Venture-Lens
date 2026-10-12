// Single API client: request timeouts, JSON handling, normalized errors.
// SECURITY: no tokens in the browser bundle — Vite exposes all VITE_* variables in
// client source. Sign-in happens entirely server-side (Entra ID via api/auth.py) and the
// browser holds nothing but an httpOnly session cookie it cannot read.
const BASE = import.meta.env.VITE_API_BASE || "";

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function readCookie(name) {
  return document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`))
    ?.split("=")[1] ?? "";
}

// Set by AuthProvider. Kept as a module-level hook rather than a redirect inside request()
// because a 401 must NOT navigate: an evaluation runs for up to four minutes, and bouncing
// the whole page to the sign-in flow mid-request would discard whatever the reviewer was
// doing. Flipping React state instead lets the app swap to the sign-in screen in place.
let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  onUnauthorized = typeof fn === "function" ? fn : () => {};
}

async function request(path, { method = "GET", body, timeoutMs = 30000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      signal: ctrl.signal,
      credentials: "same-origin",
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        // Double-submit half of the CSRF defence. Reads are exempt; the server checks this
        // against the token it stored at sign-in, not against the cookie.
        ...(method === "GET" ? {} : { "X-CSRF-Token": readCookie("sea_csrf") }),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      onUnauthorized();
      throw new ApiError(data.detail || "Your session has ended.", 401);
    }
    if ([404, 405].includes(res.status) && path === "/api/jobs") {
      throw new ApiError("The search service needs an update. Restart the API with the latest code, then try again.", res.status);
    }
    if (!res.ok) throw new ApiError(data.detail || `Request failed (${res.status})`, res.status);
    return data;
  } catch (e) {
    if (e.name === "AbortError") throw new ApiError("Request timed out.", 0);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// A profile lookup runs on the server and answers {pending: true} until it is done
// (api/jobs.in_background): market signals alone took a minute measured, which one long request
// could not be relied on to survive. Each poll is a short request; the lookup itself finishes on
// the server whether or not this page is still open.
const LOOKUP_POLL_MS = 2000;
const LOOKUP_MAX_MS = 6 * 60 * 1000;

export async function pollLookup(path, { everyMs = LOOKUP_POLL_MS, maxMs = LOOKUP_MAX_MS } = {}) {
  const deadline = Date.now() + maxMs;
  for (;;) {
    const data = await request(path, { method: "POST" });
    if (!data?.pending) return data;
    if (Date.now() > deadline) throw new ApiError("This is taking longer than usual. Try again in a minute.", 0);
    await new Promise((resolve) => setTimeout(resolve, everyMs));
  }
}

export const api = {
  search: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),
  decideRun: (id) => request(`/api/runs/${encodeURIComponent(id)}/decision`, { method: "POST", timeoutMs: 180000 }),
  startJobs: (body) => request("/api/jobs", { method: "POST", body }),
  job: (id) => request(`/api/jobs/${encodeURIComponent(id)}`),
  departments: () => request("/api/departments"),
  runDepartments: (id) => request(`/api/runs/${encodeURIComponent(id)}/departments`),
  assessDepartment: (id, department) => request(`/api/runs/${encodeURIComponent(id)}/departments/${encodeURIComponent(department)}`, { method: "POST", timeoutMs: 240000 }),
  tracxnStatus: () => request("/api/integrations/tracxn"),
  tracxnConnect: (returnTo = "workspace") => request(`/api/integrations/tracxn/connect?return_to=${encodeURIComponent(returnTo)}`, { method: "POST" }),
  tracxnDisconnect: () => request("/api/integrations/tracxn", { method: "DELETE" }),
  // Solve a Problem's follow-ups on a finished search job: re-rank with chosen facets (no new
  // search), and questions answered from the shortlist first.
  solveRefine: (jobId, answers, note = "") =>
    request(`/api/solve/${encodeURIComponent(jobId)}/refine`, { method: "POST", body: { answers, note }, timeoutMs: 90000 }),
  solveAsk: (jobId, question, history = [], ids = []) =>
    request(`/api/solve/${encodeURIComponent(jobId)}/ask`, { method: "POST", body: { question, history, ids }, timeoutMs: 120000 }),
  // The searches a reviewer keeps, server-side and private to them: the list, opening one (kept the
  // first time its job is seen finished; returns its result, shortlist and follow-up thread), the
  // shortlist, and forgetting one.
  solveSearches: () => request("/api/solve/searches"),
  solveKeep: (jobId) => request(`/api/solve/searches/${encodeURIComponent(jobId)}`, { method: "POST" }),
  solveShortlist: (jobId, ids) =>
    request(`/api/solve/searches/${encodeURIComponent(jobId)}/shortlist`, { method: "PUT", body: { ids } }),
  solveForget: (jobId) => request(`/api/solve/searches/${encodeURIComponent(jobId)}`, { method: "DELETE" }),
  // The reviewer's own list. `runs` is the same row shape but spans everyone, so it is
  // admin-only — a non-admin calling it gets a 403, by design.
  myRuns: () => request("/api/my/searches"),
  runs: () => request("/api/runs"),
  run: (id) => request(`/api/runs/${encodeURIComponent(id)}`),
  adminOverview: () => request("/api/admin/overview"),
  adminSearches: () => request("/api/admin/searches"),
  adminList: () => request("/api/admin/admins"),
  adminTokenUsage: (limit = 100) => request(`/api/admin/token-usage?limit=${limit}`),
  adminTokenUsageRun: (runId) => request(`/api/admin/token-usage/${encodeURIComponent(runId)}`),
  // Siemens catalog tools Empower recommended that a web search could not find.
  adminToolChecks: (status = "not_found") => request(`/api/admin/tool-checks?status=${encodeURIComponent(status)}`),
  adminGrant: (upn, note = "") =>
    request("/api/admin/admins", { method: "POST", body: { upn, note } }),
  adminRevoke: (upn) =>
    request(`/api/admin/admins/${encodeURIComponent(upn)}`, { method: "DELETE" }),
  views: () => request("/api/my/views"),
  saveView: (name, columns, filters) =>
    request("/api/my/views", { method: "POST", body: { name, columns, filters } }),
  deleteView: (name) =>
    request(`/api/my/views/${encodeURIComponent(name)}`, { method: "DELETE" }),
  // "How this startup works": five plain, cited sentences, written once per run and cached.
  runBusinessFlow: (runId) => pollLookup(`/api/runs/${encodeURIComponent(runId)}/business-flow`),
  // Lookups on request, Tracxn first, then web search: kind is "funding" (rounds and investor
  // profiles), "headcount" (sourced employee figures), "signals" or "team".
  runLookup: (runId, kind, refresh = false) =>
    pollLookup(`/api/runs/${encodeURIComponent(runId)}/lookup/${kind}${refresh ? "?refresh=true" : ""}`),
  // `history` is the chat so far ({role, text}), so a follow-up keeps its subject. The server also
  // stores each turn in the reviewer's thread for that company, which chat() reads back.
  ask: (question, runId = null, history = []) =>
    request("/api/ask", { method: "POST", body: { question, run_id: runId, history }, timeoutMs: 120000 }),
  chat: (runId = null) => request(`/api/my/chat${runId != null ? `?run_id=${encodeURIComponent(runId)}` : ""}`),
  clearChat: (runId = null) =>
    request(`/api/my/chat${runId != null ? `?run_id=${encodeURIComponent(runId)}` : ""}`, { method: "DELETE" }),
  me: () => request("/api/auth/me"),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  status: () => request("/api/status"),
  // Databases: the files the engine reads (api/sources.py), read-only. Columns are allow-listed
  // and contact details scrubbed on the server; search and paging happen there too, and there is
  // deliberately no call that downloads a file.
  sources: () => request("/api/sources"),
  source: (id, { q = "", offset = 0, limit = 25 } = {}) =>
    request(`/api/sources/${encodeURIComponent(id)}?${new URLSearchParams({ q, offset: String(offset), limit: String(limit) })}`),
};
export { ApiError };
