import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IxIconButton } from "@siemens/ix-react";
import { iconTrashcan } from "@siemens/ix-icons/icons";
import { api } from "../../api.js";
import { Loading } from "../../components/widgets.jsx";
import ErrorBox from "../../components/ErrorBox.jsx";
import { clip } from "./labels.js";

const when = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
};

function counts(s) {
  return [s.applicants ? `${s.applicants} applied` : "", s.market ? `${s.market} from the market` : "",
    s.shortlisted ? `${s.shortlisted} shortlisted` : "", s.refined ? "sharpened" : ""].filter(Boolean).join(" · ");
}

/* The problems this reviewer searched before, newest worked-on first, each opening as they left it
   — refined, with its shortlist and follow-up questions (GET /api/solve/searches). Private to the
   reviewer: nobody else's searches are listed, and none of these is shown to anyone else. */
export default function RecentSearches() {
  const [searches, setSearches] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api.solveSearches()
      .then((r) => { if (live) setSearches(Array.isArray(r?.searches) ? r.searches : []); })
      .catch((e) => { if (live) { setError(e.message); setSearches([]); } });
    return () => { live = false; };
  }, []);

  const forget = async (id) => {
    try {
      await api.solveForget(id);
      setSearches((list) => list.filter((s) => s.id !== id));
    } catch (e) { setError(e.message); }
  };

  return (
    <section className="solve-recent" aria-labelledby="solve-recent-title">
      <h2 id="solve-recent-title">Your recent problems</h2>
      {searches === null && <Loading text="Loading your searches…" />}
      {error && <ErrorBox message={error} hint="Your earlier searches are still kept." />}
      {searches?.length === 0 && !error && (
        <p className="muted">Each problem you search is kept here with the startups you shortlist, so you can pick it
          up again later.</p>
      )}
      {searches?.length > 0 && (
        <ul className="solve-recent-list">
          {searches.map((s) => (
            <li key={s.id}>
              <Link to={`/workspace?job=${encodeURIComponent(s.id)}`} className="solve-recent-link" title={s.problem}>
                {clip(s.problem, 140)}</Link>
              <span className="muted solve-recent-meta">{[when(s.updated_at), counts(s)].filter(Boolean).join(" · ")}</span>
              <IxIconButton icon={iconTrashcan} variant="subtle-tertiary" size="16" aria-label={`Forget “${clip(s.problem, 60)}”`}
                onClick={() => forget(s.id)} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
