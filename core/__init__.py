"""
Core engine for the Siemens Startup Evaluation Agent. Pure Python; never imports `api/`.

Pipeline: `pipeline.evaluate` → enrich → (verify ‖ summarize ‖ fit ‖ profile ‖ trend, concurrent)
→ score → route. Where each step lives:

    pipeline        evaluate() orchestration, streaming partials, re-assessing stored research
    enrich, web     evidence gathering (web search, site fetch) as provenance Facts
    data, glassdollar_api, tracxn
                    resolving the company: GlassDollar / Tracxn first, a web profile otherwise
    verify          self-reported claims checked against the web evidence
    summarize       plain-language offering summary
    fit             Siemens tool matching (tool_search: semantic + word shortlists)
    profile         the researched deep profile (founders, programmes, commercial posture)
    trend           market momentum and landscape (market: the Market criteria)
    carry_forward   folding earlier runs of the same company into a refresh
    judgment        the model's cited scores; traction: the deterministic traction rubric
    pillar_match, pillars, catalogs
                    Siemens Fit: Empower / Collaborate / Connect per department
    team_ecosystem  the Team & Ecosystem criteria
    assessment      per-department packaging and the weighted total, re-derived on read
    programs        published programme criteria and SFS; route / score / empower / departments:
                    the older scorecard router, kept for benchmarks/replay.py and the UI mirror
    chat            the assistant; solve: Solve a Problem
    config, text, llm, provenance
                    settings, text helpers, the model client, the Fact dataclass

This package re-exports only what callers reach as `core.<name>` (api/main.py, api/routes_solve.py,
scripts/prewarm.py). Importing it also loads `.env` through `core.config`.
"""
from __future__ import annotations

from .config import BASE_DIR, DEFAULT_GLASSDOLLAR, DEFAULT_TOOLS_CSV, GLASSDOLLAR_API_KEY
from .llm import LLMClient
from .data import search_glassdollar
from .chat import chat_assistant
from .pipeline import evaluate

__all__ = [
    "BASE_DIR", "DEFAULT_GLASSDOLLAR", "DEFAULT_TOOLS_CSV", "GLASSDOLLAR_API_KEY",
    "LLMClient", "search_glassdollar", "chat_assistant", "evaluate",
]
