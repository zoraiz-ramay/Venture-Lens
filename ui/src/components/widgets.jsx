import React from "react";
import { IxKeyValue, IxPill, IxSpinner } from "@siemens/ix-react";

/* Small display primitives shared across pages: the pillar pill, a label/value row (Spec), an
   external link that refuses non-URLs (ExtLink), a human duration and the announced loading line. */

// MIG-01: the four pillar colours themselves are unchanged (see tokens.css) — only the delivery
// mechanism moves, from a `.pill.<Pillar>` CSS class to IxPill's `variant="custom"` background/
// pillColor props, which is the only way to hand it a colour from JS rather than a class name.
const PILLAR_PILL_STYLE = {
  Connect: { background: "var(--pillar-connect-bg)", pillColor: "var(--pillar-connect)" },
  Collaborate: { background: "var(--pillar-collaborate-bg)", pillColor: "var(--pillar-collaborate)" },
  Empower: { background: "var(--pillar-empower-bg)", pillColor: "var(--pillar-empower)" },
  Pass: { background: "var(--pillar-pass-bg)", pillColor: "var(--pillar-pass)" },
};

export function PillarPill({ pillar, ghost = false, children, ...rest }) {
  const style = PILLAR_PILL_STYLE[pillar];
  // Absence renders the bare pillar name rather than a mis-coloured pill for an unknown value.
  if (!style) return <span {...rest}>{children ?? pillar}</span>;
  return (
    <IxPill variant="custom" background={style.background} pillColor={style.pillColor} outline={ghost} {...rest}>
      {children ?? pillar}
    </IxPill>
  );
}

// MIG-20: IxKeyValue's `value` prop only takes a string (components.md), but Spec's callers pass
// arbitrary JSX — links, chips, muted placeholders. Its compiled source (key-value.js) renders
// the `custom-value` slot whenever `value` is left undefined, which is the only way to hand it a
// React node rather than text, so `value` itself is never set here.
export function Spec({ k, children }) {
  return (
    <IxKeyValue label={k}>
      <div slot="custom-value" className="spec-value">{children || <span className="muted">—</span>}</div>
    </IxKeyValue>
  );
}

/* A stored value without a scheme is still a link.
   `core/data.py::web_profile_row` derives the website from a search result and can store a bare
   host ("bliro.io"). Every such profile rendered the Website row EMPTY — the value was there the
   whole time, and a strict `^https?://` test dropped it on the floor. Upgrading here is what
   rescues the runs already in the database; the engine normalises new ones at the source.

   The bar is deliberately narrow, because `source_url` legitimately carries non-URL labels:
   "GlassDollar" for a database fact, a filename for a pitch deck. Prefixing those would
   manufacture a link to a host that does not exist, which is worse than showing plain text. */
const _BARE_DOMAIN = /^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(\/\S*)?$/i;
const _FILE_EXT = /\.(pdf|docx?|xlsx?|pptx?|csv|txt|zip|png|jpe?g|gif|svg)$/i;

function toHref(value) {
  const s = String(value || "").trim();
  if (!s) return "";
  if (/^https?:\/\//i.test(s)) return s;
  // Any other scheme (mailto:, javascript:, data:) is never turned into an external link.
  if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return "";
  return _BARE_DOMAIN.test(s) && !_FILE_EXT.test(s) ? `https://${s}` : "";
}

/* A wall-clock duration for people, not a number of milliseconds: "0.2s", "48.3s", "1m 09s".
   null is "—" — an untimed row (logged before searches were timed) is unknown, not instant. */
export function duration(ms) {
  if (ms == null) return "—";
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const whole = Math.round(s);
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`;
}

export function ExtLink({ href, children }) {
  const url = toHref(href);
  if (!url) return children || null;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer">
      {children || String(href).replace(/^https?:\/\//i, "")}
    </a>
  );
}

export function Loading({ text }) {
  // role="status" + aria-live="polite" so a screen reader announces that work started and
  // finished. Evaluations run for tens of seconds; without this the page is silent the whole
  // time and a non-sighted reviewer cannot tell a slow run from a broken one. IxSpinner claims
  // role="status"/aria-busy="true" on its own host unconditionally (ix/spinner.js) regardless of
  // what's passed in, so leaving it unhidden would give this region two competing status
  // announcers. aria-hidden="true" is preserved through that (only role/aria-busy get
  // overwritten), which removes the whole element from the accessibility tree per spec — so the
  // spinner stays purely decorative and the outer <p> is the one and only thing a screen reader
  // hears.
  return (
    <p className="muted" role="status" aria-live="polite">
      <IxSpinner size="xx-small" aria-hidden="true" /> {text}
    </p>
  );
}
