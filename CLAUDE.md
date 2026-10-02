# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependencies are managed with `uv` (see `uv.lock`; `requirements.txt` is empty and unused).

```bash
uv sync                                   # install deps (incl. dev group)
uv run python -m src.main                 # API (uvicorn, reload, uvloop/httptools)
uv run arq src.infrastructure.driven.redis.worker.WorkerSettings   # background worker
uv run pytest                             # tests (see Tests below — the suite is currently empty)
uv run --env-file .env.dev arq src.infrastructure.driven.redis.worker.WorkerSettings    # Worker with env dev
uv run --env-file .env.dev python -m src.main # API with env dev
```

Two processes: the FastAPI app **and** the arq worker. The API only enqueues; every mail is
analyzed in the worker. Running the API alone means webhooks are accepted and nothing happens.

No linter or formatter is configured. Indentation is 2 spaces, not 4 — match it.

## Architecture

Hexagonal layering under `src/`, wired by `dependency-injector`:

- `domain/` — plain dataclasses (`User`, `Integration`, `Message`, `Task`, `Contact`), no framework imports. Ids are `bson.ObjectId`s generated client-side at construction, so the domain (not Mongo) owns identity.
- `application/ports/` — `Protocol` interfaces (structural, no inheritance needed by implementers).
- `application/use_cases/` — services taking their ports in `__init__`. `user_service`, `message_service`, `contact_service` are near pass-through; `auth_service`, `integration_service`, `gmail_service`, `task_service`, `agent_service` (mail) and `whatsapp_agent_service` hold the real rules.
- `infrastructure/driven/mongo/` — repositories. They hold the only `id` ⟷ `_id` mapping (`_to_document` / `_to_<entity>`).
- `infrastructure/driven/redis/` — `worker.py` (arq `WorkerSettings` + the `redis_pool` provider) and `functions/` (the enqueued jobs and the cron).
- `infrastructure/driving/` — the two push webhooks (`gmail_webhook.py`, `whatsapp_webhook.py`). Inbound adapters, mounted as routers but deliberately not under `presentation/`.
- `infrastructure/external_services/` — HTTP/OAuth clients: `gmail.py`, `google_oauth.py`, `oauth_refresh.py` (a provider → refresher dict), and `gowa.py` (WhatsApp, see below).
- `infrastructure/utils/` — `security.py` (scrypt hashing, HS256 JWT via joserfc, the session cookie helpers) and `crypto.py` (Fernet, used only by the integration repository).
- `presentation/api/` — `router/` (endpoints) and `schemas/` (Pydantic models, separate from domain dataclasses); `middleware/auth.py` holds the cookie guard.

Dependency flow is one-way inward: presentation → application → domain; infrastructure implements application ports.

### Wiring

`src/container.py` declares all providers and lists the modules to wire in `wiring_config.modules` — **adding a new router or webhook module means adding it to that list**, or `Provide[...]` resolves to nothing at runtime. Routes get services via `Annotated[Service, Depends(Provide[Container.x_service])]` plus an `@inject` decorator directly under the route decorator (order matters).

`integration_service` gets `refresh_token` injected as a plain callable rather than a class — the infrastructure seam here is a function.

The worker builds its **own** `Container` in `worker.startup` and stashes resolved services in arq's `ctx`. Providers that sit behind an async `Resource` (anything touching Mongo) must be awaited there; `agent_service` / `whatsapp_agent_service` have no such dependency and are called without `await`. A new service used by a job must be added to `ctx` in `startup`.

### Auth vs. integrations — two separate OAuth flows

This is the split that `b1ee97f` introduced; do not collapse them.

- **`/auth/google` + `/callback`** — identity only (`LOGIN_SCOPE`). Creates or logs in the `User` and sets the session cookie. Grants no mailbox access.
- **`/integrations/google/connect` + `/callback`** — mailbox consent (`GMAIL_SCOPE`), requires an already-authenticated user. Creates the `Integration` and starts the push.

The provider does not send our cookie back on the connect callback, so `connect` stashes `request.session["connect_user_id"]` in the signed session and the callback pops it. `SessionMiddleware` in `main.py` therefore carries both Authlib's OAuth `state` and this hand-off.

Identity is the provider's `sub` (`User.auth_provider` + `auth_account_id`), never the email. `login_oauth` links a second provider onto an existing row when the email matches rather than duplicating the user.

Google's connect asks `access_type=offline&prompt=consent` — the only way to get a `refresh_token`. `google_oauth` registers `client_kwargs={"scope": GMAIL_SCOPE}` on purpose: Authlib replays that scope on refresh, and asking for less than what was granted yields a token that 403s on the mailbox.

Password register/login exist in `auth_service` but the routes are **commented out** in `router/auth.py` — OAuth is the only live way in.

### Sessions

The JWT rides in an httponly cookie named `access_token`, not a bearer header. `set_session_cookie` in `security.py` owns its flags; `COOKIE_SECURE` / `COOKIE_DOMAIN` come from env (both must be set in production, and the cookie must not be named `session` — `SessionMiddleware` owns that name and would clobber it). `CurrentUser` in `presentation/middleware/auth.py` decodes the cookie and re-loads the user; anything failing there is a 401 with no distinction between bad token and unknown user.

### Mail integrations

A user may have **several accounts per provider**. The unique key is `(user_id, provider, account_id)` where `account_id` is the provider `sub` — so reconnecting the same mailbox updates the row and connecting another one inserts. Deletes and reads therefore go by integration **id**, not by provider (`DELETE /integrations/{id}`). Tokens are Fernet-encrypted on the way into Mongo and decrypted on the way out; never stored in clear.

`IntegrationService.access_token_for` is the only path to a usable access token: it returns the cached one, refreshes via `oauth_refresh.refresh_token` if expired, and raises `ReauthRequired` when the refresh token is gone or revoked. `connect()` deliberately carries `history_id` / `watch_expires_at` forward from the existing row — sync state must survive a token refresh, since refresh goes back through `connect()`.

`history_id` is the shared name for "how far we have processed": a Gmail `historyId`. A first-ever sync only records the marker (no backfill), an expired marker resyncs to the current one, Gmail watches and reads `INBOX` **and** `SENT` (`gmail.LABELS`), filtering the history by each message's own labels rather than with `labelId`: that parameter filters by thread, so a thread the owner opens never matched `INBOX` until someone replied. On an owner's mail the Gmail job checks `only_contacts` against the recipients (`_counterparts`), and drafts are dropped (Gmail indexes a draft while it is being written and it reappears with a different id once sent).

`disconnect` is per-provider and is the only delete path. Gmail stops the watch and revokes the grant at Google; WhatsApp logs the GOWA device out. Both tolerate a dead token/device: log, delete anyway. `router/integration.py:disconnect_integration` picks the right one from the row's provider, and `DELETE /users/{id}` reuses it — deleting a user must not leave a live grant behind.

### WhatsApp (GOWA)

WhatsApp is the second `Provider`, served by a separate [GOWA](https://github.com/aldinokemal/go-whatsapp-web-multidevice) instance (Railway) that `gowa.py` calls with Basic Auth. One GOWA **device** = one number = one `Integration`, with `account_id` = device_id and `phone` = the number in E.164 (`email` is None; `Integration.__post_init__` enforces email-only for mail providers, phone-only for WhatsApp). No OAuth, no tokens, no push to renew — GOWA keeps the session. **The `Integration` only exists once a number is linked**: the pending device lives in GOWA alone, and its id is `<user_id>_<ObjectId>` so `device_owner` can tell whose it is without a Mongo row. `_pending_device` reuses the user's unlinked device, so abandoned QR attempts don't pile up. Linking is `POST /integrations/whatsapp/connect` (QR as a data URL — GOWA's QR link is behind its Basic Auth, so we fetch it) or `/connect/code` (pairing code, for users on the same phone); both return a `device_id` and the frontend polls `GET /integrations/whatsapp/{device_id}/status`, which creates the integration (`activate`, idempotent) the first time GOWA reports a JID. The webhook never creates one: with GOWA down during a disconnect, it would resurrect the account.

The webhook is set **per device** when `_pending` creates it (`webhook_url` = `WHATSAPP_WEBHOOK_URL`, `webhook_secret` = `GOWA_WEBHOOK_SECRET`, events `message`) — GOWA itself needs no webhook config and only sends us our own devices. The URL is stored in each device: if the API domain changes, existing devices need `PATCH /devices/{id}/webhook`. GOWA signs with HMAC-SHA256 over the raw body (`X-Hub-Signature-256`); it finds the row by `session_id` (= account_id, indexed `(provider, account_id)`) or, failing that, by the device JID's number. Unlike mail, the payload already carries the message, so `process_whatsapp_message` has no sync step. Groups/broadcasts/newsletters and messages with neither text nor a useful attachment (stickers, reactions...) are dropped in the webhook. Audio/document/image/video go to the bucket like mail attachments (`attachment_service.store_for_message`, bytes from GOWA's `/statics`, `attachment_id` = the kind, GOWA's renamed file as filename), and the job also turns them into text in `body` for the agent (`_media_text`) — a voice note is transcribed (`transcription_service.audio_transcription`, cached in `wa:media:*` so a lock retry doesn't pay twice), a document/image/video becomes `[Documento PDF]` + its caption. This relies on GOWA's `WHATSAPP_AUTO_DOWNLOAD_MEDIA` (default on): without it the payload only carries an encrypted `mmg.whatsapp.net` URL. Note GOWA serves `/statics` *before* its Basic Auth, so downloaded media is public to anyone with the path; the owner's own messages are analyzed too (`to_message` swaps sender/recipient, and `only_contacts` checks the recipient). Jobs are enqueued with `_job_id=wa:<integration>:<message>` because GOWA retries.

**Debounce:** WhatsApp arrives in bursts ("hola" + the real request), so the webhook enqueues with `_defer_by=DEBOUNCE_SECONDS` (60) and, only if a job was actually enqueued, sets `wa:last:<integration>:<chat>` to that message id. Every job **stores its message** (the chat is the context), but only the job whose message is still `wa:last` analyzes: one agent call with everything newer than `wa:done:<integration>:<chat>` (the `internal_date` of the last analyzed message; missing → the last hour) as `new_messages` to `WhatsAppAgentService`, and the last `CONTEXT_MESSAGES` (30) before it as context. The analysis runs under `mailbox_lock` per chat. A WhatsApp chat keeps messages that produced no task too, but a chat with no tasks is trimmed to the last 30 after each analysis, one with tasks is kept whole (deleting a task never deletes messages).

Contacts: `Contact.email` is optional — a WhatsApp contact has only a phone (E.164, `normalize_phone` in `contact_service.py`). Both `(user_id, email)` and `(user_id, phone)` are unique **partial** indexes. `resolve_contacts` / `is_known_contact` in `contacts_filter.py` are shared by the two jobs and look up by email, else by phone; an email contact whose phone already belongs to someone else (a switchboard) is created without the phone.

### Mail → task pipeline

```
Gmail push  → POST /webhooks/gmail   → enqueue process_gmail_notification(email, history_id)
GOWA push   → POST /webhooks/whatsapp → enqueue process_whatsapp_message(integration_id, user_id, message)
                                     → gmail_service.process_notification → new messages
                                     → AgentService.run_tasks(counterpart context + new mail)
                                     → upsert Message + upsert Task + resolve/create Contacts
```

Both webhooks return 2xx almost unconditionally — a non-2xx makes the provider retry. Gmail auth is a shared `?token=` (`PUBSUB_TOKEN`).

The Gmail job runs **one per mailbox at a time** (`mailbox_lock.py`, a Redis lock on `ctx["redis"]`): Gmail sends a push per mailbox change, and parallel jobs all read the same stored marker and downloaded the same mails, burning the per-user quota (`Units per minute per user`). A job that finds the lock taken raises `Retry(defer=10)` and, when it gets in, the marker has moved on, so it is cheap; that is why it is registered with `func(..., max_tries=MAX_TRIES)`. Quota errors (`GmailRateLimited` — 429 or a 403 quota/rateLimitExceeded) become `Retry(defer=60)`; the marker has not advanced, so the retry picks the same mails up.

The two job modules (`process_gmail_notification.py`, `process_whatsapp_message.py`) share the task upsert/contacts tail but no longer the context: mail has its own agent (`AgentService`, `agent_service.py`) and WhatsApp its own (`WhatsAppAgentService`, `whatsapp_agent_service.py`, still the old shared prompt). Keep the task upsert in sync; the context and prompt are per channel.

**Mail context is by counterpart, not by thread.** In mail the thread is unreliable (people reply to an old mail about something else, open a new one to continue a topic, forward, drop the "Re:"), so for each new mail the Gmail job takes every address in From/To/Cc minus the owner (`_addresses`) and `messages.list_context` returns the last `CONTEXT_MESSAGES` (20) mails of the same mailbox that are in the same thread **or** share any of those addresses. `participants` (lowercased From/To/Cc emails) exists only in Mongo for that query: `mongo_message_repository._to_document` computes it, `_to_message` drops it, and `create_indexes` backfills old rows (`ponytail:`). The tasks shown to the agent are those of every thread in that context plus the current one (`task_service.get_by_threads`).

`AgentService` (pydantic-ai, OpenAI) is **mail only**. It gets those related tasks (each with its thread, the current one marked), the context (each mail with date, thread and subject, bodies cut at `CONTEXT_BODY_CHARS` because Gmail quotes the previous replies) and the new mail, and returns `list[ExtractedTask]` (empty = nothing). The rules live in the `INSTRUCTIONS` prompt in `agent_service.py`, and that prompt is the specification of what a task is, so behaviour changes belong there, not in the callers. It relates a mail to a task by content and people, never by thread or subject; it also covers quoted text, forwards, owner in Cc and auto-replies, and resolves relative dates against the new mail's date. **A conversation may carry several tasks, one per distinct action.** The agent returns only the tasks the new mail creates or changes: an item with `task_id` updates that task (from any related thread), one without creates a new one, and untouched tasks are not returned. `existing_task` in `task_service.py` accepts a `task_id` only if it is among the tasks handed to the agent; an unknown id is logged and created as new. A task updated from another thread **keeps its original `thread_id`** (where it was born), because `upsert` goes by `_id` with user, account and thread in the filter.

The agent also sees attachments (`AgentEmailMessage.attachments`): every mail lists its filenames, and the images/PDFs (≤10 MB, `READABLE` in `attachment_service.py`) of the **new** mail go as files after the text, so it can tell whether a reply really delivers what was asked. The Gmail job fetches them with `attachment_service.for_agent` *before* the analysis and `store_for_message` reuses the bytes; the WhatsApp burst reads them back from the bucket (`for_agent_stored(with_data=True)`), since each message stored its own in its job.

**Every mail that passes the `only_contacts` filter is stored**, task or not: it is the context for whatever is said next with those people (with `only_contacts` off this includes newsletters, a known `ponytail:`). WhatsApp also stores every message (see Debounce). Deleting a task deletes **only the task**; messages stay as context.

A `thread_id` is only unique **within one mailbox**, so every per-thread key and filter carries `integration_id` alongside `user_id`: the (non-unique) thread index on `tasks` is `(user_id, integration_id, thread_id)`, and `messages.list_by_thread_id_user_id` / `list_context` filter on user and account too. Without it two accounts of the same user sharing a `thread_id` would mix their tasks. This mirrors `messages`, whose unique key has always been `(integration_id, provider_id)`. `GET /messages/thread/{integration_id}/{thread_id}` takes the account in the path for the same reason.

Gmail push expires after 7 days. The daily `renew_watches` cron at 04:00 renews every watch expiring soon, and a broken account is logged and skipped rather than aborting the run. `list_expiring` also picks up rows with a null `watch_expires_at`, so an account that failed to start push gets retried.

### Config

Env vars are read at import time from `.env` via `load_dotenv()` — in `src/main.py` for the API and again in `worker.py`, since the worker is a separate process.

- Required, raise at import: `JWT_SECRET`, `ENCRYPTION_KEY` (a Fernet key). `CORS_ORIGINS` is `.split(",")` unconditionally and will `AttributeError` if unset.
- Optional: `MONGO_URI` / `MONGO_DB` (`mongodb://localhost:27017`, `cunplo`), `REDIS_URI` (`redis://localhost:6379`), `JWT_TTL_DAYS` (1), `COOKIE_SECURE` / `COOKIE_DOMAIN`, `GOOGLE_CLIENT_ID` / `GOOGLE_SECRET`, `PUBSUB_TOPIC` (empty disables the Gmail watch), `PUBSUB_TOKEN`, `GOWA_URL` / `GOWA_BASIC_AUTH` (`user:pass`) / `GOWA_WEBHOOK_SECRET` / `WHATSAPP_WEBHOOK_URL` (the public `/api/v1/webhooks/whatsapp`; both are registered on each GOWA device, and linking a number fails if either is empty), `FRONTEND_REDIRECT`, `SESSION_SECRET`, `ENV` (Logfire environment), `OPENAI_API_KEY`.

Mongo and Redis being down are logged, not fatal — both processes start either way (`queue` is then `None`).

`create_indexes` in `src/container.py` runs on startup right after the Mongo ping (so it is skipped when Mongo is down). It is idempotent — add new indexes there rather than by hand. The comments on each index state the rule it enforces; read them before changing a query.

Deployment is Railway: `ProxyHeadersMiddleware` in `main.py` is what makes `request.url_for()` emit `https://`, without which Google rejects the `redirect_uri`. Observability is Logfire, configured separately in `main.py` and in `worker.startup`.

### Adding an entity

Mirror an existing slice across all five layers: domain dataclass → port Protocol → service → Mongo repository → schemas + router; then register providers in `Container`, append the router module to `wiring_config`, include the router in `src/main.py` under the `/api/v1` prefix, and add any index to `create_indexes`. If a worker job touches it, add the service to `ctx` in `worker.startup` too.

## Tests

`asyncio_mode = "auto"` — async tests need no marker. The suite was deleted at some point; the only file is `tests/test_mail_context.py` (`participants` stays out of the domain, context addresses skip the owner, a task from another thread is accepted, the prompt marks threads and cuts context bodies). It has no database and no conftest, and runs with `uv run --env-file .env.dev pytest`.

If you add more tests, the shape that worked before was: no database and no network, a root `client` fixture applying `provider.override(...)` around a `TestClient` from a per-entity `overrides` dict, in-memory fakes for the repositories, and `monkeypatch.setattr` on the `gmail` / `gowa` modules (patch the module attribute, not the import inside the service).

Importing `src.main` runs `load_dotenv()`, so `.env` must hold a valid `JWT_SECRET` and `ENCRYPTION_KEY` or collection fails at import, before any test runs.

## Conventions

- Path params carrying a Mongo id use `ObjectIdParam` from `src/presentation/utils/to_object_id.py`, which converts and 400s on a malformed id.
- Services raise their own domain exceptions (`EmailAlreadyUsed`, `InvalidCredentials`, `ReauthRequired`, `GmailError`, `HistoryTooOld`, `ContactEmailAlreadyUsed`); the router is where they become `HTTPException`. Keep `fastapi` imports out of `use_cases/`.
- Queries that touch a user's own data carry `user_id` in the Mongo filter itself (see `MongoIntegrationRepository.get` / `delete`) rather than checking ownership after the fetch.
- Logging is `logfire`, not `logging`. Use its `{placeholder}` templates with keyword args (`logfire.info("... {email}", email=...)`) so the fields stay queryable; wrap jobs in `logfire.span`.
- Comments and docstrings in the codebase are in Spanish; log messages are always in English.
- `ponytail:` comments mark deliberate simplifications; leave them in place and extend the same style.

## Notes

`test.py` at the repo root is a scratch file, untracked and unrelated to `tests/`. `README.md` is empty. `.env` is gitignored and holds real secrets — do not surface or copy its contents.

Known gaps, deliberate for now: `process_*` jobs fetch and analyze messages serially (one LLM call per mail), the two job modules are duplicated, arq runs with default concurrency, and the user-delete cascade is sequential without a transaction (`ponytail:` comment in `router/user.py`).
