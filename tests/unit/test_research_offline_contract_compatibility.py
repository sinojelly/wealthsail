"""Unknown frozen contracts stay distinct from damaged materials offline."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from backend.errors import ValidationError_
from backend.macro import research_acceptance_replay as offline
from backend.macro import research_acceptance_repository as acceptance
from backend.macro import research_history_repair_repository as history
from backend.macro import research_repair
from backend.macro import research_topics
from tests.unit.test_research_acceptance_repository import NOW, RUN
from tests.unit.test_research_frozen_contract_projection import frozen

MESSAGE = "该冻结契约版本暂不受支持"


def future_packet():
    return {
        "asOf": NOW.isoformat(),
        "researchDiscovery": {"version": "PRIVATE_UNSUPPORTED_VERSION"},
    }


def artifacts(packet):
    values = (packet, "{}", {})
    return {
        "complete": True,
        "validatedAt": NOW.isoformat(),
        "items": [
            {"kind": kind, **acceptance.freeze_artifact(value, 1024 * 1024, raw=kind == "RAW_MODEL")}
            for kind, value in zip(acceptance.KINDS, values)
        ],
    }


def test_repair_entry_cannot_downgrade_unknown_discovery_to_v1():
    with pytest.raises(research_topics.FrozenDiscoveryCompatibilityError):
        research_repair.repair_report(
            {}, future_packet(), {}, research_id=str(RUN), validated_at=NOW
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["VALIDATE", "REPAIR"])
@pytest.mark.parametrize("damaged", [False, True])
async def test_real_child_distinguishes_unknown_contract_from_known_hash_damage(
    operation, damaged
):
    packet = frozen() if damaged else future_packet()
    if damaged:
        packet["researchDiscovery"]["auditManifest"]["manifestHash"] = "0" * 64
    runner = offline.run_offline_repair if operation == "REPAIR" else offline.run_offline_validation
    with pytest.raises(offline.OfflineReplayError) as error:
        await runner(
            {
                "raw": {},
                "packet": packet,
                "sourceChecks": {},
                "researchId": str(RUN),
                "validatedAt": NOW.isoformat(),
            }
        )
    assert error.value.code == ("MATERIALS_INVALID" if damaged else "INCOMPATIBLE")
    assert "PRIVATE" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["INCOMPATIBLE", "MATERIALS_INVALID", "PRIVATE_MODEL_TEXT"])
async def test_child_error_protocol_only_preserves_explicit_safe_codes(code):
    stdout = asyncio.StreamReader()
    stdout.feed_data(json.dumps({"error": code}).encode())
    stdout.feed_eof()
    proc = SimpleNamespace(
        stdin=SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock()),
        stdout=stdout,
        wait=AsyncMock(return_value=0),
    )
    with pytest.raises(offline.OfflineReplayError) as error:
        await offline._exchange(proc, b"{}")
    assert error.value.code == (code if code != "PRIVATE_MODEL_TEXT" else "VALIDATOR_FAILED")


@pytest.mark.asyncio
async def test_preview_explains_unsupported_contract_without_calling_child(monkeypatch):
    frozen_source = SimpleNamespace(packet=future_packet(), repair_payload=lambda: {})
    monkeypatch.setattr(history, "_rpc", AsyncMock(return_value=True))
    monkeypatch.setattr(history, "_source", AsyncMock(return_value=({}, frozen_source, NOW)))
    child = AsyncMock()
    monkeypatch.setattr(offline, "run_offline_repair", child)
    with pytest.raises(ValidationError_, match=MESSAGE):
        await history.preview_history_repair(AsyncMock(), RUN, uuid4(), uuid4(), NOW)
    child.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
async def test_history_generation_preserves_compatibility_reason_without_new_db_code(
    monkeypatch, retry
):
    attempt = uuid4()
    reserved = (
        {"status": "FAILED", "error_code": "VALIDATOR_FAILED"}
        if retry
        else {"id": str(attempt), "status": "RUNNING", "newAttempt": True}
    )
    rpc = AsyncMock(return_value=reserved)
    monkeypatch.setattr(history, "_rpc", rpc)
    frozen_source = SimpleNamespace(packet=future_packet(), repair_payload=lambda: {})
    monkeypatch.setattr(history, "_source", AsyncMock(return_value=({}, frozen_source, NOW)))
    child = AsyncMock()
    monkeypatch.setattr(offline, "run_offline_repair", child)

    result = await history.generate_history_repair(
        AsyncMock(), RUN, uuid4(), uuid4(), uuid4(), NOW
    )

    assert isinstance(result.error, ValidationError_) and str(result.error) == MESSAGE
    child.assert_not_awaited()
    if not retry:
        assert rpc.await_args.args[1:] == (
            "finish_history_repair_failure", attempt, "VALIDATOR_FAILED"
        )
    else:
        assert rpc.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
async def test_legacy_replay_preserves_compatibility_reason_without_new_db_code(
    monkeypatch, retry
):
    conn = AsyncMock()
    conn.transaction = Mock(return_value=AsyncMock())
    conn.fetchrow.side_effect = [None, {"run_id": RUN, "status": "FAILED", "error_code": "VALIDATOR_FAILED"}] if retry else [None, None, None]
    attempt = uuid4()
    conn.fetchval.side_effect = [{"id": str(attempt), "status": "RUNNING"}, True]
    monkeypatch.setattr(acceptance, "read_artifacts_admin", AsyncMock(return_value=artifacts(future_packet())))
    child = AsyncMock()
    monkeypatch.setattr(offline, "run_offline_validation", child)

    result = await acceptance.replay_validation(
        conn, RUN, uuid4(), uuid4(), uuid4(), NOW
    )

    assert isinstance(result.error, ValidationError_) and str(result.error) == MESSAGE
    child.assert_not_awaited()
    if not retry:
        assert conn.fetchval.await_args.args[1:] == (attempt, "FAILED", None, "VALIDATOR_FAILED")
    else:
        conn.fetchval.assert_not_awaited()


@pytest.mark.parametrize("code", ["INCOMPATIBLE", "PRIVATE_ERROR_TEXT"])
def test_cli_reports_only_allowlisted_compatibility_code(monkeypatch, capsys, code):
    from scripts import research_repair_preview as preview

    monkeypatch.setattr(
        "sys.argv", ["preview", "--bundle", "private-file.json", "--run-id", str(RUN)]
    )
    monkeypatch.setattr(
        preview, "preview_export", AsyncMock(side_effect=offline.OfflineReplayError(code))
    )
    assert preview.main() == 2
    result = json.loads(capsys.readouterr().out)
    if code == "INCOMPATIBLE":
        assert result["errorCode"] == "INCOMPATIBLE"
    else:
        assert "errorCode" not in result
    assert "PRIVATE" not in str(result) and "private-file" not in str(result)
