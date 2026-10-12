import React, { useEffect, useState } from "react";
import { IxTabItem, IxTabs } from "@siemens/ix-react";
import { api } from "../../api.js";
import ErrorBox from "../../components/ErrorBox.jsx";

export const RUNS_TAB = "runs";

/* The Databases page's one switch: the evaluated runs (the grid this page always was, and still
   its default) plus every data source the server lists. The list comes from /api/sources so the
   tabs, their order and their labels are the server's (api/sources.py SOURCES), not a copy here.

   The evaluated-runs tab renders before the list arrives and stays if it fails: the grid must
   never depend on the source browser loading. A failure is said under the tabs. */
export default function DatabaseTabs({ value, onChange }) {
  const [sources, setSources] = useState([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => api.sources())
      .then((d) => live && setSources(d.sources || []))
      .catch((e) => live && setError(e.message || "Could not list the data sources."));
    return () => { live = false; };
  }, []);

  const active = value || RUNS_TAB;
  return (
    <div className="db-tabs">
      <IxTabs activeTabKey={active} ariaLabelMoreTabs="More data sources"
        onTabChange={(e) => {
          const key = e.detail;
          if (typeof key === "string" && key !== active) onChange(key === RUNS_TAB ? "" : key);
        }}>
        <IxTabItem tabKey={RUNS_TAB}>Evaluated startups</IxTabItem>
        {sources.map((s) => (
          <IxTabItem key={s.id} tabKey={s.id} counter={s.available ? s.rows : undefined}>
            {s.label}
          </IxTabItem>
        ))}
      </IxTabs>
      <ErrorBox message={error && `The data sources could not be listed: ${error}`} />
    </div>
  );
}
