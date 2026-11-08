# Historical research opportunity preview

**Goal:** Open a complete, read-only opportunity-page simulation from Operations using original server-side frozen research materials and the original validation clock, without model calls, drafts, publication or historical mutation.

**Specification:** The user's detailed historical simulation request in this chat: Operations → Historical research repairs → Preview opportunity page; separate tab at `/operations/research-preview/:runId`; additional links on completed/failed research runs with complete materials; reuse real opportunity components and show every verifiable original fund's NAV, tracks, ranges, stops, source, chart, risks and qualification.

**Architecture:** A tenant-authorized read-only GET loads and verifies PACKET, RAW_MODEL and SOURCE_CHECKS and runs current deterministic parsing at the frozen clock. A bounded DTO carries observation report and independently projected original fund evidence, because observation downgrade intentionally removes formal plans. The frontend reuses opportunity workspace/cards/price/chart components with explicit frozen evidence and no live-data or mutation hooks. Existing repair workflow remains separate.

## Constraints

- No real AI, production database, collection, current quote substitution, tracking, alert, draft, publication, historical FAILED overwrite or old lock change.
- Active OWNER/ADMIN can directly preview; artifact visibility and server authorization still apply on every GET.
- Complete original materials are required. Missing or unreliable fund facts produce explicit reasons, never current-data fallbacks.
- Original raw proposals and rejected evidence remain untrusted. No read-only result grants investment eligibility.
- Private packet fields, credentials and personal deployment parameters never leave the server or enter Git.
- Preserve unrelated user files; verify and commit task changes locally by function; no remote push.

## Implementation

- [x] Backend: frozen material integrity/clock validation, isolated deterministic replay, bounded read-only report/fund/chart DTO, authorization and material-availability metadata.
- [x] Frontend: preview data adapter, real four-workspace layout, reusable direction and fund/track/chart components, historical banner, disabled investment actions and missing-data explanations.
- [x] Operations: direct new-tab entry on repair manager; guarded route; completed/failed research-run links gated by complete material metadata.
- [x] Regression: authorization and no writes/model/network; all original d781 funds from complete local database artifacts; chart/price/time identity; UI no live requests and every mutation disabled.
- [x] Validation: focused/backend/frontend suites, static checks/build, desktop/mobile rendered interaction and console QA, independent review and scoped local commits.

## Review focus

- A historical observation direction with no formal plans must still expose each verifiable original fund's frozen evidence without restoring buy authority.
- Current NAV/positions/tracks must not be fetched or substituted when historical data is absent.
- Partial/missing/corrupted artifacts and wrong identity/clock must fail closed with actionable errors.
- User/tenant/run changes must discard old preview state and private query cache identities.
- No enabled tracking, notification, publication or refresh-research controls may survive shared-component reuse.

## Verification results

- Offline backend suite: 5,789 passed, 391 skipped. Full frontend suite: 1,004 passed across 93 files. Ruff, ESLint, TypeScript and production build passed.
- Complete local database evidence was checked under the real restricted API role and a read-only transaction, including the original material SHA256 values and original validation clock (2026-11-07 21:50:28.937926 Asia/Shanghai).
- Original researched funds 018344, 013477 and 011035 each retain 550 genuine original-batch NAV points. Every point and both tracks' original prices match. All three directions remain WATCH/OBSERVATION_ONLY with no formal plans.
- Production-build browser QA exercised all four workspaces, fund tracks and local chart periods at desktop and 390px mobile widths: one preview GET, no live-data/mutation requests, no runtime errors and no page overflow. Authentication refresh was mocked; no real model or production database was accessed.
- Independent review covered material integrity, original-clock separation, restricted column grants, retained provider history, tenant-scoped listings, read-only projection and disabled controls. The local database has no second active tenant operator; real negative authorization used a non-member context, alongside SQL/RPC scope review and unit tests.
- Private complete materials and acceptance artifacts remain ignored. The opt-in regression `tests/unit/test_research_preview_full_audit.py` pins all three original hashes, compares all three funds' 550 points and prices, and invokes only the isolated deterministic validator. Supply `WEALTHSAIL_PREVIEW_FULL_AUDIT` and `WEALTHSAIL_PREVIEW_FULL_HISTORIES` as local export paths to run it. Its adapter uses bounded run metadata; real database metadata and permissions were checked separately.
- Runtime rollout: the existing local API process has not loaded the new route. Its launcher supervises API, frontend and Worker together, so stopping the API would stop the other services. No running services or Worker were restarted during acceptance; load the new API version before using the live entry.
