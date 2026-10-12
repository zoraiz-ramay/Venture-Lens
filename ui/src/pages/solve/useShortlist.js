import { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";

/* The startups a reviewer keeps from one search, saved with it on the server
   (PUT /api/solve/searches/{id}/shortlist) so a reload, another tab or next week finds them again.

   A search still running has no kept copy to save into, so picks made while it runs stay local and
   are saved the moment it is kept. Saves are sent one at a time, newest last: two in flight at once
   could land out of order and leave the server with the older list. */
export function useShortlist(searchId, kept) {
  const [ids, setIds] = useState([]);
  const [saveError, setSaveError] = useState("");
  const latest = useRef(ids);
  const queue = useRef({ want: null, busy: false });
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const set = (next) => { latest.current = next; setIds(next); };

  const save = (sid, next) => {
    const q = queue.current;
    q.want = { sid, ids: next };
    if (q.busy) return;
    q.busy = true;
    (async () => {
      while (q.want) {
        const { sid: target, ids: list } = q.want;
        q.want = null;
        try {
          await api.solveShortlist(target, list);
          if (alive.current) setSaveError("");
        } catch (e) {
          if (alive.current) setSaveError(e.message);
        }
      }
      q.busy = false;
    })();
  };

  useEffect(() => { set([]); setSaveError(""); }, [searchId]);

  // The kept copy's list wins when it has one; otherwise what was picked while the search ran is
  // saved into it.
  const keptId = kept?.id;
  useEffect(() => {
    if (!kept || kept.id !== searchId) return;
    if (kept.shortlist?.length) set(kept.shortlist);
    else if (latest.current.length) save(searchId, latest.current);
    // Only when a kept copy arrives for this search; its later edits come from this hook.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keptId, searchId]);

  const update = (next) => {
    set(next);
    if (kept && kept.id === searchId) save(searchId, next);
  };
  const toggle = (cid) => update(latest.current.includes(cid)
    ? latest.current.filter((x) => x !== cid) : [...latest.current, cid]);
  const clear = () => update([]);

  return { ids, toggle, clear, saved: !!kept && kept.id === searchId, saveError };
}
