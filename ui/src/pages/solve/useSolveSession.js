import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api.js";
import { useResearch } from "../../research.jsx";
import { useAuth } from "../../state.jsx";

/* One problem search as the page sees it: the background job (research.jsx polls it, so the
   interpretation and the applicants arrive as `partial` before the market is searched), the
   reviewer's draft, and the refinements made on top of the finished result.

   `?job=` names the search; `?new=1` or `?compose=1` asks for an empty brief. With neither, the
   latest search of this session is reopened — leaving the page and coming back should not lose
   a shortlist. */
export function useSolveSession() {
  const [params, setParams] = useSearchParams();
  const research = useResearch();
  const { user } = useAuth();
  const draftKey = `scouting-problem:${user?.oid || "local"}`;
  const fresh = params.has("new") || params.has("compose");
  const jobId = params.get("job");
  const job = fresh ? null
    : (research?.jobs || []).find((j) => (jobId ? j.id === jobId : j.kind === "solve")) || null;

  const [problem, setProblem] = useState(() => sessionStorage.getItem(draftKey) || "");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [refined, setRefined] = useState(null);
  const [refining, setRefining] = useState(false);
  const [refineError, setRefineError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const alive = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { sessionStorage.setItem(draftKey, problem); }, [problem, draftKey]);
  useEffect(() => { setRefined(null); setRefineError(""); }, [job?.id]);

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
    if (brief.length < 3 || inFlight.current) return;
    inFlight.current = true;
    setStarting(true); setStartError("");
    try {
      const [j] = await research.start({ problem: brief });
      if (alive.current) { setProblem(brief); setParams({ job: j.id }); }
    } catch (e) {
      if (alive.current) setStartError(e.message);
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

  // What to render: the finished (or refined) result, else whatever the job has reported so far.
  const result = refined || job?.result || null;
  const partial = job?.partial || {};
  const view = result
    ? { ...result, applicantsRanked: true, marketDone: true }
    : { interpretation: partial.interpretation || null, applicants: partial.applicants || null,
        retrieval: partial.retrieval || null,
        applicantsRanked: !!partial.applicants_ranked, market: null, marketDone: false };

  return {
    job, view, result, running, elapsed, problem, setProblem, start, starting, startError,
    refine, refining, refineError, ask, newProblem, focusInput: params.has("compose"),
  };
}
