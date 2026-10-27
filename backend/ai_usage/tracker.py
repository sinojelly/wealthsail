"""Task-local inference observation, with best-effort independent persistence."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import inspect
import logging
from uuid import uuid4

import anyio

from backend.ai_usage import repository
from backend.ai_usage.calculation import tokens

logger = logging.getLogger(__name__)
_current = ContextVar("ai_usage_task", default=None)


def now():
    return datetime.now(timezone.utc)


def active_task():
    task = _current.get()
    return task if task is not None and not task.closed else None


def mark_business(status):
    task = active_task()
    if task:
        task.business_status = status


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
        try:
            with anyio.CancelScope(shield=True):
                async with asyncio.timeout(5):
                    await repository.persist(
                        self.pool, self.tenant_id, self.user_id, event, data
                    )
        except Exception as exc:
            logger.warning(
                "ai_usage_write_failed event=%s error_class=%s",
                event,
                type(exc).__name__,
            )

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


async def account_snapshot(settings):
    from backend.integrations import codex_client

    try:
        async with asyncio.timeout(8):
            raw = await codex_client.account(settings)
        # Never retain email, auth material or arbitrary provider output in history.
        return {
            key: raw.get(key)
            for key in (
                "accountFingerprint",
                "planType",
                "observedAt",
                "windows",
                "credits",
            )
        }
    except Exception:
        return {}


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
    await task.write("start_call", record)
    record.update(status="RUNNING", usage={}, before={})
    try:
        record["before"] = await account_snapshot(settings)
        yield record
    except BaseException as exc:
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
        with anyio.CancelScope(shield=True):
            record["after"] = await account_snapshot(settings)
        if record["status"] == "RUNNING":
            record["status"] = "UNCERTAIN"
        record["finishedAt"] = now()
        record["tokens"] = tokens(record.pop("usage", {}))
        await task.write("finish_call", record)


async def trace_inference(settings, *, model, call_type, operation):
    async with observe(settings, model, call_type) as record:
        result = await operation()
        if record is not None:
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
