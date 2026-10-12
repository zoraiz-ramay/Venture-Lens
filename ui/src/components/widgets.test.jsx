import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExtLink, Loading } from "./widgets.jsx";

/**
 * X-03 / PROF-11 — loading state is announced, not just drawn.
 *
 * Replaces tests/test_accessible_loading.py, which regex-matched `aria-live` in the JSX source.
 * That assertion passed while the attribute sat in a comment and failed the moment the file was
 * reformatted; it never once proved a screen reader would hear anything.
 *
 * MIG-02: the decorative spinner is now IxSpinner rather than a hand-rolled `<span>`, so the
 * queries below target the rendered `ix-spinner` element rather than a `.spinner` class — the
 * component under test is the accessible contract, not the markup that happens to deliver it.
 */
describe("Loading", () => {
  it("exposes the message as a live status region", () => {
    render(<Loading text="Evaluating Phena…" />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Evaluating Phena…");
    expect(status).toHaveAttribute("aria-live", "polite");
  });

  it("hides the decorative spinner from assistive tech", () => {
    const { container } = render(<Loading text="Working…" />);
    expect(container.querySelector("ix-spinner")).toHaveAttribute("aria-hidden", "true");
    // The accessible name must be the message alone, with no spinner noise in it.
    expect(screen.getByRole("status").textContent.trim()).toBe("Working…");
  });
});

/**
 * ExtLink — what counts as a link.
 *
 * The regression: `core/data.py` stores a website exactly as the extraction returned it, and that
 * is routinely a bare host. ExtLink required `^https?://` and otherwise rendered NOTHING, so
 * Bliro's profile showed an empty Website row while `runs.db` held "bliro.io" the whole time.
 *
 * The opposite failure is the reason the fix has to stay narrow. `Fact.source_url` legitimately
 * carries labels rather than URLs — "GlassDollar" for a database fact, a filename for a pitch
 * deck — and a component that prefixes those manufactures a link to a host that does not exist.
 * Plain text is the honest rendering there, so both directions are pinned.
 */
describe("ExtLink", () => {
  it("links a stored bare domain, the case that rendered blank", () => {
    render(<ExtLink href="bliro.io" />);
    expect(screen.getByRole("link", { name: "bliro.io" })).toHaveAttribute("href", "https://bliro.io");
  });

  it("keeps a domain with a path intact", () => {
    render(<ExtLink href="bliro.io/en/pricing">site</ExtLink>);
    expect(screen.getByRole("link", { name: "site" }))
      .toHaveAttribute("href", "https://bliro.io/en/pricing");
  });

  it("leaves an absolute URL exactly as given", () => {
    render(<ExtLink href="https://www.linkedin.com/company/bliro/">LinkedIn</ExtLink>);
    expect(screen.getByRole("link", { name: "LinkedIn" }))
      .toHaveAttribute("href", "https://www.linkedin.com/company/bliro/");
  });

  it("renders a non-URL source label as text, never as a link", () => {
    render(<ExtLink href="GlassDollar">GlassDollar</ExtLink>);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("GlassDollar")).toBeInTheDocument();
  });

  it("does not turn a filename into a host", () => {
    render(<ExtLink href="phena_deck.pdf">deck</ExtLink>);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("refuses a scheme that is not http(s)", () => {
    // eslint-disable-next-line no-script-url
    render(<ExtLink href="javascript:alert(1)">click</ExtLink>);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("opens externally without leaking the referrer", () => {
    render(<ExtLink href="bliro.io" />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noreferrer");
  });

  it("renders nothing at all for an empty value", () => {
    const { container } = render(<ExtLink href="" />);
    expect(container).toBeEmptyDOMElement();
  });
});
