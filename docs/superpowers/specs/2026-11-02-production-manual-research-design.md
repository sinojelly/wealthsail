# Production manual daily research

The November 2 NAS evidence identifies `REPORT_VALIDATION / DIRECTION_VALIDATION_FAILED`. The old deployment had no rule/index diagnostics and auditing was disabled, so the exact rejected rule cannot be recovered from these records. Container logs use UTC; the observed times match 07:00–07:10 Shanghai. The supplied AI usage query also mistyped its task prefix. These observations do not establish an industry-specific root cause.

## Intent and constraints

An active OWNER/ADMIN can explicitly spend a new Codex invocation to collect fresh public inputs and produce a validated research report. Keep the existing queue, command identity, collector, model adapter, validation, diagnostics and UI polling. Do not change strategy, pricing, model selection, automatic push delivery or daily scheduled cost barriers. No production deployment or actual model call is part of local validation.

## Approach

Simply removing production gates would retain misleading DEBUG records and leave automatic/manual concurrency unprotected. A separate research subsystem would duplicate existing authority and validation. Reuse the existing queue/endpoint with an explicit `MANUAL` mode, migration 097 and one shared persistent model guard.

`DAILY_RESEARCH_MANUAL_ENABLED=false` is the default. MANUAL depends on this explicit switch and current operator membership, independently of the automatic `DAILY_RESEARCH_ENABLED` switch. DEBUG retains its development/test gates. The request mode is frozen when enqueued; enabling production manual work never upgrades a queued DEBUG request.

## Execution

The API still accepts only `commandKey`, binding it to the authenticated actor. A permanently replayed key returns the original request; active requests coalesce only within the same actor/mode. The Worker rechecks current membership before collection and dispatch. MANUAL creates its own public source batch and report run, bound by request/mode/batch foreign keys. It uses current source-quality checks before claiming a model slot. Failed/uncertain requests are never automatically retried.

All scheduled, manual and debug model dispatches take the same transaction lock, check persistent model occupancy, insert the immutable run and create a guard atomically. Network/model calls hold no database connection. A normal model return proves that invocation completed; its owner can release the guard even when later report validation fails. Timeout/cancellation/crash does not prove completion: occupancy is retained without TTL-based takeover, and new requests wait. Operators must verify the old execution has stopped before releasing a stranded guard; observation windows alone are insufficient evidence.

## Reports and UI

The button reads “手动启动研究” and states that a new Codex invocation incurs usage. No extra confirmation dialog is required. Preserve queue/running/success/failure/uncertain states and the prior valid report until a new report validates. Keep unresolved command keys in actor-scoped session storage so a network retry/reload reuses the same command; only the matching acknowledgement clears its key, so a delayed old response cannot erase a newer unconfirmed intent. A 35-minute observation timeout does not enable another invocation.

Newer MANUAL executions supersede older scheduled pipeline state for the displayed execution, while the automatic schedule remains configured independently. Report provenance always follows the retained report's own mode/id, not the latest failed attempt. Request failure diagnostics refer only to that request's own report run.

## Verification

Use fixed/mock model output plus a disposable loopback PostgreSQL database. Cover gate consistency, revoked membership, idempotency, precise source identity, quality rejection, scheduled/manual isolation, simultaneous dispatch, occupancy after observation expiry, token-scoped release, diagnostics, retained reports and frontend retries. Run complete default backend/frontend suites plus Ruff, TypeScript, build, docs and migration checks. Validate and commit only related files locally.
