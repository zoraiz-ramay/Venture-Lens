import { BANDS, FACT_ROWS, FACT_SOURCES, PROVIDERS, evaluateHref, fact, quoteOf } from "./labels.js";

/* The shortlist taken out of the app — as text to paste into a mail or a chat, or as a CSV for a
   spreadsheet. Both carry only what the page shows, each fact with where it came from, so a
   colleague reading the copy can tell a startup's own claim from a profile and from an earlier
   evaluation. A fact nobody stated is "—" in the CSV and left out of the text. */

const foundVia = (c) => PROVIDERS[c.source] || c.source || "";
const descriptionSource = (c) => {
  const url = c.field_sources?.description?.url;
  return /^https?:\/\//.test(url || "") ? url : "";
};
const evaluationUrl = (c, origin) => (c.run_id ? `${origin}${evaluateHref(c)}` : "");
const BOM = String.fromCharCode(0xfeff);
const factSource = (f) => FACT_SOURCES[f.source] || f.source;
const factText = (f) => `${f.value} (${factSource(f)})`;

export function shortlistText(candidates, { problem = "", refinement = null, facets = [], origin = "" } = {}) {
  const lines = [`Shortlist for: ${String(problem).trim()}`];
  const label = (id) => facets.find((f) => f.id === id)?.label || id;
  const chosen = Object.entries(refinement?.answers || {}).map(([id, v]) => `${label(id)}: ${v}`);
  if (refinement?.note) chosen.push(refinement.note);
  if (chosen.length) lines.push(`Ranked for: ${chosen.join(" · ")}`);
  candidates.forEach((c, i) => {
    lines.push("", `${i + 1}. ${c.name} — ${[BANDS[c.band], foundVia(c)].filter(Boolean).join(" · ")}`);
    if (c.reason) lines.push(`   Why it fits: ${c.reason}`);
    const q = quoteOf(c);
    if (q) lines.push(`   “${q.text}” (${q.from})`);
    if (c.description) lines.push(`   What it does: ${c.description}`);
    for (const row of FACT_ROWS) {
      const f = fact(c, row);
      if (f) lines.push(`   ${row[1]}: ${factText(f)}`);
    }
    if (c.website) lines.push(`   Website: ${c.website}`);
    if (descriptionSource(c)) lines.push(`   Source: ${descriptionSource(c)}`);
    lines.push(c.run_id ? `   Evaluation: ${evaluationUrl(c, origin)}` : "   Not evaluated in depth yet.");
  });
  return lines.join("\n");
}

// A cell a spreadsheet would run as a formula is defused with a leading apostrophe: descriptions
// come from the web and from pitch forms, and "=HYPERLINK(...)" is a pitch like any other.
function cell(value) {
  let s = String(value ?? "");
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return `"${s.replace(/"/g, '""')}"`;
}

export function shortlistCsv(candidates, { origin = "" } = {}) {
  const header = ["Startup", "Match", "Found via", "Why it fits", "Quote", "Quote from", "What it does",
    ...FACT_ROWS.map((row) => row[1]), "Website", "Description source", "Evaluation"];
  const rows = candidates.map((c) => {
    const q = quoteOf(c);
    return [c.name, BANDS[c.band] || "—", foundVia(c), c.reason || "—", q?.text || "—", q?.from || "—",
      c.description || "—", ...FACT_ROWS.map((row) => { const f = fact(c, row); return f ? `${f.value} · ${factSource(f)}` : "—"; }),
      c.website || "—", descriptionSource(c) || "—", evaluationUrl(c, origin) || "—"];
  });
  // CRLF and a byte-order mark: what Excel needs to open UTF-8 (umlauts, "€") as it was written.
  return BOM + [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n");
}

export function downloadCsv(text, filename) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export const csvName = (problem) =>
  `shortlist-${String(problem).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "search"}.csv`;
