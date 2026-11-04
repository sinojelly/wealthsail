"""A bounded JSON-only subprocess for deterministic offline validation.

This module has no job, provider, database or HTTP execution authority. The
child receives frozen evidence on stdin and emits only a bounded receipt.
"""

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys

TIMEOUT_SECONDS = 30
OUTPUT_LIMIT = 1024 * 1024
REPAIR_OUTPUT_LIMIT = 4 * 1024 * 1024
INPUT_LIMIT = 48 * 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class OfflineReplayError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def check_frozen_contract(packet):
    """Classify version support separately from integrity, without execution."""
    from backend.macro.research_topics import (
        FrozenDiscoveryCompatibilityError,
        frozen_manifest,
    )

    if not isinstance(packet, dict):
        raise OfflineReplayError("MATERIALS_INVALID")
    try:
        frozen_manifest(packet)
    except FrozenDiscoveryCompatibilityError:
        raise OfflineReplayError("INCOMPATIBLE") from None
    except (ValueError, TypeError):
        raise OfflineReplayError("MATERIALS_INVALID") from None


def child_environment():
    # In particular, do not inherit provider/DB credentials, PATH or PYTHONPATH.
    result = {
        name: os.environ[name]
        for name in ("SystemRoot", "WINDIR", "TEMP", "TMP")
        if name in os.environ
    }
    result.update(
        PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1"
    )
    from backend.macro.research_acceptance import build_revision

    if commit := build_revision():
        result["WEALTHSAIL_BUILD_COMMIT"] = commit
    return result


async def _read_output(reader, *, limit=OUTPUT_LIMIT):
    chunks, length = [], 0
    while chunk := await reader.read(65536):
        length += len(chunk)
        if length > limit:
            raise OfflineReplayError("OUTPUT_LIMIT")
        chunks.append(chunk)
    return b"".join(chunks)


async def _exchange(proc, payload, output_limit=OUTPUT_LIMIT):
    async def send():
        proc.stdin.write(payload)
        await proc.stdin.drain()
        proc.stdin.close()

    sending = asyncio.create_task(send())
    try:
        output = await _read_output(proc.stdout, limit=output_limit)
        await sending
        code = await proc.wait()
        if code:
            raise OfflineReplayError("VALIDATOR_FAILED")
        try:
            value = json.loads(output)
        except (ValueError, UnicodeError):
            raise OfflineReplayError("INVALID_OUTPUT") from None
        if not isinstance(value, dict):
            raise OfflineReplayError("INVALID_OUTPUT")
        if value.get("error"):
            code = value["error"]
            raise OfflineReplayError(
                code
                if code in ("INCOMPATIBLE", "MATERIALS_INVALID")
                else "VALIDATOR_FAILED"
            )
        return value
    finally:
        if not sending.done():
            sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)


async def _stop(proc):
    async def reap():
        async def drain():
            reader = getattr(proc, "stdout", None)
            if reader is not None:
                while await reader.read(65536):
                    continue
        result, _ = await asyncio.gather(proc.wait(), drain())
        return result

    reaping = asyncio.create_task(reap())
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            return await reaping
    try:
        return await asyncio.wait_for(asyncio.shield(reaping), 2)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            return await reaping
        return await reaping


async def run_offline_validation(payload, *, timeout=TIMEOUT_SECONDS):
    return await _run_child({**payload, "operation": "VALIDATE"}, timeout=timeout, output_limit=OUTPUT_LIMIT)


async def run_offline_repair(payload, *, timeout=TIMEOUT_SECONDS):
    return await _run_child({**payload, "operation": "REPAIR"}, timeout=timeout, output_limit=REPAIR_OUTPUT_LIMIT)


async def _run_child(payload, *, timeout=TIMEOUT_SECONDS, output_limit=OUTPUT_LIMIT):
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(encoded) > INPUT_LIMIT:
        raise OfflineReplayError("MATERIALS_INVALID")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "backend.macro.research_acceptance_replay",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(PROJECT_ROOT),
        env=child_environment(),
    )
    try:
        return await asyncio.wait_for(_exchange(proc, encoded, output_limit), timeout)
    except TimeoutError:
        await _stop(proc)
        raise OfflineReplayError("TIMEOUT") from None
    except asyncio.CancelledError:
        await _stop(proc)
        raise OfflineReplayError("CANCELLED") from None
    except (OfflineReplayError, OSError):
        await _stop(proc)
        raise


def _deny_external_execution(event, args):
    if event in {
        "socket.__new__",
        "socket.connect",
        "socket.getaddrinfo",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
    }:
        raise PermissionError("offline validation has no external execution authority")
    if (
        event == "import"
        and args
        and isinstance(args[0], str)
        and args[0].startswith(
            ("backend.jobs", "backend.integrations.codex", "backend.integrations.ai_")
        )
    ):
        raise ImportError("offline validation cannot import execution providers")


def main():
    sys.addaudithook(_deny_external_execution)
    try:
        data = sys.stdin.buffer.read(INPUT_LIMIT + 1)
        if len(data) > INPUT_LIMIT:
            raise ValueError("input limit")
        payload = json.loads(data)
        check_frozen_contract(payload["packet"])
        if payload.get("operation", "VALIDATE") == "REPAIR":
            from backend.macro.research_repair import repair_report
            value = repair_report(payload["raw"], payload["packet"], payload["sourceChecks"], research_id=payload["researchId"], validated_at=datetime.fromisoformat(payload["validatedAt"]))
            limit = REPAIR_OUTPUT_LIMIT
        elif payload.get("operation", "VALIDATE") == "VALIDATE":
            from backend.macro.research_acceptance import validate_report_outcome
            value = validate_report_outcome(payload["raw"], payload["packet"], payload["sourceChecks"], research_id=payload["researchId"], completed_at=datetime.fromisoformat(payload["validatedAt"])).to_dict()
            limit = OUTPUT_LIMIT
        else:
            raise ValueError("unknown offline operation")
        output = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(output) > limit:
            raise ValueError("output limit")
    except OfflineReplayError as error:
        code = (
            error.code
            if error.code in ("INCOMPATIBLE", "MATERIALS_INVALID")
            else "VALIDATOR_FAILED"
        )
        output = json.dumps({"error": code}).encode("utf-8")
    except Exception:
        # No exception repr, raw text, URLs, command keys or credentials leave the child.
        output = b'{"error":"VALIDATOR_FAILED"}'
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
