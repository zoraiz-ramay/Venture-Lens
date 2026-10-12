import { afterEach, describe, expect, it, vi } from "vitest";
import { pollLookup } from "./api.js";

const reply = (body, status = 200) => Promise.resolve({ status, ok: status < 400, json: () => Promise.resolve(body) });

describe("pollLookup", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks again while the server says pending and returns the finished lookup", async () => {
    const fetch = vi.fn()
      .mockReturnValueOnce(reply({ pending: true }))
      .mockReturnValueOnce(reply({ pending: true }))
      .mockReturnValueOnce(reply({ provider: "web", signals: [{ who: "UVC Partners" }] }));
    vi.stubGlobal("fetch", fetch);
    const out = await pollLookup("/api/runs/7/lookup/signals", { everyMs: 1 });
    expect(out.signals[0].who).toBe("UVC Partners");
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(fetch.mock.calls.every(([url, init]) => url.endsWith("/api/runs/7/lookup/signals") && init.method === "POST")).toBe(true);
  });

  it("surfaces a failed lookup as the server's error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValueOnce(reply({ pending: true }))
      .mockReturnValueOnce(reply({ detail: "The lookup failed. Please try again." }, 502)));
    await expect(pollLookup("/api/runs/7/lookup/funding", { everyMs: 1 })).rejects.toThrow("The lookup failed. Please try again.");
  });

  it("gives up with a message instead of polling forever", async () => {
    vi.stubGlobal("fetch", vi.fn(() => reply({ pending: true })));
    await expect(pollLookup("/api/runs/7/lookup/funding", { everyMs: 1, maxMs: 5 })).rejects.toThrow(/longer than usual/);
  });
});
