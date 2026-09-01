# Task 2 — Watchlist domain API report

## Scope delivered

- Added the isolated `backend.watchlist` domain package with repository, service,
  and FastAPI router modules.
- Mounted `GET`, `POST`, and `DELETE` routes at `/api/v1/watchlist`.
- Enforced self-owned reads and mutations through `ctx.user_id` and the existing
  tenant transaction RLS handshake. No role gate is applied, so OWNER, ADMIN,
  EDITOR, and VIEWER can manage their own preferences.
- POST accepts only `symbol`; server-side active TICKER master data resolves the
  canonical symbol, asset name, and mapped type. Repeated additions return the
  existing item without a second audit event.
- Adds/removals audit only actual inserted/deleted rows using normalized symbol
  and asset type details.

## TDD evidence

1. Added service and API contract tests before the production watchlist module.
2. RED command:

   ```powershell
   D:\wealthsail\.venv\Scripts\python.exe -m pytest -q tests/unit/test_watchlist_service.py tests/unit/test_watchlist_api.py
   ```

   Result: collection failed with `ModuleNotFoundError: No module named
   'backend.watchlist'`, the expected absent-feature failure.
3. Implemented the minimum repository, service, route, and router-mount code.
4. GREEN command result: `14 passed`.

## Verification

| Command | Result |
| --- | --- |
| Focused service/API plus domain route tests | `19 passed` |
| `ruff format backend/watchlist tests/unit/test_watchlist_service.py tests/unit/test_watchlist_api.py` | formatted 2 files |
| `ruff check backend tests` | clean |
| Full backend `pytest -q` | `636 passed, 4 skipped, 6 pre-existing dependency deprecation warnings` |
| `git diff --check` | clean |

## Risks / follow-up

- The full suite reports six `python-jose` `datetime.utcnow()` deprecation
  warnings in existing authentication tests; this task did not introduce them.
- The API depends on migration 044's `identity.watchlist` table and its RLS
  policies being deployed before runtime use.
