# Task 4 Report — 我的关注前端

## Delivered

- Added the `WatchlistItem` DTO, authenticated watchlist API client, and React Query hooks scoped to the private `watchlist/mine` key.
- Added the dashboard “我的关注” panel after market sentiment and before charts. It uses the existing instrument search API, a 300 ms debounce, and the existing two-character search floor.
- The browser sends only `{ symbol }` when adding an item. Add/remove mutations invalidate only the watchlist query after success; errors remain visible and the existing list remains rendered.
- Each pending mutation disables only the matching symbol. The panel renders independently of portfolio emptiness and does not mutate holdings.
- Documented migration 044, self-ownership, API bodies/responses, and deployment order.

## TDD evidence

The focused dashboard test was added before the implementation. Its RED run failed because the “搜索关注标的” field, cancel button, empty state, and watchlist error alert were not rendered. After implementation, all four watchlist tests pass.

## Verification

- `npm test -- --run src/features/dashboard/watchlist.test.tsx src/features/dashboard/dashboard.test.tsx --reporter=dot` — 2 files, 7 tests passed.
- `npm run lint` — passed.
- `npm run build` — passed (`tsc -b && vite build`).
- `D:\wealthsail\.venv\Scripts\python.exe -m pytest -q tests/unit/test_watchlist_migration.py tests/unit/test_watchlist_service.py tests/unit/test_watchlist_api.py tests/unit/test_report_watchlist_context.py` — 18 passed.
- `git diff --check` — passed.

## Deployment gate

Production must apply and read back migration 044 before deploying the corresponding API or frontend build.

## Review round 1 regression fix

The initial panel change added three hook dependencies to every dashboard render: `useWatchlist`, `useAddWatchlist`, and `useRemoveWatchlist`. Three tests fully mock the hooks module, so missing exports caused `WatchlistPanel` to throw during rendering. No production behavior was changed.

- Added neutral, stable watchlist hook mocks to `dashboard-empty.test.tsx`, `dashboard-valuation-syncing.test.tsx`, and the full-route mock in `app-routes.test.tsx`.
- Focused dashboard verification: 4 files, 10 tests passed.
- Route regression verification: 1 file, 3 tests passed.
- Complete frontend suite: 28 files, 105 tests passed.
- `npm run lint` and `npm run build` both passed.
