import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import SiemensRelationship from "./SiemensRelationship.jsx";
import PillarMethod from "./PillarMethod.jsx";

/* An existing Siemens relationship adds a point to Collaborate in the engine. The reviewer must be
   able to see which engagement earned it, open its source, and tell a claim from a corroboration —
   and see the engagements that did NOT count, with the reason. */
const PILOT = { unit: "Siemens Mobility", type: "pilot", product: "", year: "2024", claimed: false,
  quote: "Acme launches a pilot with Siemens Mobility.", source_url: "https://news.example/a" };
const USES = { unit: "Siemens", type: "uses_product", product: "Teamcenter", year: "", claimed: true,
  quote: "Acme integrates with Teamcenter.", source_url: "https://acme.io/integrations" };
const APP_ONLY = { unit: "Siemens", type: "customer", product: "", year: "", claimed: true,
  quote: "Siemens is a customer.", source_url: "" };

const crit = (scores) => ["capability_fit", "need_fit", "actionability"].map((id, i) =>
  ({ id, label: id, score: scores[i], anchor: "a", evidence: [], catalog: [] }));
const BONUS = { pillar: "Collaborate", status: "assessed", total: 7, model_total: 6, relationship_bonus: 1,
  band: "strong", criteria: crit([2, 2, 2]), notes: [] };

describe("Existing Siemens relationship on Collaborate", () => {
  it("names each engagement with its source and says it added a point", () => {
    render(<SiemensRelationship res={{ deep_profile: { siemens_engagements: [PILOT, USES] } }} pillar={BONUS} />);
    const section = screen.getByRole("region", { name: "Existing Siemens relationship" });
    expect(within(section).getByText("Siemens Mobility: pilot (2024)")).toBeInTheDocument();
    expect(within(section).getByText("Siemens: uses Teamcenter")).toBeInTheDocument();
    expect(within(section).getByText(/Adds \+1 to Collaborate \(criteria 6\/9, total 7\/9\)/)).toBeInTheDocument();
    expect(within(section).getAllByRole("link", { name: /Source/ })).toHaveLength(2);
  });

  it("labels the company's own statement as a claim", () => {
    render(<SiemensRelationship res={{ deep_profile: { siemens_engagements: [USES] } }} pillar={BONUS} />);
    expect(screen.getByText("claimed")).toBeInTheDocument();
    expect(screen.getByText(/the company's own statement/)).toBeInTheDocument();
  });

  it("shows an application-only mention as not counted, and no bonus without a link", () => {
    render(<SiemensRelationship res={{ deep_profile: { siemens_engagements: [APP_ONLY] } }}
      pillar={{ ...BONUS, relationship_bonus: undefined, total: 6 }} />);
    expect(screen.getByText(/no source link, not counted/)).toBeInTheDocument();
    expect(screen.getByText(/none adds to the score/)).toBeInTheDocument();
  });

  it("renders nothing when the run found no relationship", () => {
    const { container } = render(<SiemensRelationship res={{ deep_profile: {} }} pillar={BONUS} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says in the method total that a point was added beside the criteria", () => {
    render(<PillarMethod pillar={BONUS} row={{ bandLabel: "Strong", value: 78 }} name="Collaborate" onOpenCriterion={() => {}} />);
    expect(screen.getByText(/including \+1 for an existing Siemens relationship \(criteria 6\/9\)/)).toBeInTheDocument();
  });
});
