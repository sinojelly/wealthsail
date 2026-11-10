"""An explicit report replay never invokes research or loses its original identity."""

from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import datetime
import json
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

RUN = UUID("ae71f283-b0db-46dc-a072-229714627476")
AT = datetime(2026, 11, 10, 7, 7, 46, tzinfo=ZoneInfo("Asia/Shanghai"))


def materials(tmp_path, **changes):
    raw = {"summary": "原始研究", "valueDirections": [{"key": "ai-hardware", "title": "AI硬件", "reviewIndustryIds": ["SEMICONDUCTOR_AI", "MEMORY"]}]}
    audit = {"runId": str(RUN), "completedAt": AT.isoformat(), "raw": raw, "sourceChecks": {}}
    audit.update(changes)
    (tmp_path / f"{RUN}.json").write_text(json.dumps(audit), encoding="utf-8")
    return SimpleNamespace(daily_research_audit_dir=str(tmp_path)), {
        "run_id": RUN, "public_packet": {"asOf": AT.isoformat()},
        "status": "FAILED", "failure_stage": "REPORT_VALIDATION",
        "recorded_at": AT, "packet_digest": "1" * 32,
    }


def test_materials_are_exact_run_and_keep_original_asof(tmp_path):
    from backend.macro.research_report_recovery import read_recovery_materials

    configured, record = materials(tmp_path)
    before = deepcopy(record)
    value = read_recovery_materials(configured, record)
    assert value["researchId"] == str(RUN)
    assert value["validatedAt"] == AT.isoformat()
    assert value["raw"]["valueDirections"][0]["reviewIndustryIds"] == ["SEMICONDUCTOR_AI", "MEMORY"]
    assert len(value["rawSha256"]) == 64
    assert record == before


def test_different_audit_run_cannot_be_published(tmp_path):
    from backend.macro.research_report_recovery import read_recovery_materials

    configured, record = materials(tmp_path, runId="00000000-0000-4000-8000-000000000001")
    with pytest.raises(ValueError, match="AUDIT_RUN_MISMATCH"):
        read_recovery_materials(configured, record)


@pytest.mark.asyncio
async def test_offline_replay_preserves_real_insights_without_any_provider():
    from backend.macro.research_acceptance_replay import run_offline_sanitization

    report = await run_offline_sanitization({
        "packet": {"asOf": AT.isoformat()}, "researchId": str(RUN),
        "raw": {"summary": "AI硬件研究", "valueDirections": [{"title": "AI硬件", "reviewIndustryIds": ["SEMICONDUCTOR_AI", "MEMORY"]}]},
        "sourceChecks": {}, "validatedAt": AT.isoformat(),
    })
    assert report["executionStatus"] == "COMPLETED"
    assert report["valueDirections"][0]["title"] == "AI硬件"
    assert report["valueDirections"][0]["auditStatus"] == "UNAUDITED_SCOPE"


@pytest.mark.asyncio
async def test_preview_does_not_write_and_publish_binds_material_hash(tmp_path, monkeypatch):
    from backend.macro import research_report_recovery as recovery

    configured, record = materials(tmp_path)
    calls = []

    class Connection:
        async def fetchrow(self, sql, *args):
            if "publish_sanitized" in sql:
                calls.append(args)
                return {"run_id": RUN, "published": True, "already_published": False}
            return record

    async def replay(payload):
        return {"executionStatus": "COMPLETED", "asOf": AT.isoformat(), "valueDirections": payload["raw"]["valueDirections"], "validationWarnings": []}

    async def enrich(conn, payload, materials, now):
        return payload

    monkeypatch.setattr(recovery, "run_offline_sanitization", replay)
    monkeypatch.setattr(recovery, "enrich_recovery_payload", enrich)
    now = AT.replace(hour=23)
    preview = await recovery.recover_report(Connection(), configured, RUN, now=now)
    assert preview["published"] is False
    assert preview["modelCalls"] == 0
    assert calls == []
    result = await recovery.recover_report(Connection(), configured, RUN, now=now, publish=True)
    assert result["published"] is True
    assert len(calls) == 1
    assert calls[0][0] == RUN
    assert calls[0][2] == "1" * 32
    assert len(calls[0][3]) == 64
    payload = json.loads(calls[0][1])
    assert payload["asOf"] == AT.isoformat()
    assert payload["researchRecovery"]["sourceRunId"] == str(RUN)
    assert payload["researchRecovery"]["modelCalls"] == 0


def test_recovery_times_keep_original_completion_separate_from_publication():
    from backend.macro.research_report_recovery import report_recovery_times

    published = AT.replace(day=11, hour=0)
    report = {"researchRecovery": {"version": "research-sanitize-replay-v1", "sourceRunId": str(RUN), "originalCompletedAt": AT.isoformat(), "replayedAt": published.isoformat(), "modelCalls": 0}}
    result = report_recovery_times(report, str(RUN), published)
    assert result["originalCompletedAt"] == AT
    assert result["replayedAt"] == published
    assert report_recovery_times(report, "another-run", published) is None


@pytest.mark.asyncio
async def test_historical_report_lists_and_previous_baseline_keep_original_completion():
    from backend.macro.daily_repository import read_previous_daily_research, read_recent_public_research

    published = AT.replace(day=11, hour=0)
    payload = {"researchRecovery": {"version": "research-sanitize-replay-v1", "sourceRunId": str(RUN), "originalCompletedAt": AT.isoformat(), "replayedAt": published.isoformat()}}
    row = {"run_id": RUN, "recorded_at": published, "slot_date": AT.date(), "payload": payload}

    class Connection:
        async def fetch(self, *args):
            return [row]

        async def fetchrow(self, *args):
            return row

    recent = await read_recent_public_research(Connection(), published)
    previous = await read_previous_daily_research(Connection(), published)
    assert recent[0]["completedAt"] == AT
    assert previous["reportCompletedAt"] == AT


@pytest.mark.asyncio
async def test_public_nav_refresh_keeps_successful_fund_when_other_source_and_write_fail(monkeypatch):
    from backend.jobs import fund_nav_sync
    from backend.macro.research_report_recovery import refresh_recovery_nav

    codes = ["000001", "000002", "000003"]
    report = {"mentionedFunds": [{"fundCode": code, "subjectKind": "nav", "subjectId": code, "market": "FUND_CN", "priceBasis": "UNIT_NAV", "unit": "CNY/份", "tracks": []} for code in codes]}
    saved = []

    class Connection:
        async def fetch(self, sql, values):
            assert values == codes
            return [{"fund_code": code} for code in codes]

        @asynccontextmanager
        async def transaction(self):
            yield

        async def execute(self, sql, code, acquired, payload):
            if code == "000002":
                raise RuntimeError("one cache write unavailable")
            saved.append(json.loads(payload))

    async def fetch(code, now):
        if code == "000001":
            raise TimeoutError("one public source unavailable")
        return {"fundCode": code, "priceBasis": "UNIT_NAV", "currency": "CNY", "unit": "CNY/份", "retrievedAt": AT.isoformat(), "availableAt": AT.isoformat(), "sourceUri": f"https://fund.eastmoney.com/{code}.html", "asOf": "2026-11-09", "points": [{"date": "2026-11-09", "close": "1.2500"}]}

    monkeypatch.setattr(fund_nav_sync, "fetch_on_demand_nav_history", fetch)
    result = await refresh_recovery_nav(Connection(), report, AT)
    assert result == {"requested": 3, "persisted": 1, "failed": 2}
    assert [row["fundCode"] for row in saved] == ["000003"]
    assert len(report["mentionedFunds"]) == 3
