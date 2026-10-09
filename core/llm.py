"""OpenAI-compatible LLM client.

Primary provider is the Siemens LLM gateway at llm.sdc.siemens.cloud, authenticated via
OPENAI_API_KEY (sent as an 'x-api-key' header). If that key isn't set, falls back to
Gemini's OpenAI-compatible endpoint using GEMINI_API_KEY — handy for local dev without
gateway access.
"""
from __future__ import annotations

import contextvars
import os
import threading
import random
import re
import json
import time
import logging
from typing import Optional

from . import web as _web
from .config import LLM_MODEL, LLM_TIMEOUT

log = logging.getLogger(__name__)

LLM_BASE_URL = (os.getenv("LLM_BASE_URL") or "https://llm.sdc.siemens.cloud/v1").strip()
GEMINI_BASE_URL = (os.getenv("GEMINI_BASE_URL")
                   or "https://generativelanguage.googleapis.com/v1beta/openai/").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
# A ceiling, not a charge: the model stops when its answer is done. At 1024 the gateway's gpt-5.4
# stopped mid-JSON (finish_reason=length) on the profile extraction — founders, team, advisors,
# programs and customers, each with a source URL — and the profile fell back to keyword-only.
LLM_MIN_BUDGET = int(os.getenv("LLM_MIN_BUDGET", "4096"))
# A reply that still hits its ceiling is re-asked at a larger one, up to this.
LLM_MAX_BUDGET = int(os.getenv("LLM_MAX_BUDGET", "16384"))
# Gemini 2.5 is a THINKING model: its reasoning tokens are billed against
# max_completion_tokens, before a single character of the answer is emitted. A budget sized
# for the answer alone therefore gets consumed by thinking and the reply comes back truncated
# mid-token — which parse_json rejects, so the caller silently falls back to keyword-only
# extraction. That failure is invisible (the request itself succeeds, so last_error stays
# empty) and it degraded EVERY profile to method='offline_keyword': no founders, no headcount,
# no founding year. Reasoning models get this headroom added on top of the requested budget.
LLM_THINKING_HEADROOM = int(os.getenv("LLM_THINKING_HEADROOM", "6144"))
_THINKING_PROVIDERS = ("gemini",)
# The gateway's gpt-5/gpt-6 models also bill reasoning against max_completion_tokens, so they get
# the headroom and `reasoning_effort` too — decided by model name, so the same code is right on the
# gateway in production and on Gemini locally. A gateway that rejects the parameter costs one
# retry, then complete() drops it (_unsupported_param).
_REASONING_MODEL = re.compile(r"^(gpt-[56]|o\d|(google/)?gemini-)", re.I)
_GPT_REASONING = re.compile(r"^(gpt-[56]|o\d)", re.I)
# Gemini 3 and later cannot switch thinking off: reasoning_effort "none" and a thinking budget of 0
# are both rejected with a bare 400 that names no parameter, so _unsupported_param cannot drop them
# and every evidence-to-JSON call failed outright. "minimal" is what "off" means on those models.
_THINKING_ALWAYS_ON = re.compile(r"(^|/)gemini-([3-9]|\d{2})", re.I)
# Effort for calls that do not ask for one (none|low|medium|high); empty keeps the model default.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()


def _reasons(provider: str, model: str) -> bool:
    return provider in _THINKING_PROVIDERS or (provider == "openai" and bool(_REASONING_MODEL.match(model or "")))


def _thinking_floor(model: str) -> bool:
    return bool(_THINKING_ALWAYS_ON.search(model or ""))
# Deterministic by default. Unset, Gemini samples at 1.0 and four identical extraction calls
# returned three different JSON spellings, so re-evaluating a startup never reproduced exactly.
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
# Cache completions alongside the web results (same store, same TTL, same refresh bypass).
# Safe only because LLM_TEMPERATURE is 0 — see complete().
LLM_CACHE = os.getenv("LLM_CACHE", "1") != "0"
MAX_RETRIES = 3
# A completion that searches the web runs several searches before it answers; the plain LLM
# timeout cut the longer ones off mid-search.
WEB_SEARCH_TIMEOUT = max(LLM_TIMEOUT, int(os.getenv("WEB_SEARCH_TIMEOUT", "90")))
RETRY_BACKOFF = 2
# A rate-limited call waits for the quota window instead of retrying at 2s and 4s: against a
# per-minute quota those retries fail too, and the caller's offline fallback then hands the
# reviewer a weaker result with no sign anything went wrong.
RATE_LIMIT_MAX_WAIT = float(os.getenv("RATE_LIMIT_MAX_WAIT", "60"))
# Embeddings get one attempt more than completions. Gemini's embedding 429 carries no retry hint, so
# the waits are the plain backoff (~4s, 8s, 16s): three attempts gave up after ~13s, inside the
# per-minute window. The wait overlaps the fit stage, which runs far longer, so it costs little.
EMBED_RETRIES = int(os.getenv("EMBED_RETRIES", "4"))
# Vector size for semantic tool search (core/tool_search.py). gemini-embedding-001 is trained so a
# shorter prefix keeps its meaning; 768 keeps the stored catalog index at ~4.5 MB.
EMBEDDING_DIMS = int(os.getenv("EMBEDDING_DIMS", "768"))
_RETRY_DELAY = re.compile(r"""(?:retryDelay["']?\s*[:=]\s*["']?|retry in\s+)(\d+(?:\.\d+)?)s""", re.I)

# Optional shared throttle, called before every uncached request. Injected by api/main.py (Redis,
# so both gunicorn workers share one budget) the same way the web cache is: core/ never imports
# api/, and scripts and tests run unthrottled.
_rate_gate = None


def install_rate_limiter(gate) -> None:
    global _rate_gate
    _rate_gate = gate


# Which pipeline stage is calling, and where to record a call that failed for good. Set by the
# pipeline; a ContextVar so it follows each branch into its worker thread (copy_context). The list
# is shared by reference across those copies, which is what lets every branch report into one run.
_stage: contextvars.ContextVar = contextvars.ContextVar("llm_stage", default="")
_failures: contextvars.ContextVar = contextvars.ContextVar("llm_failures", default=None)


def set_stage(stage: str):
    return _stage.set(stage)


def reset_stage(token) -> None:
    _stage.reset(token)


def collect_failures():
    """Start recording failed calls for one run. Returns (token, list)."""
    failures: list = []
    return _failures.set(failures), failures


def stop_collecting(token) -> None:
    _failures.reset(token)


# Every model call of one evaluation, for the token log the admin dashboard shows per run. Same
# mechanism as _failures: a list shared by reference into each branch's copied context.
_usage: contextvars.ContextVar = contextvars.ContextVar("llm_usage", default=None)
# A web search's token counts, handed from the provider helper to web_answer on the same thread.
_tls = threading.local()


def collect_usage():
    """Start recording model calls for one run. Returns (token, list)."""
    calls: list = []
    return _usage.set(calls), calls


def stop_collecting_usage(token) -> None:
    _usage.reset(token)


def _num(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _record_call(*, kind: str, model: str, provider: str, started: float, cached: bool = False,
                 ok: bool = True, reason: str = "", attempts: int = 1, input_tokens=0, output_tokens=0,
                 reasoning_tokens=0, total_tokens=0, hedged: bool = False) -> None:
    calls = _usage.get()
    if calls is None:
        return
    inp, out, think = _num(input_tokens), _num(output_tokens), _num(reasoning_tokens)
    total = _num(total_tokens) or inp + out + think
    # Gemini reports thinking only inside total_tokens; what total has beyond input + output is it.
    think = think or max(0, total - inp - out)
    calls.append({"stage": _stage.get() or "input", "kind": kind, "model": model, "provider": provider,
                  "cached": cached, "ok": ok, "reason": reason, "attempts": attempts,
                  "input_tokens": inp, "output_tokens": out, "reasoning_tokens": think,
                  "total_tokens": max(total, inp + out + think),
                  "duration_ms": int((time.time() - started) * 1000),
                  # When it started, as an offset into the run: durations alone cannot show which
                  # calls ran one after another, and that chain is what a slow run is made of.
                  **({"start_ms": offset} if (offset := _web.run_offset_ms(started)) is not None else {}),
                  **({"hedged": True} if hedged else {})})


def _usage_counts(resp) -> dict:
    usage = getattr(resp, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    return {"input_tokens": getattr(usage, "prompt_tokens", 0),
            "output_tokens": getattr(usage, "completion_tokens", 0),
            "reasoning_tokens": getattr(details, "reasoning_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0)}


# A thinking call's time varies a lot from one request to the next — fit's match call measured
# 25s typically and over 90s under load — and a slow request does not get faster by waiting on
# it. Retrying after the timeout threw away everything it had waited for; a duplicate sent at
# `hedge_after` instead races the original, and the run gets whichever answers first. Costs one
# extra request on the slow tail only, and the reply is the same contract as a retry's:
# temperature 0, the same request, and the cache keeps whichever won.
def _hedged(send, hedge_after: float, record_loser) -> tuple:
    """(response, hedged?) — ``send()`` once, again after ``hedge_after``s; the first success wins.

    Raises the last error when every request sent failed, exactly like a single ``send()``. A
    request that loses the race is not cancelled (the client cannot), so ``record_loser(response)``
    logs its tokens when it lands: it was billed, and the token log is what a run cost."""
    import concurrent.futures as cf
    # Not a `with` block: leaving one waits for the loser, which is the wait this exists to skip.
    pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-hedge")
    try:
        first = pool.submit(contextvars.copy_context().run, send)
        try:
            return first.result(timeout=hedge_after), False
        except cf.TimeoutError:
            if first.done():       # the request itself timed out: that is the retry path's to handle
                raise
        second = pool.submit(contextvars.copy_context().run, send)
        error = None
        for fut in cf.as_completed((first, second)):
            try:
                resp = fut.result()
            except Exception as e:                       # noqa: BLE001 — the other may still answer
                error = e
                continue
            other = second if fut is first else first
            ctx = contextvars.copy_context()
            other.add_done_callback(lambda f: f.exception() is None and ctx.run(record_loser, f.result()))
            return resp, fut is second
        raise error
    finally:
        pool.shutdown(wait=False)


def summarize_usage(calls: list) -> dict:
    """Totals, a per-stage breakdown and the call log, as stored on the run."""
    keys = ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
    totals = {k: sum(c[k] for c in calls) for k in keys}
    stages: dict = {}
    for c in calls:
        row = stages.setdefault(c["stage"], {"stage": c["stage"], "calls": 0, "cached_calls": 0,
                                             "failed_calls": 0, **{k: 0 for k in keys}})
        row["calls"] += 1
        row["cached_calls"] += c["cached"]
        row["failed_calls"] += not c["ok"]
        for k in keys:
            row[k] += c[k]
    return {"calls": len(calls), "cached_calls": sum(c["cached"] for c in calls),
            "failed_calls": sum(not c["ok"] for c in calls), **totals,
            "models": sorted({c["model"] for c in calls if c["model"]}),
            "by_stage": sorted(stages.values(), key=lambda r: -r["total_tokens"]),
            "log": calls}


def summarize_failures(failures: list) -> list:
    """[{stage, reason, calls}] — one row per stage and reason, in first-seen order."""
    rows: dict = {}
    for f in failures:
        row = rows.setdefault((f["stage"], f["reason"]), {**f, "calls": 0})
        row["calls"] += 1
    return list(rows.values())


def _rate_limit_wait(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying a rate-limited call, or None if this is not a rate limit."""
    msg = str(exc)
    rate_limited = (getattr(exc, "status_code", None) == 429 or "RESOURCE_EXHAUSTED" in msg
                    or re.search(r"\b429\b", msg) is not None)
    if not rate_limited:
        return None
    hinted = None
    response = getattr(exc, "response", None)
    try:
        hinted = float(response.headers.get("retry-after")) if response is not None else None
    except (TypeError, ValueError):
        hinted = None
    if hinted is None:
        m = _RETRY_DELAY.search(msg + " " + json.dumps(getattr(exc, "body", None), default=str))
        hinted = float(m.group(1)) if m else None
    wait = hinted if hinted is not None else RETRY_BACKOFF * (2 ** attempt)
    return min(RATE_LIMIT_MAX_WAIT, wait) + random.uniform(0, 1.0)


# A spending cap or a daily/monthly quota is a 429 too, but no wait inside a run ends it: retrying
# only spends the run's time (three attempts were ~13s per call, on every call of a run) before
# failing the same way. Per-minute limits do clear, and are waited out.
_QUOTA_EXHAUSTED = re.compile(r"spending cap|per[ _-]?day|perday|daily|monthly|billing|quota exceeded for quota metric.*day",
                              re.I)
# How many attempts a per-minute rate limit gets: enough for the waits (4, 8, 16, 32 s, or the
# provider's own hint) to span a whole quota window, so a burst queues instead of failing a part.
RATE_LIMIT_ATTEMPTS = int(os.getenv("RATE_LIMIT_ATTEMPTS", "5"))


def _quota_exhausted(exc: Exception) -> bool:
    msg = str(exc) + " " + json.dumps(getattr(exc, "body", None), default=str)
    return _rate_limit_wait(exc, 1) is not None and bool(_QUOTA_EXHAUSTED.search(msg))


def _failure_reason(exc: Exception) -> str:
    if _quota_exhausted(exc):
        return "quota_exhausted"
    if _rate_limit_wait(exc, 1) is not None:
        return "rate_limited"
    return "timeout" if "timed out" in str(exc).lower() or "timeout" in type(exc).__name__.lower() else "error"


def _unsupported_param(exc: Exception, sent: dict) -> str:
    """Name of a tuning parameter the endpoint rejected, or '' if the error is something else.

    Only 4xx *parameter* complaints qualify: a timeout or a 500 must still go through the normal
    retry/backoff path rather than silently stripping the request down."""
    msg = str(exc).lower()
    if not any(tok in msg for tok in ("400", "invalid", "unsupported", "unrecognized",
                                      "not supported", "unknown")):
        return ""
    for name in sent:
        if name.lower() in msg:
            return name
    return ""


def openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip().strip("<>").strip()


def gemini_api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip().strip("<>").strip()


class LLMClient:
    def __init__(self, key: str = "", base_url: str = "", model: str = ""):
        oa_key = (key or openai_api_key()).strip().strip("<>").strip()
        ge_key = gemini_api_key()

        if oa_key:
            self.key, self.provider = oa_key, "openai"
            self.base_url = (base_url or LLM_BASE_URL).strip()
            self.model = model or LLM_MODEL
        elif ge_key:
            self.key, self.provider = ge_key, "gemini"
            self.base_url = (base_url or GEMINI_BASE_URL).strip()
            self.model = model or GEMINI_MODEL
        else:
            self.key, self.provider = "", "none"
            self.base_url = (base_url or LLM_BASE_URL).strip()
            self.model = model or LLM_MODEL

        self.available = bool(self.key)
        self._client = None
        self.last_error: str = ""

        if self.available:
            try:
                from openai import OpenAI
                # The x-api-key header is Siemens-gateway-specific; Gemini's compat layer
                # only wants the standard Authorization: Bearer header the client sets itself.
                headers = {"x-api-key": self.key} if self.provider == "openai" else {}
                self._client = OpenAI(
                    api_key=self.key,
                    base_url=self.base_url,
                    default_headers=headers,
                    timeout=LLM_TIMEOUT,
                    max_retries=0,
                )
            except Exception as e:
                self.available = False
                self.last_error = str(e)

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200,
                 model: str = "", temperature: float = LLM_TEMPERATURE,
                 reasoning: str = "", max_attempts: int | None = None,
                 timeout: float | None = None, hedge_after: float | None = None) -> str:
        """Run one completion; '' on failure.

        ``hedge_after`` sends one identical duplicate if the first request has not answered after
        that many seconds, and takes whichever answers first (see ``_hedged``).

        ``timeout`` overrides LLM_TIMEOUT for a call measured to need longer. The default is sized
        for a hung request, not a slow one: a call that reliably takes longer than it is killed
        and retried from scratch every time, which costs the whole budget again and, when every
        attempt dies the same way, ends in the caller's offline fallback.

        ``temperature`` defaults to 0 so repeated runs agree: left unset, Gemini defaults to 1.0
        and four identical extraction calls returned three different JSON spellings, which is
        why re-evaluating the same startup kept producing subtly different profiles.

        ``reasoning`` maps to OpenAI's ``reasoning_effort`` and is sent ONLY to models that
        actually reason (Gemini, and gpt-5/gpt-6 on the Siemens gateway). Pass
        "none" for calls that transcribe supplied evidence into JSON — measured on the profile
        extraction prompt, that is ~5x faster AND more accurate than thinking (it recovered a
        founding year the thinking run missed), because the work is reading, not reasoning.
        """
        if not self.available:
            return ""
        use_model = (model or self.model)
        thinks = _reasons(self.provider, use_model)
        budget = max(max_tokens, LLM_MIN_BUDGET)
        # Applied even when reasoning is off. max_completion_tokens is a ceiling, not a charge,
        # so a generous one costs nothing — whereas trimming it to the "no thought tokens
        # needed" figure truncated the largest profile extractions mid-JSON and dropped them
        # back to keyword-only, because the per-call max_tokens values were never sized for a
        # full founders+advisors+programs+customers reply on their own.
        if thinks:
            budget += LLM_THINKING_HEADROOM
        msgs = [
            {"role": "system",
             "content": system or "You are a precise startup-evaluation analyst for Siemens."},
            {"role": "user", "content": prompt},
        ]
        extra: dict = {}
        effort = (reasoning or LLM_REASONING_EFFORT) if thinks else ""
        if effort == "none" and _thinking_floor(use_model):
            effort = "minimal"
        # The gateway's gpt-5.x rejects any temperature but the default unless reasoning is off.
        if temperature is not None and not (self.provider == "openai" and _GPT_REASONING.match(use_model or "")
                                            and effort != "none"):
            extra["temperature"] = temperature
        if effort:
            extra["reasoning_effort"] = effort
        # Replies are cached on the full request. Sound only because temperature is 0: at the
        # old default of 1.0 the same prompt gave a different answer every time, so a cache
        # would have frozen one arbitrary sample. A changed prompt changes the key, so prompt
        # edits can never be masked by a stale entry. LLM_CACHE=0 disables.
        ckey = _web._cache_key("llm", use_model, system, prompt, budget,
                               extra.get("temperature"), extra.get("reasoning_effort"))
        started = time.time()
        if LLM_CACHE:
            cached = _web._cached("llm", ckey)
            if isinstance(cached, str):
                self.last_error = ""
                # Logged at zero tokens: a cache hit cost nothing, and counting it shows how much
                # of a run the cache answered.
                _record_call(kind="completion", model=use_model, provider=self.provider,
                             started=started, cached=True)
                return cached
        attempts = MAX_RETRIES if max_attempts is None else max(1, int(max_attempts))
        reason = "error"
        attempt = 0
        while attempt < attempts:
            attempt += 1
            try:
                sent = dict(extra)
                ceiling = budget

                def send():
                    if _rate_gate is not None:
                        _rate_gate()
                    return self._client.chat.completions.create(
                        model=use_model,
                        messages=msgs,
                        max_completion_tokens=ceiling,
                        timeout=timeout or LLM_TIMEOUT,
                        **sent,
                    )
                hedged = False
                if hedge_after and hedge_after > 0:
                    resp, hedged = _hedged(send, hedge_after, lambda r: _record_call(
                        kind="completion", model=use_model, provider=self.provider, started=started,
                        reason="hedge_loser", hedged=True, **_usage_counts(r)))
                else:
                    resp = send()
                # A reply cut off at its ceiling is half a JSON object: parse_json rejects it and
                # the caller drops to its offline fallback, with no error anywhere to show for it.
                # Ask again with room to finish, and never cache the cut-off copy.
                while _truncated(resp) and ceiling < LLM_MAX_BUDGET:
                    ceiling = min(ceiling * 4, LLM_MAX_BUDGET)
                    log.warning("LLM reply hit its token ceiling (%s, stage %s); retrying at %d",
                                use_model, _stage.get() or "input", ceiling)
                    resp = send()
                self.last_error = ""
                text = resp.choices[0].message.content or ""
                if _truncated(resp):
                    log.warning("LLM reply still truncated at %d tokens (%s, stage %s)",
                                ceiling, use_model, _stage.get() or "input")
                elif LLM_CACHE and text:
                    _web._store("llm", ckey, text)
                _record_call(kind="completion", model=use_model, provider=self.provider, started=started,
                             attempts=attempt, hedged=hedged, **_usage_counts(resp))
                return text
            except Exception as e:
                # A gateway that rejects one of the tuning parameters fails every call with a
                # 400. Drop the offending parameter and carry on rather than letting a model
                # swap silently disable the LLM entirely.
                dropped = _unsupported_param(e, extra)
                if dropped:
                    log.warning("LLM rejected %r; retrying without it", dropped)
                    extra.pop(dropped, None)
                    continue
                self.last_error = str(e)
                reason = _failure_reason(e)
                wait = _rate_limit_wait(e, attempt)
                if reason == "rate_limited" and max_attempts is None:
                    attempts = max(attempts, RATE_LIMIT_ATTEMPTS)
                log.warning("LLM attempt %d/%d failed (%s): %s", attempt, attempts, reason, e)
                if reason == "quota_exhausted":
                    break
                if attempt < attempts:
                    time.sleep(wait if wait is not None else RETRY_BACKOFF * attempt)
        failures = _failures.get()
        if failures is not None:
            failures.append({"stage": _stage.get() or "input", "reason": reason})
        # A failed call is billed for nothing the run used, but its time and attempts belong in
        # the log: three 30s timeouts are where a slow run went.
        _record_call(kind="completion", model=use_model, provider=self.provider, started=started,
                     ok=False, reason=reason, attempts=attempt)
        return ""

    def web_answer(self, prompt: str, system: str = "", max_tokens: int = 1200,
                   thinking_budget: Optional[int] = None) -> Optional[dict]:
        """One completion that searches the internet itself, with the sources it actually used.

        Gemini grounds with Google Search; the OpenAI-compatible gateway uses the Responses API's
        `web_search` tool. Either way the search is the model's own, so there is no scraping and
        no result list to adjudicate: the sources are the ones the provider says it cited.
        Returns {"text", "sources": [{"title", "url"}], "queries"} or None when this provider or
        gateway cannot search — the caller says so rather than passing memory off as search.
        Never cached: an internet answer is only worth having fresh. ``thinking_budget`` caps a
        thinking model's reasoning tokens (Gemini's ``thinkingBudget``); None leaves the default.
        """
        if not self.available:
            return None
        _tls.web_usage = {}
        started = time.time()
        out = None
        try:
            out = self._web_answer(prompt, system, max_tokens, thinking_budget)
        finally:
            _record_call(kind="web_search", model=self.model, provider=self.provider, started=started,
                         ok=out is not None, reason="" if out is not None else "no_answer",
                         **getattr(_tls, "web_usage", {}))
        return out

    def embedding_model(self) -> str:
        """The embedding model served by the selected endpoint, or '' when none is known.

        Gemini direct serves gemini-embedding-001; the Siemens gateway serves Gemini's embedding
        under its own name. The two produce different vectors, so core/tool_search.py keeps one
        index file per model and each environment loads its own.
        """
        configured = os.getenv("EMBEDDING_MODEL", "").strip()
        if configured:
            return configured
        if self.provider == "openai" and self.base_url.rstrip("/") == "https://llm.sdc.siemens.cloud/v1":
            return "google/gemini-embedding-2"
        return "gemini-embedding-001" if self.provider == "gemini" else ""

    def _gateway_embeddings(self, texts: list[str], model: str, dims: int) -> list:
        # The gateway does not serve Gemini embeddings on the OpenAI-compatible /embeddings route,
        # only on Gemini's native embedContent, one text per request.
        from concurrent.futures import ThreadPoolExecutor
        import requests
        model_path = f"models/{model.removeprefix('google/')}"
        url = f"{self.base_url.rstrip('/').removesuffix('/v1')}/v1beta/{model_path}:embedContent"

        def embed_one(text: str) -> list:
            attempt = 0
            while True:
                attempt += 1
                if _rate_gate is not None:
                    _rate_gate()
                try:
                    response = requests.post(
                        url, headers={"x-api-key": self.key},
                        json={"content": {"parts": [{"text": text}]}, "outputDimensionality": dims},
                        timeout=LLM_TIMEOUT,
                    )
                    break
                except (requests.ConnectionError, requests.Timeout):
                    if attempt >= EMBED_RETRIES:
                        raise
                    time.sleep(RETRY_BACKOFF * attempt)
            response.raise_for_status()
            return list(response.json()["embedding"]["values"])

        if len(texts) == 1:
            return [embed_one(texts[0])]
        with ThreadPoolExecutor(max_workers=min(8, len(texts))) as executor:
            return list(executor.map(embed_one, texts))

    def embed(self, texts: list[str], dims: int = EMBEDDING_DIMS, cache: bool = True) -> Optional[list]:
        """Embedding vectors for ``texts`` (one per text), or None when this provider cannot embed.

        A single text is cached like a completion (same store, same refresh bypass), so a re-run of
        a startup does not pay for its query again; catalog batches are built once into a file by
        core/tool_search.py and are not cached here.
        """
        model = self.embedding_model()
        if not self.available or not model or not texts:
            return None
        key = _web._cache_key("embed", model, dims, *texts) if cache and len(texts) == 1 else ""
        started = time.time()
        if key:
            hit = _web._cached("embed", key)
            if isinstance(hit, list):
                _record_call(kind="embedding", model=model, provider=self.provider, started=started, cached=True)
                return hit
        # A rate limit is retried after the quota window, like a completion. Without it a 429 sent
        # Connect's seller search to word matching alone, which looked at a different 30 sellers:
        # Wandelbots' count of sellers selling the same thing went from 3 to 8 between two runs,
        # and with it the score. Any other failure still means "no vectors" at once.
        attempt = 0
        while True:
            attempt += 1
            try:
                if self.provider == "openai" and model.startswith("google/gemini-embedding-"):
                    vectors = self._gateway_embeddings(texts, model, dims)
                    usage = None
                else:
                    if _rate_gate is not None:
                        _rate_gate()
                    resp = self._client.embeddings.create(model=model, input=list(texts), dimensions=dims,
                                                          timeout=LLM_TIMEOUT)
                    vectors = [list(d.embedding) for d in resp.data]
                    usage = getattr(resp, "usage", None)
                break
            except Exception as e:                              # noqa: BLE001 — any failure means "no vectors"
                wait = _rate_limit_wait(e, attempt)
                if wait is not None and attempt < EMBED_RETRIES and not _quota_exhausted(e):
                    log.warning("Embedding rate-limited (%s), attempt %d/%d; retrying in %.0fs",
                                model, attempt, EMBED_RETRIES, wait)
                    time.sleep(wait)
                    continue
                self.last_error = str(e)
                log.warning("Embedding failed (%s): %s", model, e)
                _record_call(kind="embedding", model=model, provider=self.provider, started=started,
                             ok=False, reason=_failure_reason(e), attempts=attempt)
                return None
        self.last_error = ""
        _record_call(kind="embedding", model=model, provider=self.provider, started=started,
                     input_tokens=getattr(usage, "prompt_tokens", 0), total_tokens=getattr(usage, "total_tokens", 0))
        if key:
            _web._store("embed", key, vectors)
        return vectors

    def _web_answer(self, prompt: str, system: str, max_tokens: int, thinking_budget: Optional[int] = None) -> Optional[dict]:
        try:
            if self.provider == "gemini":
                return _gemini_grounded(self, prompt, system, max_tokens, thinking_budget)
            if self.provider == "openai":
                return _openai_web_search(self, prompt, system, max_tokens)
        except Exception as e:                              # noqa: BLE001 — any failure means "no search"
            self.last_error = str(e)
            log.warning("web-search completion failed: %s", e)
        return None

    @staticmethod
    def parse_json(text: str) -> Optional[dict]:
        if not text:
            return None
        m = re.search(r"\{.*\}", text, re.DOTALL)
        try:
            return json.loads(m.group(0)) if m else None
        except Exception:
            pass
        # A reply with one stray brace or a trailing remark after the object ("}}}", "…} Done.")
        # still holds a whole object at its start; read just that rather than discarding it.
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None


def _truncated(resp) -> bool:
    try:
        return getattr(resp.choices[0], "finish_reason", "") == "length"
    except (AttributeError, IndexError, TypeError):
        return False


def _cite(text: str, supports: list, chunks: list) -> tuple[str, list]:
    """Insert [n] after each grounded segment and number the sources in first-cited order.

    Gemini reports each supported segment's end as a UTF-8 byte offset into the answer, so the
    markers are placed on the encoded text; placing them on characters would drift after the
    first non-ASCII character."""
    order: list[int] = []
    marks: dict[int, list[int]] = {}
    # A web chunk's title is its site ("pitchbook.com"). Google also attaches utility chunks —
    # "Current time information in Munich, DE." — that are not pages anyone could check.
    def page(i):
        title = str(((chunks[i] or {}).get("web") or {}).get("title", ""))
        return bool(title) and not re.search(r"\s", title.strip())
    for s in supports or []:
        end = (s.get("segment") or {}).get("endIndex")
        idx = [i for i in (s.get("groundingChunkIndices") or [])
               if isinstance(i, int) and 0 <= i < len(chunks) and page(i)]
        if not isinstance(end, int) or not idx:
            continue
        for i in idx:
            if i not in order:
                order.append(i)
        marks.setdefault(end, [])
        marks[end].extend(order.index(i) + 1 for i in idx if order.index(i) + 1 not in marks[end])
    raw = text.encode("utf-8")
    for end in sorted(marks, reverse=True):
        if 0 <= end <= len(raw):
            raw = raw[:end] + "".join(f"[{n}]" for n in sorted(marks[end])).encode() + raw[end:]
    sources = [{"title": (chunks[i].get("web") or {}).get("title", ""), "url": (chunks[i].get("web") or {}).get("uri", "")}
               for i in order]
    return raw.decode("utf-8", errors="ignore"), sources


def _drop_repeat(text: str) -> str:
    """Keep the first copy of a grounded answer the model wrote twice in a row.

    Gemini's search-grounded reply intermittently repeats itself whole — the same opening sentence,
    then a lightly reworded second copy — and the dock showed both. Cut at the opening's second
    appearance; an answer that merely mentions its subject twice does not repeat 60 characters.
    """
    head = text.strip()[:60]
    if len(head) < 40:
        return text
    at = text.find(head, len(head))
    return text[:at].rstrip() if at > 0 else text


def _gemini_grounded(client: "LLMClient", prompt: str, system: str, max_tokens: int,
                     thinking_budget: Optional[int] = None) -> Optional[dict]:
    import requests
    base = os.getenv("GEMINI_NATIVE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": LLM_TEMPERATURE,
                                 "maxOutputTokens": max(max_tokens, LLM_MIN_BUDGET) + LLM_THINKING_HEADROOM}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if thinking_budget is not None:
        body["generationConfig"]["thinkingConfig"] = (
            {"thinkingLevel": "minimal"} if int(thinking_budget) == 0 and _thinking_floor(client.model)
            else {"thinkingBudget": int(thinking_budget)})
    resp = requests.post(f"{base}/models/{client.model}:generateContent", headers={"x-goog-api-key": client.key},
                         json=body, timeout=WEB_SEARCH_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    meta = body.get("usageMetadata") or {}
    _tls.web_usage = {"input_tokens": meta.get("promptTokenCount"), "output_tokens": meta.get("candidatesTokenCount"),
                         "reasoning_tokens": meta.get("thoughtsTokenCount"), "total_tokens": meta.get("totalTokenCount")}
    cand = (body.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if isinstance(p, dict))
    if not text.strip():
        return None
    meta = cand.get("groundingMetadata") or {}
    chunks = [c for c in meta.get("groundingChunks") or [] if isinstance(c, dict)]
    text, sources = _cite(text, meta.get("groundingSupports") or [], chunks)
    # Every page the search returned, cited or not, in Google's order: web.py's search backend
    # reads these pages itself and never the answer (see web._grounded_search).
    pages = [{"title": str(w.get("title", "")), "url": str(w.get("uri", ""))}
             for w in ((c.get("web") or {}) for c in chunks) if w.get("uri")]
    return {"text": _drop_repeat(text).strip(), "sources": [s for s in sources if s["url"]],
            "queries": [q for q in meta.get("webSearchQueries") or [] if isinstance(q, str)],
            "pages": pages}


def _openai_web_search(client: "LLMClient", prompt: str, system: str, max_tokens: int) -> Optional[dict]:
    ceiling = max(max_tokens, LLM_MIN_BUDGET)
    headroom = LLM_THINKING_HEADROOM if _reasons(client.provider, client.model) else 0
    resp = client._client.responses.create(
        model=client.model, tools=[{"type": "web_search"}], instructions=system or None,
        input=prompt, timeout=WEB_SEARCH_TIMEOUT,
        max_output_tokens=ceiling + headroom)
    # Search results and the answer share the ceiling; one that ran out answers only in part.
    if getattr(resp, "status", "") == "incomplete" and ceiling < LLM_MAX_BUDGET:
        log.warning("web-search answer incomplete at %d tokens; retrying", ceiling)
        resp = client._client.responses.create(
            model=client.model, tools=[{"type": "web_search"}], instructions=system or None,
            input=prompt, timeout=WEB_SEARCH_TIMEOUT * 2,
            max_output_tokens=LLM_MAX_BUDGET + headroom)
    usage = getattr(resp, "usage", None)
    _tls.web_usage = {"input_tokens": getattr(usage, "input_tokens", 0),
                         "output_tokens": getattr(usage, "output_tokens", 0),
                         "reasoning_tokens": getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0),
                         "total_tokens": getattr(usage, "total_tokens", 0)}
    text, sources, index, marks, searched = "", [], {}, {}, False
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", "") == "web_search_call":
            searched = True
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", "") != "output_text":
                continue
            offset = len(text)
            text += getattr(part, "text", "") or ""
            for a in getattr(part, "annotations", None) or []:
                url = getattr(a, "url", "")
                if getattr(a, "type", "") != "url_citation" or not url:
                    continue
                if url not in index:
                    index[url] = len(sources) + 1
                    sources.append({"title": getattr(a, "title", "") or url, "url": url})
                end = getattr(a, "end_index", None)
                if isinstance(end, int):
                    marks.setdefault(offset + end, set()).add(index[url])
    text = text or getattr(resp, "output_text", "") or ""
    # The same "[n] after the claim" shape Gemini's grounding produces, so callers read one format.
    for end in sorted(marks, reverse=True):
        if 0 <= end <= len(text):
            text = text[:end] + "".join(f"[{n}]" for n in sorted(marks[end])) + text[end:]
    # A gateway that accepts the tool but never runs it would return memory under a search label.
    if not text.strip() or not (searched or sources):
        return None
    return {"text": text.strip(), "sources": sources, "queries": []}
