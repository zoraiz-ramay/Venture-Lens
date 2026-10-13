import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/**
 * One founder, one row. Stored Radical Dot runs hold "Andreas Wagner" and "Dr. Andreas Wagner" side
 * by side; runs are never rewritten, so the profile collapses the spellings as it reads them.
 */
vi.mock("../../api.js", () => ({ api: { runBusinessFlow: vi.fn() } }));

import OverviewTab from "./OverviewTab.jsx";
import { cleanPersonName, dedupePeople, personKey, personResolver, resolveShortName, samePerson, withoutListed }
  from "./people.js";

describe("personKey", () => {
  it("ignores titles, post-nominals, a role in brackets, accents and case", () => {
    expect(personKey("Dr. Andreas Wagner")).toBe("andreas wagner");
    expect(personKey("Prof. Dr.-Ing. Max Mustermann")).toBe("max mustermann");
    expect(personKey("Alexandre Krémer (CTO)")).toBe("alexandre kremer");
    expect(personKey("Jane Doe, PhD")).toBe("jane doe");
    expect(personKey("Jack Ma")).toBe("jack ma");                  // a surname is never an affix
  });

  it("keeps the first spelling and fills only its blanks from a duplicate", () => {
    expect(dedupePeople([{ name: "Andreas Wagner", role: "" }, { name: "Dr. Andreas Wagner", role: "CEO" }]))
      .toEqual([{ name: "Andreas Wagner", role: "CEO" }]);
  });
});

describe("samePerson", () => {
  it("matches one person spelled with initials or sharing a profile link", () => {
    expect(samePerson({ name: "KD Kutadgu Gokalp Demirci" }, { name: "Kutadgu Gokalp Demirci" })).toBe(true);
    expect(samePerson({ name: "Andy Wagner", linkedin: "https://www.linkedin.com/in/wagner-andreas/" },
      { name: "Andreas Wagner", source_url: "https://linkedin.com/in/wagner-andreas" })).toBe(true);
  });

  it("keeps different people apart, even when research gave one the other's link", () => {
    expect(samePerson({ name: "Christian Piechnick" }, { name: "Maria Piechnick" })).toBe(false);
    expect(samePerson({ name: "Florian Zahn", source_url: "https://www.linkedin.com/in/anna-winiwarter" },
      { name: "Anna Winiwarter", linkedin: "https://www.linkedin.com/in/anna-winiwarter" })).toBe(false);
    expect(samePerson({ name: "Jack Ma" }, { name: "Jack" })).toBe(false);
  });

  it("gives one id to every spelling of a person", () => {
    const id = personResolver();
    expect(id({ name: "KD Kutadgu Gokalp Demirci" })).toBe(id("Kutadgu Gokalp Demirci"));
    expect(id({ name: "Maria Piechnick" })).not.toBe(id({ name: "Christian Piechnick" }));
  });

  it("drops an advisor already listed as a founder", () => {
    expect(withoutListed([{ name: "Trevor Amanya" }, { name: "Jane Roe" }], [{ name: "Trevor Amanya" }]))
      .toEqual([{ name: "Jane Roe" }]);
  });
});

describe("first names and placeholders", () => {
  // Radical Dot's third refresh stored five founders for a company with two.
  const radicalDot = [
    { name: "Andreas Wagner", source_url: "https://fundraisingfox.example/radicaldot" },
    { name: "Alex (surname not stated)", background: "HEC Paris", source_url: "https://radicaldot.notion.example" },
    { name: "Andi (surname not stated)", source_url: "https://radicaldot.notion.example" },
    { name: "Alexandre Kremer", source_url: "https://openregister.example/radical-dot" },
    { name: "Alexandre", source_url: "https://radicaldot.notion.example" },
  ];

  it("collapses every spelling to the two founders, keeping the full names", () => {
    const out = dedupePeople(radicalDot);
    expect(out.map((p) => p.name)).toEqual(["Andreas Wagner", "Alexandre Kremer"]);
    expect(out[1].background).toBe("HEC Paris");            // a blank filled from the first-name entry
  });

  it("never keeps a placeholder as a name", () => {
    expect(cleanPersonName("Alex (surname not stated)")).toBe("Alex");
    expect(cleanPersonName("Founder (name not given)")).toBe("");
    expect(cleanPersonName("Unknown")).toBe("");
    expect(dedupePeople([{ name: "Not stated" }, { name: "CEO" }])).toEqual([]);
  });

  it("drops a first name that names nobody on record, and one that could be two people", () => {
    expect(dedupePeople([{ name: "Alex" }])).toEqual([]);
    expect(dedupePeople([{ name: "Alex" }, { name: "Alexandre Kremer" }, { name: "Alexander Ochs" }])
      .map((p) => p.name)).toEqual(["Alexandre Kremer", "Alexander Ochs"]);
  });

  it("matches a first name, a prefix of one, or a common short form, and nothing looser", () => {
    expect(resolveShortName({ name: "Andi" }, [{ name: "Andreas Wagner" }])?.name).toBe("Andreas Wagner");
    expect(resolveShortName({ name: "Al" }, [{ name: "Alexandre Kremer" }])).toBeNull();
    expect(resolveShortName({ name: "Kremer" }, [{ name: "Alexandre Kremer" }])).toBeNull();
  });

  it("gives a first name the id of the one person it names", () => {
    const id = personResolver();
    expect(id({ name: "Alexandre Kremer" })).toBe(id({ name: "Alex" }));
    expect(withoutListed([{ name: "Andi" }, { name: "Jane Roe" }], [{ name: "Andreas Wagner" }]))
      .toEqual([{ name: "Jane Roe" }]);
  });

  it("shows Radical Dot's stored five founders as two", () => {
    render(<OverviewTab res={{ company: "Radical Dot", score: {}, deep_profile: { founders: radicalDot } }} />);
    expect(screen.getAllByText(/Alexandre Kremer/)).toHaveLength(1);
    expect(screen.queryByText(/surname not stated/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Andi$/)).not.toBeInTheDocument();
  });
});

describe("Profile founders", () => {
  it("shows Phena's co-founder, stored with and without his initials, once", () => {
    const res = { company: "Phena", score: {}, deep_profile: { founders: [
      { name: "KD Kutadgu Gokalp Demirci", role: "Co-Founder" }, { name: "Kutadgu Gokalp Demirci", role: "Co-Founder" }],
      advisors: [{ name: "Kutadgu Gokalp Demirci" }] } };
    render(<OverviewTab res={res} />);
    expect(screen.getAllByText(/Kutadgu Gokalp Demirci/)).toHaveLength(1);
  });


  it("shows a founder spelled two ways once", () => {
    const res = { company: "Radical Dot", score: {}, deep_profile: { founders: [
      { name: "Andreas Wagner", role: "Co-founder" }, { name: "Alexandre Kremer", role: "Co-founder" },
      { name: "Dr. Andreas Wagner", role: "CEO", background: "PhD, Cambridge" }] } };
    render(<OverviewTab res={res} />);
    expect(screen.getAllByText(/Andreas Wagner/)).toHaveLength(1);
    expect(screen.getByText(/PhD, Cambridge/)).toBeInTheDocument();     // the duplicate's detail is kept
  });
});
