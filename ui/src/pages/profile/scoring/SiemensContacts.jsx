import React from "react";
import { iconMail } from "@siemens/ix-icons/icons";
import Icon from "../../../components/Icon.jsx";
import { Lookup } from "./TractionLookup.jsx";

/* "Relevant Siemens Contact" under Empower → Tool fit: people the Siemens Directory lists in each
   cited tool's department (core/siemens_contacts.py). The department is shown as the catalogue
   gives it; a contact is ranked by the app from department match, which shows organisational
   relevance, not that the person owns the tool — the lookup's note says so above the list.

   Three tools at most, two people each (config.SIEMENS_CONTACT_TOOLS / SIEMENS_CONTACTS_PER_TOOL):
   one card per tool, side by side, each person with the email a reviewer would write to. */
const initials = (name) => String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

function ToolContacts({ row }) {
  return (
    <li className="contact-tool">
      <div className="contact-tool-head">
        <strong className="contact-tool-name">{row.tool}</strong>
        <span className="kind-tag">{row.department || "Department not available"}</span>
      </div>
      {row.translated && <span className="muted contact-note">Searched as {row.department_query}: the app's reading of the department.</span>}
      {row.contacts.length > 0
        ? <ul className="contact-list">
          {row.contacts.map((p) => (
            <li key={p.email || p.name} className="contact-person">
              <span className="contact-avatar" aria-hidden="true">{initials(p.name)}</span>
              <span className="contact-who">
                <strong>{p.name || "Name not available"}</strong>
                <span className="muted">{p.department || "Department not available"}</span>
                {p.email && <a className="contact-mail" href={`mailto:${p.email}`}><Icon icon={iconMail} size={14} />{p.email}</a>}
              </span>
            </li>
          ))}
        </ul>
        : <span className="muted contact-note">{row.note || "No contact found."}</span>}
    </li>
  );
}

export default function SiemensContacts({ runId }) {
  return (
    <Lookup runId={runId} kind="contacts" title="Relevant Siemens Contact" loadingText="Looking up Siemens contacts…"
      className="fd contacts">
      {(data) => (data.tools?.length
        ? <ul className="contact-tools">{data.tools.map((row) => <ToolContacts key={row.tool} row={row} />)}</ul>
        : !data.note && <p className="muted">No contact to show.</p>)}
    </Lookup>
  );
}
