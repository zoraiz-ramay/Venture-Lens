---
paths:
  - "api/telemetry.py"
  - "api/auth.py"
  - "api/security.py"
  - "api/main.py"
  - "Dockerfile"
  - "tests/conftest.py"
---

## Telemetry (SigNoz / OpenTelemetry)

`api/telemetry.py` exports traces, metrics and logs over OTLP, and is **off unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set** (`tests/conftest.py` blanks it, so pytest never exports).
`core/` uses only `opentelemetry-api` — spans are no-ops without the SDK — so the engine still runs
standalone. Each pipeline branch runs inside its own span (`pipeline._submit`), and every DuckDuckGo
search gets a `web.search` span, because ddgs' own HTTP client is invisible to instrumentation.

Kept out of telemetry on purpose: the `/api/auth/callback` URL (it carries the Entra code), Redis
(its keys are session ids), and prompt/completion text. Adding a root log handler silences Python's
last-resort stderr output, which is why `attach_log_handler` adds a WARNING console handler first.

Local SigNoz runs self-hosted through Foundry inside WSL2 Ubuntu; UI on :8080, OTLP on :4318.

## Authentication

Sign-in is Microsoft Entra ID, as a **backend-for-frontend**: `api/auth.py` is the confidential
client, does the code exchange, and keeps every token server-side. The browser gets an opaque
session id in an httpOnly cookie and never sees a token — which is what lets `ui/src/api.js` keep
its "no tokens in the browser bundle" stance. Sessions live in Redis (db 1), not in a signed cookie
(that would put the payload in the browser) and not in SQLite (`api/store.py` uploads the whole DB
file to S3 after every write).

Entra is behind a Conditional Access policy requiring a compliant device on a trusted location, so
**no CI runner can ever complete a real sign-in.** `AUTH_MODE=stub` replaces only the round trip to
Entra; cookies, CSRF, the session store and the guard are all production code. It is sealed twice —
`APP_ENV=production` (baked into the `Dockerfile`) and a gunicorn check — and the process exits
rather than starting with auth stubbed. Do not add a third mode, and do not add a "not configured,
so allow" branch anywhere: fail-closed is the point.

The guard lives in `SecurityMiddleware.dispatch()`, so **a new `/api` route is protected by
default** and has to be named in `PUBLIC_PATHS` to opt out. Identity reaches a route via
`Depends(current_user)`, and admin-only routes via its sibling `Depends(require_admin)`.

`overrides.reviewer` used to be free text a client chose. It is now the authenticated principal,
alongside `reviewer_oid/upn/tid/source`. Those are **NULL on pre-SSO rows and are never backfilled**
— promoting unverified history to verified is the one thing an audit trail must not do.
