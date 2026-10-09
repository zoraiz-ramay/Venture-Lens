/* Words for what the problem search returns (core/solve.py). Shared by the list, the comparison
   and the tests, so a source is called the same thing wherever it appears. */

// The rank stage's bands, in plain language: business users read "Strong match", not "84".
export const BANDS = {
  strong: "Strong match",
  good: "Good match",
  possible: "Possible match",
};

export const PROVIDERS = {
  applications: "Applied to Siemens",
  tracxn: "Tracxn",
  glassdollar: "GlassDollar",
  web: "Web",
};

// Where a single fact came from. "Self-reported" stays in the label: a pitch-form answer is the
// startup's own claim, and a reader comparing two startups should weigh it as one.
export const FACT_SOURCES = {
  application: "Their application (self-reported)",
  glassdollar_profile: "GlassDollar profile",
  evaluation: "Earlier evaluation",
};

// Comparison rows, in reading order. Customers fall back to the profile's list when the pitch
// form left "Reference customers" blank.
export const FACT_ROWS = [
  ["stage", "Development stage"],
  ["business_model", "Business model"],
  ["reference_customers", "Reference customers", "customers"],
  ["siemens_functions", "Siemens functions it serves"],
  ["partnership", "Partnership sought"],
  ["hq", "Headquarters"],
  ["founded", "Founded"],
  ["employees", "Employees"],
  ["funding", "Total funding"],
];

export const fact = (candidate, [key, , fallback]) =>
  candidate.facts?.[key] || (fallback ? candidate.facts?.[fallback] : null) || null;

/** The quote shown under a name: one the rank stage verified, else the application's own sentence. */
export function quoteOf(c) {
  if (c.quote) return { text: c.quote, from: "From its description" };
  if (c.why_quote) return { text: c.why_quote, from: "From its application" };
  return null;
}

export function evaluateHref(c) {
  return c.run_id ? `/startup/${c.run_id}` : `/startup/new?name=${encodeURIComponent(c.name)}`;
}

export const clip = (text, n) => {
  const s = String(text || "").trim();
  return s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s;
};
