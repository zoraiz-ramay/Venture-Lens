import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api.js";
import { useResearch } from "../../research.jsx";
import { useAuth } from "../../state.jsx";

const GONE = "This search is no longer available. It may have expired before it finished, or been removed.";

/* One problem search as the page sees it: the background job (research.jsx polls it, so the
   interpretation and the applicants arrive as `partial` before the market is searched), the copy
   the server keeps once it has finished, the reviewer's draft, and the refinements made on top.

   `?job=` names the search; `?new=1` or `?compose=1` asks for an empty brief. With neither, the
   latest search of this session is reopened — leaving the page and coming back should not lose
   a shortlist. A search from another tab or another day is opened from the kept copy
   (POST /api/solve/searches/{id}), which is also where its refinement, shortlist and follow-up
   thread live: the job behind it expires after a day, and the tab's list of jobs with the tab. */
export function useSolveSession() {
  const [params, setParams] = useSearchParams();
  const research = useResearch();
  const { user } = useAuth();
  const draftKey = `scouting-problem:${user?.oid || "local"}`;
  const fresh = params.has("new") || params.has("compose");
  const jobId = params.get("job");
  const sessionJob = fresh ? null
    : (research?.jobs || []).find((j) => (jobId ? j.id === jobId : j.kind === "solve")) || null;
  const id = fresh ? null : jobId || sessionJob?.id || null;

  const [problem, setProblem] = useState(() => sessionStorage.getItem(draftKey) || "");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [kept, setKept] = useState(null);
  const [openError, setOpenError] = useState(null);
  const [refined, setRefined] = useState(null);
  const [refining, setRefining] = useState(false);
  const [refineError, setRefineError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const alive = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { sessionStorage.setItem(draftKey, problem); }, [problem, draftKey]);
  useEffect(() => { setRefined(null); setRefineError(""); }, [id]);

  // Open the kept copy once there is nothing left to wait for: a job still running has nothing to
  // keep, and a session job whose result has not been fetched back yet will have one shortly.
  const settled = !sessionJob || sessionJob.status === "error" || (sessionJob.status === "complete" && !!sessionJob.result);
  useEffect(() => {
    if (!id || !settled) return undefined;
    let live = true;
    api.solveKeep(id).then((r) => {
      // Anything without a result is not a kept search (a stub server answers `{}`).
      if (live) { setKept(r?.result ? { ...r, id } : null); setOpenError(null); }
    }).catch((e) => {
      if (live) { setKept(null); setOpenError({ id, message: e.status === 404 ? GONE : e.message }); }
    });
    return () => { live = false; };
  }, [id, settled]);

  const here = kept?.id === id ? kept : null;
  // The session's job while it is the better record; the kept copy once the job is gone or failed.
  const job = here && (!sessionJob || sessionJob.status === "error")
    ? { id, kind: "solve", query: here.problem, status: "complete", result: here.result }
    : sessionJob;
  const opening = !!id && !job && !(openError?.id === id);

  const running = !!job && ["queued", "running"].includes(job.status);
  useEffect(() => {
    if (!running) return undefined;
    const started = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [running, job?.id]);

  const start = async (text) => {
    const brief = String(text ?? problem).trim();
    if (brief.length < 3 || inFlight.current) return null;
    inFlight.current = true;
    setStarting(true); setStartError("");
    try {
      const [j] = await research.start({ problem: brief });
      if (alive.current) { setProblem(brief); setParams({ job: j.id }); }
      return j.id;
    } catch (e) {
      if (alive.current) setStartError(e.message);
      return null;
    } finally {
      inFlight.current = false;
      if (alive.current) setStarting(false);
    }
  };

  const refine = async (answers, note = "") => {
    if (!job || job.status !== "complete") return;
    setRefining(true); setRefineError("");
    try {
      const r = await api.solveRefine(job.id, answers, note);
      if (alive.current) setRefined(r);
    } catch (e) {
      if (alive.current) setRefineError(e.message);
    } finally {
      if (alive.current) setRefining(false);
    }
  };

  const ask = (question, history, ids) => api.solveAsk(job.id, question, history, ids);
  const newProblem = () => setParams({ new: "1" });

  // What to render: the refined result, else the kept copy (it carries a refinement made in an
  // earlier visit), else the finished job, else whatever the job has reported so far.
  const result = refined || here?.result || job?.result || null;
  const partial = job?.partial || {};
  const view = result
    ? { ...result, applicantsRanked: true, marketDone: true }
    : { interpretation: partial.interpretation || null, applicants: partial.applicants || null,
        retrieval: partial.retrieval || null,
        applicantsRanked: !!partial.applicants_ranked, market: null, marketDone: false };

  return {
    id, job, kept: here, view, result, elapsed, problem, setProblem, start, starting, startError,
    opening, openError: openError?.id === id && !job ? openError.message : "",
    refine, refining, refineError, ask, newProblem, focusInput: params.has("compose"),
  };
}
