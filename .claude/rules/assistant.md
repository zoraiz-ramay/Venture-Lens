---
paths:
  - "core/chat.py"
  - "core/llm.py"
  - "core/tracxn.py"
  - "api/tracxn.py"
  - "ui/src/components/AssistantDock.jsx"
  - "ui/src/components/AnswerText.jsx"
  - "ui/src/components/TracxnConnection.jsx"
  - "tests/test_assistant.py"
---

## The assistant: the evaluation first, Tracxn second, the model's own web search third

`POST /api/ask` → `core/chat.py::chat_assistant`. With a run in focus, `run_brief` turns what the
evaluation already researched into ≤ ~700 tokens of numbered facts (`R1…`), each with its source —
profile, scores, pillars, market, competitors, claim checks, then founders/investors/programmes —
and one **non-thinking** call answers from them, citing `[R#]` (renumbered to `[1]…`, the same
scheme as a web answer, so the dock links both alike). Measured on Radical Dot's four suggestion
chips, two were answered there with no search at all. Python, not the prompt, decides what falls
through: an answer citing no fact, or one that says `Not covered:`, goes to search, and a
`SEARCH: <query>` reply hands the search a sharper query (always naming the company — without
it "top risks" came back as generic industry risks). The search then gets only the headline
facts, not the whole brief, and a capped thinking budget (`CHAT_SEARCH_THINKING`, 512): a
grounded Gemini answer spent up to ~3,900 thinking tokens otherwise. History is the last four
turns, assistant turns cut to 400 chars. Answers follow one shape (`_CHAT_STYLES`): a sentence
that answers, cited bullets, a table only for comparisons, a `Not covered:` line — rendered by
`ui/src/components/AnswerText.jsx`, never as HTML. A grounded answer the model writes twice is
cut to its first copy (`llm._drop_repeat`).

With the reviewer's Tracxn account connected,
`TracxnClient.research` plans up to three calls over the MCP server's **read-only** tools (schemas
discovered, arguments validated against them, anything named for a write refused) and the answer
is written from that data only. Without a connection — or when Tracxn fails or has nothing — the
model searches the web itself (`LLMClient.web_answer`: Google Search grounding on Gemini, the
Responses API `web_search` tool on the gateway) and cites what it used. **No DuckDuckGo here**:
`tests/test_assistant.py` fails if the path touches it. A provider that cannot search answers from
memory labelled *unverified* — never under a web label. The gateway's `web_search` support cannot
be checked from outside the Siemens network; if it is missing, answers degrade to that label.
