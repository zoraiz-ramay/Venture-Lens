import React, { useState } from "react";
import { IxButton } from "@siemens/ix-react";
import { Answer } from "../../components/AssistantDock.jsx";
import { Loading } from "../../components/widgets.jsx";

const SUGGESTIONS = [
  "Which of these already sell to manufacturers?",
  "Which are furthest along?",
  "What would be the risks of piloting these?",
];
const HISTORY_TURNS = 8;

/* Follow-up questions about what the search found. Answered from the shortlist first — its
   descriptions, application answers and the reasons each was ranked, every one with its source —
   then the reviewer's Tracxn, then the model's web search (POST /api/solve/{job}/ask). With
   startups selected, the question is about those; otherwise about everything listed. */
export default function ShortlistAsk({ ask, selectedNames }) {
  const [turns, setTurns] = useState([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const scope = selectedNames.length
    ? `About the ${selectedNames.length} you selected: ${selectedNames.join(", ")}`
    : "About every startup listed above. Select some to narrow the question.";

  const send = async (text) => {
    const question = String(text ?? q).trim();
    if (question.length < 2 || busy) return;
    const history = turns.filter((t) => t.role === "user" || t.provider !== "none")
      .slice(-HISTORY_TURNS).map((t) => ({ role: t.role, text: t.text }));
    setQ(""); setBusy(true);
    setTurns((t) => [...t, { role: "user", text: question }]);
    try {
      const r = await ask(question, history);
      setTurns((t) => [...t, { role: "assistant", text: r.answer, source: r.source, provider: r.provider,
        note: r.note, evidence: r.evidence }]);
    } catch (e) {
      setTurns((t) => [...t, { role: "assistant", text: `The question could not be answered: ${e.message}`, provider: "none" }]);
    } finally {
      setBusy(false);
    }
  };

  const asked = new Set(turns.filter((t) => t.role === "user").map((t) => t.text));
  return (
    <section className="solve-ask" aria-labelledby="solve-ask-title">
      <h2 id="solve-ask-title">Ask about these startups</h2>
      <p className="muted">{scope}</p>
      {turns.length > 0 && (
        <div className="solve-ask-thread" aria-live="polite">
          {turns.map((t, i) => (t.role === "user"
            ? <div key={i} className="dock-msg user">{t.text}</div>
            : <Answer key={i} m={t} />))}
        </div>
      )}
      {busy && <div role="status"><Loading text="Looking into it…" /></div>}
      <div className="solve-examples" role="group" aria-label="Suggested questions">
        {SUGGESTIONS.filter((s) => !asked.has(s)).map((s) => (
          <IxButton key={s} variant="secondary" disabled={busy} onClick={() => send(s)}>{s}</IxButton>
        ))}
      </div>
      <form className="solve-ask-form" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} maxLength={2000} disabled={busy}
          aria-label="Ask a question about these startups" placeholder="e.g. Which of them integrate with PLCs?" />
        <IxButton disabled={busy || q.trim().length < 2} onClick={() => send()}>Ask</IxButton>
      </form>
    </section>
  );
}
