import React, { Fragment, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { IxEmptyState, IxInput, IxKeyValue, IxKeyValueList, IxPagination } from "@siemens/ix-react";
import { iconChevronDownSmall, iconChevronRightSmall } from "@siemens/ix-icons/icons";
import { api } from "../../api.js";
import ErrorBox from "../../components/ErrorBox.jsx";
import Icon from "../../components/Icon.jsx";

export const PAGE_SIZE = 25;
const SEARCH_DELAY_MS = 300;

/* One data source, read-only: what it is, its file's identity, and its rows a page at a time.

   Everything a reviewer can do here is in the URL (`?source=&search=&page=`), so a filtered page
   of the tools catalogue is a link that can be shared. The server owns the search and the paging
   (api/sources.py) — and decides which columns exist at all: this component renders whatever
   columns the response names and has no list of its own to drift from that allow-list. */
export default function SourceBrowser({ sourceId }) {
  const [params, setParams] = useSearchParams();
  const search = params.get("search") || "";
  const page = Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1);
  const [draft, setDraft] = useState(search);
  const [state, setState] = useState({ status: "loading", data: null, error: "" });
  const [open, setOpen] = useState(null);

  const update = (changes) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, String(v)); else next.delete(k); }
    return next;
  }, { replace: true });

  useEffect(() => { setDraft(search); }, [search]);          // back/forward, a shared link
  // The server filters, so a request per keystroke would be wasted; a pause commits the words.
  useEffect(() => {
    if (draft === search) return undefined;
    const t = setTimeout(() => update({ search: draft.trim() ? draft : "", page: "" }), SEARCH_DELAY_MS);
    return () => clearTimeout(t);
  }, [draft, search]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let live = true;
    // A new page of the same source keeps the old rows on screen until it lands; another source
    // must not show this one's rows under its name.
    setState((s) => ({ status: "loading", data: s.data?.id === sourceId ? s.data : null, error: "" }));
    setOpen(null);
    api.source(sourceId, { q: search, offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE })
      .then((data) => live && setState({ status: "ready", data, error: "" }))
      .catch((e) => live && setState({ status: "error", data: null, error: e.message || "Request failed." }));
    return () => { live = false; };
  }, [sourceId, search, page]);

  const { status, data, error } = state;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 0;
  const columns = data?.columns || [];

  return (
    <div className="source-browser">
      {data && <SourceFacts source={data} />}
      <div className="filter-row">
        <IxInput className="source-search" label="Search this source" value={draft}
          placeholder="Every word must appear in the row"
          onValueChange={(e) => setDraft(e.detail ?? e.target?.value ?? "")} />
        {data && (
          <span className="muted source-count" aria-live="polite">
            {data.total === data.rows ? `${data.total} rows` : `${data.total} of ${data.rows} rows match`}
          </span>
        )}
      </div>
      <ErrorBox message={error} hint="is the API running?" />
      <div className="grid-shell source-grid" aria-busy={status === "loading"}>
        {status === "loading" && !data && (
          <div className="source-loading" aria-label="Loading rows">
            {Array.from({ length: 8 }).map((_, i) => <div key={i} className="skel skel-row" />)}
          </div>
        )}
        {data && !data.available && (
          <IxEmptyState layout="compact" header="This source is not available"
            subHeader={data.reason || "Its file could not be read."} />
        )}
        {data && data.available && data.items.length === 0 && (
          <IxEmptyState layout="compact"
            header={data.total > 0 ? "No rows on this page" : search ? "No rows match" : "This source has no rows"}
            subHeader={data.total > 0 ? `The last page is ${pages}.`
              : search ? "Every word of the search must appear in a row. Try fewer words." : ""} />
        )}
        {data && data.items.length > 0 && (
          <table className="dtable source-table">
            <thead>
              <tr>
                <th className="source-toggle-col"><span className="sr-only">Details</span></th>
                {columns.map((c) => <th key={c.key} scope="col">{c.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {data.items.map((row, i) => {
                const title = row[columns[0]?.key] || `Row ${data.offset + i + 1}`;
                const expanded = open === i;
                return (
                  <Fragment key={`${data.offset}-${i}`}>
                    <tr className={expanded ? "selected" : ""}>
                      <td className="source-toggle-col">
                        <button type="button" className="kebab" aria-expanded={expanded}
                          aria-label={`${expanded ? "Hide" : "Show"} the full row for ${title}`}
                          onClick={() => setOpen(expanded ? null : i)}>
                          <Icon icon={expanded ? iconChevronDownSmall : iconChevronRightSmall} />
                        </button>
                      </td>
                      {columns.map((c) => <td key={c.key}><Cell value={row[c.key]} /></td>)}
                    </tr>
                    {expanded && (
                      <tr className="source-detail">
                        <td colSpan={columns.length + 1}>
                          <IxKeyValueList striped>
                            {columns.map((c) => (
                              <IxKeyValue key={c.key} label={c.label} labelPosition="left">
                                <span slot="custom-value" className="source-full"><Cell value={row[c.key]} full /></span>
                              </IxKeyValue>
                            ))}
                          </IxKeyValueList>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {pages > 1 && (
        <IxPagination className="source-pages" count={pages} selectedPage={Math.min(page, pages) - 1}
          ariaLabelPageSelection="Page of rows"
          onPageSelected={(e) => update({ page: e.detail > 0 ? e.detail + 1 : "" })} />
      )}
    </div>
  );
}

/* A cell clamps to a few lines in the grid — pitches run to pages — and is shown whole in the
   row's details. A web address is a link, opened away from the app. */
function Cell({ value, full = false }) {
  const text = value || "";
  if (!text) return <span className="muted">—</span>;
  if (/^https?:\/\/\S+$/.test(text)) {
    return <a href={text} target="_blank" rel="noopener noreferrer" className="source-link">{text}</a>;
  }
  return <span className={full ? "source-text-full" : "source-text"}>{text}</span>;
}

function SourceFacts({ source }) {
  const modified = source.modified ? new Date(source.modified).toLocaleString() : "—";
  const facts = [
    ["What it is", source.about],
    ["Feeds", source.feeds],
    ...(source.note ? [["In use", source.note]] : []),
    ["File", source.file || "—"],
    ["Rows", source.available ? String(source.rows) : "—"],
    ["Last modified", modified],
    ["Checksum (SHA-256)", source.checksum || "Not recorded for this source"],
  ];
  return (
    <section className="panel source-facts" aria-label={`About ${source.label}`}>
      <h3>{source.label}</h3>
      <IxKeyValueList>
        {facts.map(([k, v]) => (
          <IxKeyValue key={k} label={k} labelPosition="left">
            <span slot="custom-value" className={k.startsWith("Checksum") ? "source-checksum" : undefined}>{v}</span>
          </IxKeyValue>
        ))}
      </IxKeyValueList>
    </section>
  );
}
