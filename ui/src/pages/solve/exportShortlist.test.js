import { describe, expect, it } from "vitest";
import { csvName, shortlistCsv, shortlistText } from "./exportShortlist.js";

const SENSEI = { id: "app:1", name: "Sensei Labs", source: "applications", band: "strong", reason: "Fits.",
  why_quote: "We detect anomalies.", description: "Anomaly detection.",
  facts: { stage: { value: "Growth", source: "application" }, hq: { value: "Munich", source: "evaluation" } }, run_id: 7 };
const WEB = { id: "web:x", name: "=HYPERLINK(\"http://evil\")", source: "web", band: "possible",
  description: "Says \"hello\".", field_sources: { description: { provider: "web", url: "https://x.example/about" } } };

const parse = (csv) => csv.replace(String.fromCharCode(0xfeff), "").split("\r\n");

describe("taking a shortlist out of the app", () => {
  it("writes a dash for a fact nobody stated, and names the source of every one that was", () => {
    const csv = shortlistCsv([SENSEI, WEB], { origin: "https://app.example" });
    expect(csv.charCodeAt(0)).toBe(0xfeff);      // Excel reads "€" and umlauts as written only with it
    const [header, sensei, web] = parse(csv);
    expect(header).toContain('"Development stage"');
    expect(sensei).toContain('"Growth · Their application (self-reported)"');
    expect(sensei).toContain('"Munich · Earlier evaluation"');
    expect(sensei).toContain('"https://app.example/startup/7"');
    expect(web).toContain('"https://x.example/about"');
    expect(web.split(",").filter((c) => c === '"—"').length).toBeGreaterThan(5);
  });

  it("defuses a cell a spreadsheet would run as a formula, and escapes quotes", () => {
    const [, , web] = parse(shortlistCsv([SENSEI, WEB]));
    expect(web.startsWith('"\'=HYPERLINK(""http://evil"")"')).toBe(true);
    expect(web).toContain('"Says ""hello""."');
  });

  it("reads as a list a colleague can follow, with what it was ranked for", () => {
    const text = shortlistText([SENSEI, WEB], { problem: "predict failures", origin: "https://app.example",
      refinement: { answers: { deployment: "On-prem" }, note: "" },
      facets: [{ id: "deployment", label: "Deployment", options: ["On-prem"] }] });
    expect(text.split("\n").slice(0, 2)).toEqual(["Shortlist for: predict failures", "Ranked for: Deployment: On-prem"]);
    expect(text).toContain("“We detect anomalies.” (From its application)");
    expect(text).toContain("Evaluation: https://app.example/startup/7");
    expect(text).toContain("Source: https://x.example/about");
    expect(text).toContain("Not evaluated in depth yet.");
    expect(text).not.toContain("—\n");
  });

  it("names the file after the problem", () => {
    expect(csvName("Predict failures on PLC lines!")).toBe("shortlist-predict-failures-on-plc-lines.csv");
    expect(csvName("")).toBe("shortlist-search.csv");
  });
});
