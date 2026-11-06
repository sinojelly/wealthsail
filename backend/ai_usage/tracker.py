"""Task-local inference observation, with best-effort independent persistence."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import wraps
import inspect
import logging
from time import monotonic
from uuid import UUID, uuid4

import anyio

from backend.ai_usage import repository
from backend.ai_usage.calculation import stamp, tokens

logger = logging.getLogger(__name__)
_current = ContextVar("ai_usage_task", default=None)
_current_call = ContextVar("ai_usage_call", default=None)
_research_run = ContextVar("ai_usage_research_run", default=None)
PROGRESS_WRITE_INTERVAL_SECONDS = 10.0
PROGRESS_WRITE_TIMEOUT_SECONDS = 1.0
# One client call has a 25 second outer limit (HTTP 23s / Runner read 20s).
# Telemetry gets no retries, and its two independent reads share a finite budget.
SNAPSHOT_TIMEOUT_SECONDS = 26.0
SNAPSHOT_TOTAL_BUDGET_SECONDS = 52.0
_CLOCK_SKEW = timedelta(seconds=5)


class AccountSnapshotUnavailable(ValueError):
    """The independent metadata read did not return a ready account."""


class AccountSnapshotNotFresh(ValueError):
    """The metadata read cannot prove a new observation at this boundary."""


def _snapshot_diagnostic(reason, started):
    return {
        "reason": reason,
        "elapsedMs": max(0, round((monotonic() - started) * 1000)),
    }


def _failed_snapshot(reason, started):
    # Fixed diagnostic codes are the only failure material persisted in history.
    return {
        "status": "UNAVAILABLE",
        "diagnostic": _snapshot_diagnostic(reason, started),
    }


def _capture_research_progress(record, value):
    from backend.integrations.research_execution import sanitize_execution_progress

    if record["callType"] == "research" and record.get("researchRunId"):
        safe = sanitize_execution_progress(getattr(value, "execution_progress", None))
        if safe is not None:
            record["researchProgress"] = safe


def now():
    return datetime.now(timezone.utc)


def active_task():
    task = _current.get()
    return task if task is not None and not task.closed else None


def mark_business(status):
    task = active_task()
    if task:
        task.business_status = status


@contextmanager
def research_progress_run(run_id):
    """Bind telemetry to a server-selected run, without granting exit authority."""
    token = _research_run.set(str(UUID(str(run_id))))
    try:
        yield
    finally:
        _research_run.reset(token)


async def record_research_progress(run_id, progress):
    """Best-effort snapshots from an independent monitor, at most once per 10s.

    Callers await this only from their separate status-monitor task. A stalled
    database gets one second and no retry; progress never runs a model, changes
    its result, or provides process-exit evidence.
    """
    from backend.integrations.research_execution import sanitize_execution_progress

    try:
        identity = str(UUID(str(run_id)))
    except (ValueError, TypeError, AttributeError):
        return False
    context = _current_call.get()
    task = active_task()
    if (
        context is None
        or task is None
        or context[0] is not task
        or identity != _research_run.get()
    ):
        return False
    record = context[1]
    if (
        record["callType"] != "research"
        or record["status"] != "RUNNING"
        or record.get("researchRunId") != identity
    ):
        return False
    safe = sanitize_execution_progress(progress)
    if safe is None:
        return False
    started = monotonic()
    if (
        started - record.get("progressLastWrite", -float("inf"))
        < PROGRESS_WRITE_INTERVAL_SECONDS
    ):
        return False
    # Set before awaiting so concurrent status observations share the same cap.
    record["progressLastWrite"] = started
    record["researchRunId"], record["researchProgress"] = identity, safe
    try:
        async with asyncio.timeout(PROGRESS_WRITE_TIMEOUT_SECONDS):
            await repository.persist(
                task.pool,
                task.tenant_id,
                task.user_id,
                "call_progress",
                {
                    "id": record["id"],
                    "taskId": record["taskId"],
                    "runId": identity,
                    "progress": safe,
                },
            )
        return True
    except Exception as exc:  # noqa: BLE001 -- optional live telemetry cannot change inference
        logger.warning("ai_usage_progress_failed error_class=%s", type(exc).__name__)
        return False


_TRANSIENT_SQLSTATES = {
    "40001",  # serialization_failure
    "40P01",  # deadlock_detected
    "53300",  # too_many_connections
    "57P01",  # admin_shutdown
    "57P02",  # crash_shutdown
    "57P03",  # cannot_connect_now
    "08000",  # connection_exception
    "08003",  # connection_does_not_exist
    "08006",  # connection_failure
}


def _is_transient_error(exc: Exception) -> bool:
    """Determine whether an error encountered during usage write is transient and retryable.

    Args:
        exc: Exception raised during repository persistence.

    Returns:
        True if the exception indicates a transient network or lock failure, False otherwise.
    """
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    return str(sqlstate) in _TRANSIENT_SQLSTATES


class Task:
    def __init__(self, pool, settings, tenant_id, user_id, task_type, task_name):
        self.pool, self.settings = pool, settings
        self.tenant_id, self.user_id = tenant_id, user_id
        self.id = str(uuid4())
        self.task_type, self.task_name = task_type, task_name
        self.started = False
        self.closed = False
        self.business_status = "SUCCEEDED"
        self.lock = asyncio.Lock()

    async def write(self, event, data):
        """Persist an AI usage ledger event independently with bounded retry.

        Args:
            event: Name of the telemetry event ('start_task', 'start_call', 'finish_call', 'finish_task').
            data: Payload dictionary for the event.
        """
        max_attempts = 2 if event in {"finish_call", "finish_task"} else 1
        for attempt in range(max_attempts):
            try:
                with anyio.CancelScope(shield=True):
                    async with asyncio.timeout(5):
                        await repository.persist(
                            self.pool, self.tenant_id, self.user_id, event, data
                        )
                return
            except Exception as exc:
                sqlstate = getattr(exc, "sqlstate", None) or getattr(
                    exc, "pgcode", None
                )
                if attempt + 1 < max_attempts and _is_transient_error(exc):
                    logger.warning(
                        "ai_usage_write_retry event=%s attempt=%d error_class=%s sqlstate=%s",
                        event,
                        attempt + 1,
                        type(exc).__name__,
                        sqlstate,
                    )
                    await asyncio.sleep(0.5)
                    continue
                if sqlstate is not None:
                    logger.warning(
                        "ai_usage_write_failed event=%s error_class=%s sqlstate=%s",
                        event,
                        type(exc).__name__,
                        sqlstate,
                    )
                else:
                    logger.warning(
                        "ai_usage_write_failed event=%s error_class=%s",
                        event,
                        type(exc).__name__,
                    )
                break

    async def start(self):
        async with self.lock:
            if not self.started:
                self.started = True
                await self.write(
                    "start_task",
                    {
                        "id": self.id,
                        "taskType": self.task_type,
                        "taskName": self.task_name,
                        "startedAt": now(),
                    },
                )


@asynccontextmanager
async def usage_task(pool, settings, *, tenant_id, user_id, task_type, task_name):
    # Nested consumers (e.g. a shared brief) belong to the existing business task.
    if active_task() is not None:
        yield active_task()
        return
    task = Task(pool, settings, tenant_id, user_id, task_type, task_name)
    previous = _current.get()
    token = _current.set(task)
    error = None
    try:
        yield task
    except BaseException as exc:
        error = type(exc).__name__
        task.business_status = (
            "CANCELLED"
            if isinstance(exc, (asyncio.CancelledError, GeneratorExit))
            else "FAILED"
        )
        raise
    finally:
        task.closed = True
        try:
            _current.reset(token)
        except ValueError:
            # ASGI may close a suspended generator from its send/cleanup task.
            # A token belongs to its originating Context; never reset a foreign
            # task's context or skip the independently committed final record.
            if _current.get() is task:
                _current.set(previous)
        if task.started:
            await task.write(
                "finish_task",
                {
                    "id": task.id,
                    "finishedAt": now(),
                    "businessStatus": task.business_status,
                    "errorClass": error,
                },
            )


async def account_snapshot(settings, *, stage="account", timeout_seconds=None):
    from backend.integrations import codex_client

    started, requested_at = monotonic(), now()
    stage = stage if stage in {"before", "after"} else "account"
    timeout_seconds = min(
        SNAPSHOT_TIMEOUT_SECONDS,
        SNAPSHOT_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds,
    )
    try:
        if timeout_seconds <= 0:
            raise TimeoutError()
        async with asyncio.timeout(timeout_seconds):
            # Page metadata may be cached or STALE. Attribution always asks for
            # a new independent observation, including a separate App Server read.
            raw = await codex_client.account(settings, fresh=True)
        if raw.get("status") == "STALE":
            raise AccountSnapshotNotFresh()
        if raw.get("status") != "READY":
            raise AccountSnapshotUnavailable()
        observed_at = stamp(raw.get("observedAt"))
        if (
            not isinstance(raw.get("observationId"), str)
            or not raw["observationId"]
            or observed_at is None
            or observed_at < requested_at - _CLOCK_SKEW
            or observed_at > now() + _CLOCK_SKEW
        ):
            raise AccountSnapshotNotFresh()
        # Never retain email, auth material or arbitrary provider output in history.
        result = {
            key: raw.get(key)
            for key in (
                "status",
                "accountFingerprint",
                "authGeneration",
                "observationId",
                "planType",
                "observedAt",
                "windows",
                "credits",
            )
        }
        result["diagnostic"] = _snapshot_diagnostic(None, started)
        return result
    except asyncio.CancelledError:
        logger.info(
            "ai_usage_snapshot_cancelled stage=%s error_class=CancelledError elapsed_ms=%.0f",
            stage,
            (monotonic() - started) * 1000,
        )
        raise
    except Exception as exc:
        logger.warning(
            "ai_usage_snapshot_failed stage=%s error_class=%s elapsed_ms=%.0f",
            stage,
            type(exc).__name__,
            (monotonic() - started) * 1000,
        )
        reason = (
            "TIMEOUT"
            if isinstance(exc, TimeoutError)
            else "UNAVAILABLE"
            if isinstance(exc, AccountSnapshotUnavailable)
            else "NOT_FRESH"
            if isinstance(exc, AccountSnapshotNotFresh)
            else "ERROR"
        )
        return _failed_snapshot(reason, started)


@asynccontextmanager
async def observe(settings, model, call_type):
    task = active_task()
    if task is None:
        yield None
        return
    await task.start()
    record = {
        "id": str(uuid4()),
        "taskId": task.id,
        "model": model or "codex",
        "callType": call_type,
        "startedAt": now(),
    }
    if call_type == "research" and _research_run.get() is not None:
        record["researchRunId"] = _research_run.get()
    await task.write("start_call", record)
    record.update(status="RUNNING", usage={}, before={})
    call_token = _current_call.set((task, record))
    before_elapsed = 0.0
    try:
        snapshot_started = monotonic()
        try:
            record["before"] = await account_snapshot(settings, stage="before")
        except asyncio.CancelledError:
            record["before"] = _failed_snapshot("CANCELLED", snapshot_started)
            raise
        finally:
            before_elapsed = monotonic() - snapshot_started
        yield record
    except BaseException as exc:
        _capture_research_progress(record, exc)
        if not (isinstance(exc, GeneratorExit) and record["status"] == "SUCCEEDED"):
            task.business_status = (
                "CANCELLED"
                if isinstance(exc, (asyncio.CancelledError, GeneratorExit))
                else "FAILED"
            )
        if record["status"] != "SUCCEEDED":
            record.update(
                status="SUCCEEDED"
                if getattr(exc, "inference_succeeded", False)
                else "UNCERTAIN"
                if getattr(exc, "inference_uncertain", False)
                or isinstance(
                    exc, (TimeoutError, asyncio.CancelledError, GeneratorExit)
                )
                else "FAILED",
                usage=getattr(exc, "usage", {}),
                errorClass=type(exc).__name__,
                model=getattr(exc, "model", None) or record["model"],
            )
        raise
    finally:
        cancelled = None
        after_started = monotonic()
        try:
            with anyio.CancelScope(shield=True):
                record["after"] = await account_snapshot(
                    settings,
                    stage="after",
                    timeout_seconds=max(
                        0, SNAPSHOT_TOTAL_BUDGET_SECONDS - before_elapsed
                    ),
                )
        except asyncio.CancelledError as exc:
            # asyncio cancellation can interrupt an AnyIO shield. Commit the
            # inference evidence before passing cancellation to the business task.
            record["after"] = _failed_snapshot("CANCELLED", after_started)
            cancelled = exc
        if record["status"] == "RUNNING":
            record["status"] = "UNCERTAIN"
        record["finishedAt"] = now()
        record["tokens"] = tokens(record.pop("usage", {}))
        try:
            await task.write("finish_call", record)
        finally:
            try:
                _current_call.reset(call_token)
            except ValueError:
                if _current_call.get() == (task, record):
                    _current_call.set(None)
        if cancelled is not None:
            raise cancelled


async def trace_inference(settings, *, model, call_type, operation):
    async with observe(settings, model, call_type) as record:
        result = await operation()
        if record is not None:
            _capture_research_progress(record, result)
            record.update(
                status="SUCCEEDED",
                usage=result.get("usage", {})
                if call_type == "completion"
                else getattr(result, "usage", {}),
                model=(
                    result.get("model")
                    if call_type == "completion"
                    else getattr(result, "model", None)
                )
                or model
                or "codex",
            )
        return result


async def trace_stream(settings, *, model, operation_iterator):
    async with observe(settings, model, "chat") as record:
        try:
            async for event in operation_iterator:
                if record is not None and event.get("done"):
                    record.update(
                        status="SUCCEEDED",
                        usage=event.get("usage") or {},
                        model=event.get("model") or model or "codex",
                    )
                yield event
        finally:
            with anyio.CancelScope(shield=True):
                await operation_iterator.aclose()


def track_task(task_type, name):
    """Attach task identity at business boundaries while preserving their API."""

    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        async def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            ctx = bound.get("ctx")
            task_name = name(bound) if callable(name) else name
            async with usage_task(
                bound["pool"],
                bound["settings"],
                tenant_id=ctx.tenant_id if ctx else None,
                user_id=ctx.user_id if ctx else None,
                task_type=task_type,
                task_name=task_name,
            ):
                result = await function(*args, **kwargs)
                if isinstance(result, dict) and result.get("status") in {
                    "FAILED",
                    "UNCERTAIN",
                }:
                    mark_business("FAILED")
                return result

        return wrapped

    return decorate
