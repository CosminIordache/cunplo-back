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

- `domain/` — plain dataclasses, no framework imports. `User.id` is a `bson.ObjectId` generated client-side at construction, so the domain (not Mongo) owns identity.
- `application/ports/` — `Protocol` interfaces (structural, no inheritance needed by implementers).
- `application/use_cases/` — services taking a port in `__init__`. Currently pass-through to the repository; business rules go here.
- `infrastructure/driven/` — adapters implementing the ports. `mongo_user_repository.py` holds the only `id` ⟷ `_id` mapping (`_to_document` / `_to_user`).
- `presentation/api/` — `router/` (FastAPI endpoints) and `schemas/` (Pydantic request/response models, separate from domain dataclasses).

Dependency flow is one-way inward: presentation → application → domain; infrastructure implements application ports.

### Wiring

`src/container.py` declares all providers and lists the modules to wire in `wiring_config.modules` — **adding a new router module means adding it to that list**, or `Provide[...]` resolves to nothing at runtime. Routes get services via `Annotated[Service, Depends(Provide[Container.user_service])]` plus an `@inject` decorator directly under the route decorator (order matters).

Mongo config comes from env vars `MONGO_URI` / `MONGO_DB` (defaults `mongodb://localhost:27017`, `cunplo`).

### Adding an entity

Mirror the `user` slice across all five layers: domain dataclass → port Protocol → service → Mongo repository → schemas + router; then register providers in `Container` and append the router module to `wiring_config`, and include the router in `src/main.py` under the `/api/v1` prefix.

## Tests

`asyncio_mode = "auto"` — async tests need no marker.

Tests use no database. `tests/conftest.py` provides a `client` fixture that reads an `overrides` dict (`{provider: test double}`), applies `provider.override(...)` around a `TestClient`, and resets afterwards. Each entity's `tests/<entity>/conftest.py` defines its own `overrides` — for users it swaps `container.user_repository` for an in-memory `FakeUserRepository`. New entities follow the same pattern rather than adding new machinery to the root conftest.

## Conventions

- Path params carrying a Mongo id use `ObjectIdParam` from `src/presentation/utils/to_object_id.py`, which converts and 400s on a malformed id.
- Comments and docstrings in the codebase are in Spanish.
- `ponytail:` comments mark deliberate simplifications; leave them in place and extend the same style.

## Notes

`test.py` at the repo root is a scratch file, untracked and unrelated to the `tests/` suite. `.env` is committed-adjacent and currently holds a real `OPENAI_API_KEY` — it is gitignored; do not surface or copy its contents. `pydantic-ai-slim` is a declared dependency but nothing imports it yet.
