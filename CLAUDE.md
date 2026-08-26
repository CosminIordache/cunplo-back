# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependencies are managed with `uv` (see `uv.lock`; `requirements.txt` is empty and unused).

```bash
uv sync                                   # install deps (incl. dev group)
uv run python -m src.main                 # API (uvicorn, reload, uvloop/httptools)
uv run arq src.infrastructure.driven.redis.worker.WorkerSettings   # background worker
uv run pytest                             # tests (see Tests below — the suite is currently empty)
```

Two processes: the FastAPI app **and** the arq worker. The API only enqueues; every mail is
analyzed in the worker. Running the API alone means webhooks are accepted and nothing happens.

No linter or formatter is configured. Indentation is 2 spaces, not 4 — match it.

## Architecture

Hexagonal layering under `src/`, wired by `dependency-injector`:

- `domain/` — plain dataclasses (`User`, `Integration`, `Message`, `Task`, `Contact`), no framework imports. Ids are `bson.ObjectId`s generated client-side at construction, so the domain (not Mongo) owns identity.
- `application/ports/` — `Protocol` interfaces (structural, no inheritance needed by implementers).
- `application/use_cases/` — services taking their ports in `__init__`. `user_service`, `message_service`, `contact_service` are near pass-through; `auth_service`, `integration_service`, `gmail_service`, `outlook_service`, `task_service` and `agent_service` hold the real rules.
- `infrastructure/driven/mongo/` — repositories. They hold the only `id` ⟷ `_id` mapping (`_to_document` / `_to_<entity>`).
- `infrastructure/driven/redis/` — `worker.py` (arq `WorkerSettings` + the `redis_pool` provider) and `functions/` (the enqueued jobs and the cron).
- `infrastructure/driving/` — the two push webhooks (`gmail_webhook.py`, `outlook_webhook.py`). Inbound adapters, mounted as routers but deliberately not under `presentation/`.
- `infrastructure/external_services/` — HTTP/OAuth clients: `gmail.py`, `outlook.py` (Graph), `google_oauth.py`, `microsoft_oauth.py`, and `oauth_refresh.py` (a provider → refresher dict).
- `infrastructure/utils/` — `security.py` (scrypt hashing, HS256 JWT via joserfc, the session cookie helpers) and `crypto.py` (Fernet, used only by the integration repository).
- `presentation/api/` — `router/` (endpoints) and `schemas/` (Pydantic models, separate from domain dataclasses); `middleware/auth.py` holds the cookie guard.

Dependency flow is one-way inward: presentation → application → domain; infrastructure implements application ports.

### Wiring

`src/container.py` declares all providers and lists the modules to wire in `wiring_config.modules` — **adding a new router or webhook module means adding it to that list**, or `Provide[...]` resolves to nothing at runtime. Routes get services via `Annotated[Service, Depends(Provide[Container.x_service])]` plus an `@inject` decorator directly under the route decorator (order matters).

`integration_service` gets `refresh_token` injected as a plain callable rather than a class — the infrastructure seam here is a function.

The worker builds its **own** `Container` in `worker.startup` and stashes resolved services in arq's `ctx`. Providers that sit behind an async `Resource` (anything touching Mongo) must be awaited there; `agent_service` has no such dependency and is called without `await`. A new service used by a job must be added to `ctx` in `startup`.

### Auth vs. integrations — two separate OAuth flows

This is the split that `b1ee97f` introduced; do not collapse them.

- **`/auth/{google,microsoft}` + `/callback`** — identity only (`LOGIN_SCOPE`). Creates or logs in the `User` and sets the session cookie. Grants no mailbox access.
- **`/integrations/{google,microsoft}/connect` + `/callback`** — mailbox consent (`GMAIL_SCOPE` / `MAIL_SCOPE`), requires an already-authenticated user. Creates the `Integration` and starts the push.

The provider does not send our cookie back on the connect callback, so `connect` stashes `request.session["connect_user_id"]` in the signed session and the callback pops it. `SessionMiddleware` in `main.py` therefore carries both Authlib's OAuth `state` and this hand-off.

Identity is the provider's `sub` (`User.auth_provider` + `auth_account_id`), never the email. `login_oauth` links a second provider onto an existing row when the email matches rather than duplicating the user.

Google's connect asks `access_type=offline&prompt=consent` — the only way to get a `refresh_token`. Microsoft gets one from the `offline_access` scope. `google_oauth` registers `client_kwargs={"scope": GMAIL_SCOPE}` on purpose: Authlib replays that scope on refresh, and asking for less than what was granted yields a token that 403s on the mailbox. `microsoft_oauth` uses the `common` tenant, so the issuer check is relaxed to `CLAIMS_OPTIONS` (the template `{tenantid}` issuer never matches literally); which accounts may enter is bounded by the app's `signInAudience`.

Password register/login exist in `auth_service` but the routes are **commented out** in `router/auth.py` — OAuth is the only live way in.

### Sessions

The JWT rides in an httponly cookie named `access_token`, not a bearer header. `set_session_cookie` in `security.py` owns its flags; `COOKIE_SECURE` / `COOKIE_DOMAIN` come from env (both must be set in production, and the cookie must not be named `session` — `SessionMiddleware` owns that name and would clobber it). `CurrentUser` in `presentation/middleware/auth.py` decodes the cookie and re-loads the user; anything failing there is a 401 with no distinction between bad token and unknown user.

### Mail integrations

A user may have **several accounts per provider**. The unique key is `(user_id, provider, account_id)` where `account_id` is the provider `sub` — so reconnecting the same mailbox updates the row and connecting another one inserts. Deletes and reads therefore go by integration **id**, not by provider (`DELETE /integrations/{id}`). Tokens are Fernet-encrypted on the way into Mongo and decrypted on the way out; never stored in clear.

`IntegrationService.access_token_for` is the only path to a usable access token: it returns the cached one, refreshes via `oauth_refresh.refresh_token` if expired, and raises `ReauthRequired` when the refresh token is gone or revoked. `connect()` deliberately carries `history_id` / `watch_expires_at` / `subscription_id` forward from the existing row — sync state must survive a token refresh, since refresh goes back through `connect()`.

`history_id` is the shared name for "how far we have processed": a Gmail `historyId`, and for Outlook the Graph **delta link**. Both services follow the same shape — a first-ever sync only records the marker (no backfill), an expired marker resyncs to the current one, and drafts are dropped (Gmail indexes a draft while it is being written and it reappears with a different id once sent).

`disconnect` is per-provider and is the only delete path. Gmail stops the watch and revokes the grant at Google; Outlook deletes the Graph subscription (Microsoft exposes no per-token revoke — the user withdraws consent from their account). Both tolerate a dead token: log, delete anyway. `router/integration.py:disconnect_integration` picks the right one from the row's provider, and `DELETE /users/{id}` reuses it — deleting a user must not leave a live grant behind.

### Mail → task pipeline

```
Gmail push  → POST /webhooks/gmail   → enqueue process_gmail_notification(email, history_id)
Graph push  → POST /webhooks/outlook → enqueue process_outlook_sync(integration_id, user_id)
                                     → <service>.sync/process_notification → new messages
                                     → AgentService.run_tasks(thread context + new mail)
                                     → upsert Message + upsert Task + resolve/create Contacts
```

Both webhooks return 2xx almost unconditionally — a non-2xx makes the provider retry. Gmail auth is a shared `?token=` (`PUBSUB_TOKEN`); Graph's is `clientState` in the body (`GRAPH_CLIENT_STATE`), plus the `?validationToken=` echo Graph requires to register a subscription at all. Graph's notification carries only a `subscriptionId`, which is why `subscription_id` is uniquely indexed and `get_by_subscription` exists.

The two job modules (`process_gmail_notification.py`, `process_outlook_sync.py`) are near-duplicates by design — they differ only in how messages are fetched. Keep them in sync when changing the downstream logic.

`AgentService` (pydantic-ai, OpenAI) decides in one call whether a thread carries a task and returns `ExtractedTask | None`. The rules live in the `INSTRUCTIONS` prompt in `agent_service.py` — that prompt is the specification of what a task is, so behaviour changes belong there, not in the callers. **One thread, at most one task**, backed by a unique index; a later mail in the thread re-`upsert`s it. Only messages that produced a task are stored, and deleting a task deletes its thread's messages.

Push expires on both sides (Gmail 7 days, Graph ~3). The daily `renew_watches` cron at 04:00 renews everything expiring soon, per provider, and a broken account is logged and skipped rather than aborting the run. `list_expiring` also picks up rows with a null `watch_expires_at`, so an account that failed to start push gets retried.

### Config

Env vars are read at import time from `.env` via `load_dotenv()` — in `src/main.py` for the API and again in `worker.py`, since the worker is a separate process.

- Required, raise at import: `JWT_SECRET`, `ENCRYPTION_KEY` (a Fernet key). `CORS_ORIGINS` is `.split(",")` unconditionally and will `AttributeError` if unset.
- Optional: `MONGO_URI` / `MONGO_DB` (`mongodb://localhost:27017`, `cunplo`), `REDIS_URI` (`redis://localhost:6379`), `JWT_TTL_DAYS` (1), `COOKIE_SECURE` / `COOKIE_DOMAIN`, `GOOGLE_CLIENT_ID` / `GOOGLE_SECRET`, `MS_CLIENT_ID` / `MS_SECRET`, `PUBSUB_TOPIC` (empty disables the Gmail watch), `PUBSUB_TOKEN`, `GRAPH_NOTIFICATION_URL` (empty disables Graph push), `GRAPH_CLIENT_STATE`, `FRONTEND_REDIRECT`, `SESSION_SECRET`, `ENV` (Logfire environment), `OPENAI_API_KEY`.

Mongo and Redis being down are logged, not fatal — both processes start either way (`queue` is then `None`).

`create_indexes` in `src/container.py` runs on startup right after the Mongo ping (so it is skipped when Mongo is down). It is idempotent — add new indexes there rather than by hand. The comments on each index state the rule it enforces; read them before changing a query.

Deployment is Railway: `ProxyHeadersMiddleware` in `main.py` is what makes `request.url_for()` emit `https://`, without which Google rejects the `redirect_uri`. Observability is Logfire, configured separately in `main.py` and in `worker.startup`.

### Adding an entity

Mirror an existing slice across all five layers: domain dataclass → port Protocol → service → Mongo repository → schemas + router; then register providers in `Container`, append the router module to `wiring_config`, include the router in `src/main.py` under the `/api/v1` prefix, and add any index to `create_indexes`. If a worker job touches it, add the service to `ctx` in `worker.startup` too.

## Tests

`asyncio_mode = "auto"` — async tests need no marker. **`tests/` is currently empty** (only a stale `__pycache__`); the fixtures described below are gone. If you add tests back, the shape that worked was: no database and no network, a root `client` fixture applying `provider.override(...)` around a `TestClient` from a per-entity `overrides` dict, in-memory fakes for the repositories, and `monkeypatch.setattr` on the `gmail` / `outlook` modules (patch the module attribute, not the import inside the service).

Importing `src.main` runs `load_dotenv()`, so `.env` must hold a valid `JWT_SECRET` and `ENCRYPTION_KEY` or collection fails at import, before any test runs.

## Conventions

- Path params carrying a Mongo id use `ObjectIdParam` from `src/presentation/utils/to_object_id.py`, which converts and 400s on a malformed id.
- Services raise their own domain exceptions (`EmailAlreadyUsed`, `InvalidCredentials`, `ReauthRequired`, `GmailError`, `OutlookError`, `HistoryTooOld`, `DeltaTooOld`, `ContactEmailAlreadyUsed`); the router is where they become `HTTPException`. Keep `fastapi` imports out of `use_cases/`.
- Queries that touch a user's own data carry `user_id` in the Mongo filter itself (see `MongoIntegrationRepository.get` / `delete`) rather than checking ownership after the fetch.
- Logging is `logfire`, not `logging`. Use its `{placeholder}` templates with keyword args (`logfire.info("... {email}", email=...)`) so the fields stay queryable; wrap jobs in `logfire.span`.
- Comments and docstrings in the codebase are in Spanish; log messages are always in English.
- `ponytail:` comments mark deliberate simplifications; leave them in place and extend the same style.

## Notes

`test.py` at the repo root is a scratch file, untracked and unrelated to `tests/`. `README.md` is empty. `.env` is gitignored and holds real secrets — do not surface or copy its contents.

Known gaps, deliberate for now: `process_*` jobs fetch and analyze messages serially (one LLM call per mail), the two job modules are duplicated, arq runs with default concurrency, and the user-delete cascade is sequential without a transaction (`ponytail:` comment in `router/user.py`).
