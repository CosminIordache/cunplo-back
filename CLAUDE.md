# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependencies are managed with `uv` (see `uv.lock`; `requirements.txt` is empty and unused).

```bash
uv sync                                   # install deps (incl. dev group)
uv run python -m src.main                 # run dev server (uvicorn, reload, uvloop/httptools)
uv run pytest                             # all tests
uv run pytest tests/users/test_api.py     # one file
uv run pytest -k test_health_is_ok        # one test
```

No linter or formatter is configured. Indentation is 2 spaces, not 4 — match it.

## Architecture

Hexagonal layering under `src/`, wired by `dependency-injector`:

- `domain/` — plain dataclasses, no framework imports. `User.id` / `Integration.id` are `bson.ObjectId`s generated client-side at construction, so the domain (not Mongo) owns identity.
- `application/ports/` — `Protocol` interfaces (structural, no inheritance needed by implementers).
- `application/use_cases/` — services taking a port in `__init__`. `user_service` is pass-through; `auth_service`, `integration_service` and `gmail_service` hold real rules.
- `infrastructure/driven/` — adapters. The Mongo repositories hold the only `id` ⟷ `_id` mapping (`_to_document` / `_to_user`, `_to_integration`); `gmail.py` and `google_oauth.py` are HTTP/OAuth clients, not repositories.
- `infrastructure/utils/` — `security.py` (scrypt password hashing, HS256 JWT via joserfc) and `crypto.py` (Fernet, used only by the integration repository).
- `presentation/api/` — `router/` (FastAPI endpoints) and `schemas/` (Pydantic request/response models, separate from domain dataclasses); `middleware/auth.py` holds the bearer guard.

Dependency flow is one-way inward: presentation → application → domain; infrastructure implements application ports.

### Wiring

`src/container.py` declares all providers and lists the modules to wire in `wiring_config.modules` — **adding a new router module means adding it to that list**, or `Provide[...]` resolves to nothing at runtime. Routes get services via `Annotated[Service, Depends(Provide[Container.user_service])]` plus an `@inject` decorator directly under the route decorator (order matters).

`integration_service` gets `refresh_token` injected as a plain callable rather than a class — the infrastructure seam here is a function, and tests pass their own stub in its place.

### Auth

Two ways in, both ending at the same HS256 JWT (`create_token(str(user.id))`):

- `POST /auth/register` + `/auth/login` — scrypt-hashed password stored as `b64(salt)$b64(digest)`.
- `GET /auth/google` → `/auth/google/callback` — Authlib OIDC. Login and Gmail consent are a single grant (`GMAIL_SCOPE`, `access_type=offline`, `prompt=consent`) so the callback gets a `refresh_token` and can connect the integration in the same request. `SessionMiddleware` in `main.py` exists only to carry Authlib's OAuth `state`.

Protected routes take `CurrentUser` from `src/presentation/middleware/auth.py`, which decodes the bearer token and re-loads the user. Anything failing there is a 401 — the middleware never distinguishes bad token from unknown user.

### Gmail integration

A user has **at most one `Integration` per provider** — that is the key `upsert` filters on, and a unique index backs it. Connecting a different Google account replaces the row rather than adding one (tokens and sync state are only inherited when `account_id`, the provider `sub`, matches). Tokens are Fernet-encrypted on the way into Mongo and decrypted on the way out; they are never stored in clear.

`GmailService.disconnect(user_id, provider)` is the only delete path: it stops the Gmail watch and revokes the grant at Google before dropping the row, and tolerates a dead token (logs, deletes anyway). Both `DELETE /integrations/google` and `DELETE /users/{id}` go through it — deleting a user must not leave a live Google grant behind.

`IntegrationService.access_token_for` is the only path to a usable access token: it returns the cached one, refreshes if expired, and raises `ReauthRequired` when the refresh token is gone or revoked. `connect()` deliberately carries `history_id` / `watch_expires_at` forward from the existing row — sync state must survive a token refresh, since refresh goes through `connect()` too.

Push flow: the callback calls `GmailService.start_watch` (only when `PUBSUB_TOPIC` is set; failures are logged, never fatal to login) → Gmail publishes to Pub/Sub → `POST /webhooks/gmail` decodes the base64 envelope → `process_notification` walks the history from the *stored* `history_id`, not the one in the notification. A first-ever notification only records the marker (no backfill), and an expired history resyncs to the current marker. Gmail's watch expires in 7 days and nothing renews it yet.

### Config

All env vars are read at import time from `.env` via `load_dotenv()` in `src/main.py`.

- Required, raise at import if missing: `JWT_SECRET`, `ENCRYPTION_KEY` (a Fernet key).
- Optional: `MONGO_URI` / `MONGO_DB` (`mongodb://localhost:27017`, `cunplo`), `JWT_TTL_DAYS` (1), `GOOGLE_CLIENT_ID` / `GOOGLE_SECRET`, `PUBSUB_TOPIC` (empty disables the Gmail watch), `PUBSUB_TOKEN` (empty disables webhook auth), `FRONTEND_REDIRECT`, `SESSION_SECRET`, `LOG_LEVEL`.

Mongo being down is logged, not fatal — the app starts either way.

`create_indexes` in `src/container.py` runs on startup, right after the connection ping (so it is skipped when Mongo is down). It is idempotent — add new indexes there rather than by hand.

### Adding an entity

Mirror the `user` slice across all five layers: domain dataclass → port Protocol → service → Mongo repository → schemas + router; then register providers in `Container` and append the router module to `wiring_config`, and include the router in `src/main.py` under the `/api/v1` prefix.

## Tests

`asyncio_mode = "auto"` — async tests need no marker.

Tests use no database and make no network calls. `tests/conftest.py` provides a `client` fixture that reads an `overrides` dict (`{provider: test double}`), applies `provider.override(...)` around a `TestClient`, and resets afterwards. Each entity's `tests/<entity>/conftest.py` defines its own `overrides` — swapping `container.user_repository` / `container.integration_repository` for in-memory fakes. New entities follow the same pattern rather than adding new machinery to the root conftest.

Two things are faked without the container: the Google/Gmail HTTP clients are `monkeypatch.setattr`'d on the `src.infrastructure.driven.gmail` module (patch the module attribute, not the import inside `gmail_service`), and `IntegrationService`'s `refresh` callable is passed directly by `make_service` in `tests/integrations/test_integration_service.py`.

Importing `src.main` (which every test does, via the root conftest) runs `load_dotenv()` — so `.env` must hold a valid `JWT_SECRET` and `ENCRYPTION_KEY` or collection fails at import, before any test runs.

## Conventions

- Path params carrying a Mongo id use `ObjectIdParam` from `src/presentation/utils/to_object_id.py`, which converts and 400s on a malformed id.
- Services raise their own domain exceptions (`EmailAlreadyUsed`, `InvalidCredentials`, `ReauthRequired`, `GmailError`, `HistoryTooOld`); the router is where they become `HTTPException`. Keep `fastapi` imports out of `use_cases/`.
- Queries that touch a user's own data carry `user_id` in the Mongo filter itself (see `MongoIntegrationRepository.delete`) rather than checking ownership after the fetch.
- Comments and docstrings in the codebase are in Spanish; log messages are always in English.
- `ponytail:` comments mark deliberate simplifications; leave them in place and extend the same style.

## Notes

`test.py` at the repo root is a scratch file, untracked and unrelated to the `tests/` suite. `README.md` is empty. `.env` is gitignored and holds real secrets (including an `OPENAI_API_KEY`) — do not surface or copy its contents. `pydantic-ai-slim` is a declared dependency but nothing imports it yet; `gmail.stop_watch` is likewise defined but uncalled.

Known gaps, deliberate for now: nothing renews the Gmail watch before its 7-day expiry, `process_notification` fetches messages serially and inline in the request, and CORS is `allow_origins=["*"]`.
