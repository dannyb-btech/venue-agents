---
paths:
  - "apps/**/*.py"
  - "packages/**/*.py"
  - "tests/**/*.py"
---

# Python conventions

- Python 3.12, fully typed (pyright strict for `packages/`, basic for `apps/`). No `Any` in public APIs.
- Pydantic v2 models for every API body, tool input/output and message payload.
- Async everywhere I/O happens (httpx, asyncpg/psycopg async). No blocking calls in request paths.
- One package per app under `apps/<name>/src/<name>/`; shared code only in `packages/core` or `packages/db`.
- Errors: raise typed exceptions from `packages/core.errors`; never swallow exceptions silently.
- External calls: timeouts always, retries with backoff + jitter for 429/5xx, respect `Retry-After`.
- Tests: pytest + pytest-asyncio; recorded fixtures for external APIs (no live calls in CI);
  test file next to the package in `tests/`. Every bug fix gets a regression test.
- Config via pydantic-settings; no `os.environ` reads outside settings classes.
