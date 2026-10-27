"""Account inspection cache boundaries; these fakes never launch Codex."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from backend.integrations import codex_private


def ready(email="operator@example.test"):
    return codex_private.account_snapshot(
        {"account": {"type": "chatgpt", "email": email, "planType": "pro"}},
        {"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 1800000000}}},
    )


def partial(email="operator@example.test"):
    return codex_private.account_snapshot(
        {"account": {"type": "chatgpt", "email": email, "planType": "pro"}}, {},
    )


def cache(fetch, *, clock=None, generation=None):
    from backend.integrations.codex_account_cache import AccountMetadataCache

    return AccountMetadataCache(
        fetch, generation=generation or (lambda: "fixture-generation"),
        clock=clock or (lambda: 100.0),
    )


@pytest.mark.asyncio
async def test_success_is_cached_until_180_seconds_without_mutable_aliases():
    now = [100.0]
    fetch = AsyncMock(side_effect=[ready(), ready("new@example.test")])
    subject = cache(fetch, clock=lambda: now[0])
    first = await subject.get()
    first["windows"].clear()
    now[0] = 279.99
    cached = await subject.get()
    assert cached["email"] == "operator@example.test" and len(cached["windows"]) == 1
    assert cached["observationId"] == first["observationId"]
    assert fetch.await_count == 1
    now[0] = 280
    expired = await subject.get()
    assert expired["email"] == "new@example.test"
    assert expired["observationId"] != first["observationId"]
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_metadata_refreshes_merge_and_cancelled_waiter_does_not_cancel_read():
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        return ready()

    subject = cache(fetch)
    abandoned = asyncio.create_task(subject.get(refresh=True))
    await started.wait()
    waiting = asyncio.create_task(subject.get(refresh=True))
    await asyncio.sleep(0)
    abandoned.cancel()
    with pytest.raises(asyncio.CancelledError):
        await abandoned
    finish.set()
    result = await waiting
    assert calls == 1 and result["status"] == "READY"
    assert (await subject.get())["observationId"] == result["observationId"]


@pytest.mark.asyncio
async def test_failed_refresh_preserves_last_success_as_stale_and_obeys_cooldown():
    now = [100.0]
    fetch = AsyncMock(side_effect=[ready(), TimeoutError("private token"), ready()])
    subject = cache(fetch, clock=lambda: now[0])
    original = await subject.get()
    failure = await subject.get(refresh=True)
    assert failure["status"] == "STALE" and failure["observedAt"] == original["observedAt"]
    assert failure["windows"] == original["windows"]
    assert "ACCOUNT_STALE" in failure["limitations"]
    now[0] = 119.99
    assert (await subject.get(refresh=True))["status"] == "STALE"
    assert fetch.await_count == 2
    now[0] = 120
    assert (await subject.get())["status"] == "READY"
    assert fetch.await_count == 3


@pytest.mark.asyncio
async def test_first_failure_is_unavailable_and_throttles_retries():
    fetch = AsyncMock(return_value=codex_private.unavailable_account())
    subject = cache(fetch)
    assert (await subject.get())["status"] == "UNAVAILABLE"
    assert (await subject.get(refresh=True))["email"] is None
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_partial_quota_failure_keeps_complete_snapshot_stale_until_short_retry():
    now = [100.0]
    fetch = AsyncMock(side_effect=[ready(), partial(), ready()])
    subject = cache(fetch, clock=lambda: now[0])
    good = await subject.get()
    now[0] = 101
    stale = await subject.get(refresh=True)
    assert stale["status"] == "STALE" and stale["observedAt"] == good["observedAt"]
    assert stale["windows"] == good["windows"]
    assert stale["observationId"] == good["observationId"]
    assert "ACCOUNT_STALE" in stale["limitations"]
    now[0] = 120.99
    assert (await subject.get(refresh=True))["status"] == "STALE"
    assert fetch.await_count == 2
    now[0] = 121
    recovered = await subject.get()
    assert recovered["status"] == "READY" and recovered["observationId"] != good["observationId"]
    assert fetch.await_count == 3
    now[0] = 300.99
    assert (await subject.get())["observationId"] == recovered["observationId"]
    assert fetch.await_count == 3


@pytest.mark.asyncio
async def test_fresh_partial_quota_observation_never_uses_previous_windows():
    subject = cache(AsyncMock(side_effect=[ready(), partial()]))
    good = await subject.get()
    fresh = await subject.get(fresh=True)
    assert fresh["status"] == "READY" and fresh["windows"] == []
    assert "QUOTA_UNAVAILABLE" in fresh["limitations"]
    assert fresh["observationId"] != good["observationId"]
    assert fresh["authGeneration"] == good["authGeneration"]
    page = await subject.get()
    assert page["status"] == "STALE" and page["windows"] == good["windows"]


@pytest.mark.parametrize("new_email", ["new@example.test", None])
@pytest.mark.asyncio
async def test_partial_identity_change_or_missing_identity_clears_previous_account(new_email):
    subject = cache(AsyncMock(side_effect=[ready(), partial(new_email)]))
    old = await subject.get()
    new = await subject.get(refresh=True)
    cached = await subject.get()
    assert new["status"] == cached["status"] == "READY"
    assert new["email"] == cached["email"] == new_email
    assert new["windows"] == cached["windows"] == []
    assert new["authGeneration"] != old["authGeneration"]


@pytest.mark.asyncio
async def test_first_partial_metadata_retries_after_short_cooldown_instead_of_success_ttl():
    now = [100.0]
    fetch = AsyncMock(side_effect=[partial(), ready()])
    subject = cache(fetch, clock=lambda: now[0])
    initial = await subject.get()
    assert initial["email"] == "operator@example.test" and initial["windows"] == []
    now[0] = 119.99
    assert (await subject.get())["observationId"] == initial["observationId"]
    assert fetch.await_count == 1
    now[0] = 120
    complete = await subject.get()
    assert complete["status"] == "READY" and len(complete["windows"]) == 1
    assert complete["observationId"] != initial["observationId"]
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_hard_failure_after_partial_metadata_preserves_known_account_as_stale():
    now = [100.0]
    fetch = AsyncMock(side_effect=[partial(), codex_private.unavailable_account()])
    subject = cache(fetch, clock=lambda: now[0])
    initial = await subject.get()
    now[0] = 120
    failed = await subject.get()
    assert failed["status"] == "STALE" and failed["email"] == "operator@example.test"
    assert failed["observedAt"] == initial["observedAt"] and failed["windows"] == []
    assert "QUOTA_UNAVAILABLE" in failed["limitations"] and "ACCOUNT_STALE" in failed["limitations"]


@pytest.mark.asyncio
async def test_late_old_full_result_cannot_overwrite_a_newer_partial_identity():
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await finish.wait()
            return ready("old@example.test")
        return partial("new@example.test")

    subject = cache(fetch)
    pending = asyncio.create_task(subject.get())
    await started.wait()
    latest = await subject.get(fresh=True)
    finish.set()
    old = await pending
    cached = await subject.get()
    assert latest["email"] == cached["email"] == "new@example.test"
    assert old["status"] == "UNAVAILABLE" and old["email"] is None


@pytest.mark.asyncio
async def test_auth_generation_change_drops_cached_identity_even_when_next_read_fails():
    auth = ["first"]
    fetch = AsyncMock(side_effect=[ready(), codex_private.unavailable_account()])
    subject = cache(fetch, generation=lambda: auth[0])
    await subject.get()
    auth[0] = "second"
    result = await subject.get()
    assert result["status"] == "UNAVAILABLE" and result["email"] is None
    assert result["limitations"] == ["ACCOUNT_AUTH_CHANGED"]
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_known_auth_invalidation_remains_explicit_until_a_new_ready_observation():
    auth = ["first"]
    now = [100.0]
    fetch = AsyncMock(side_effect=[ready(), codex_private.unavailable_account(), codex_private.unavailable_account(), ready("new@example.test")])
    subject = cache(fetch, clock=lambda: now[0], generation=lambda: auth[0])
    await subject.get()
    auth[0] = "second"
    first_failure = await subject.get()
    assert first_failure["limitations"] == ["ACCOUNT_AUTH_CHANGED"]
    now[0] += 20
    retry_failure = await subject.get()
    assert retry_failure["limitations"] == ["ACCOUNT_AUTH_CHANGED"] and retry_failure["email"] is None
    now[0] += 20
    recovered = await subject.get()
    assert recovered["status"] == "READY" and recovered["email"] == "new@example.test"
    assert "ACCOUNT_AUTH_CHANGED" not in recovered["limitations"]


@pytest.mark.asyncio
async def test_logout_clears_cached_identity_and_relogin_has_different_auth_generation():
    signed_out = codex_private.unavailable_account()
    signed_out["status"] = "UNAUTHENTICATED"
    now = [100.0]
    fetch = AsyncMock(side_effect=[ready(), signed_out, ready("other@example.test")])
    subject = cache(fetch, clock=lambda: now[0])
    before = await subject.get()
    logout = await subject.get(refresh=True)
    assert logout["status"] == "UNAUTHENTICATED" and logout["email"] is None
    now[0] += 20
    after = await subject.get()
    assert after["email"] == "other@example.test"
    assert after["authGeneration"] != before["authGeneration"]


@pytest.mark.asyncio
async def test_auth_change_while_reading_discards_old_inflight_result():
    auth = ["first"]
    started, finish = asyncio.Event(), asyncio.Event()

    async def fetch():
        started.set()
        await finish.wait()
        return ready()

    subject = cache(fetch, generation=lambda: auth[0])
    pending = asyncio.create_task(subject.get())
    await started.wait()
    auth[0] = "second"
    finish.set()
    result = await pending
    assert result["status"] == "UNAVAILABLE" and result["email"] is None


@pytest.mark.asyncio
async def test_fresh_observations_never_share_metadata_inflight_or_cached_identity():
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await finish.wait()
        return ready()

    subject = cache(fetch)
    page = asyncio.create_task(subject.get())
    await started.wait()
    before, after = await subject.get(fresh=True), await subject.get(fresh=True)
    finish.set()
    page_result = await page
    assert calls == 3
    assert len({before["observationId"], after["observationId"], page_result["observationId"]}) == 3
    assert before["authGeneration"] == after["authGeneration"]


@pytest.mark.asyncio
async def test_fresh_failure_returns_unknown_instead_of_stale_or_cooldown_cache():
    fetch = AsyncMock(side_effect=[ready(), codex_private.unavailable_account(), ready()])
    subject = cache(fetch)
    await subject.get()
    assert (await subject.get(fresh=True))["status"] == "UNAVAILABLE"
    assert (await subject.get(fresh=True))["status"] == "READY"
    assert fetch.await_count == 3


@pytest.mark.asyncio
async def test_identity_switch_rotates_generation_without_auth_file_change():
    subject = cache(AsyncMock(side_effect=[ready(), ready("new@example.test")]))
    first = await subject.get()
    second = await subject.get(refresh=True)
    assert first["authGeneration"] != second["authGeneration"]
    assert (await subject.get())["email"] == "new@example.test"


@pytest.mark.asyncio
async def test_late_old_identity_cannot_invalidate_a_newer_fresh_identity():
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await finish.wait()
            return ready("old@example.test")
        return ready("new@example.test")

    subject = cache(fetch)
    old_request = asyncio.create_task(subject.get())
    await started.wait()
    latest = await subject.get(fresh=True)
    finish.set()
    old = await old_request
    cached = await subject.get()
    assert latest["email"] == cached["email"] == "new@example.test"
    assert latest["authGeneration"] == cached["authGeneration"]
    assert old["status"] == "UNAVAILABLE" and old["email"] is None


@pytest.mark.asyncio
async def test_cache_diagnostics_do_not_log_exception_message_or_identity(caplog):
    subject = cache(AsyncMock(side_effect=ValueError("private-token operator@example.test /auth.json")))
    with caplog.at_level("INFO"):
        assert (await subject.get())["status"] == "UNAVAILABLE"
    logs = caplog.text
    assert "stage=cache_fetch" in logs and "error=ValueError" in logs and "elapsed_ms=" in logs
    assert all(secret not in logs for secret in ("private-token", "operator@example.test", "/auth.json"))


def test_auth_generation_uses_only_file_stat_and_detects_replacement(tmp_path, monkeypatch):
    from backend.integrations.codex_account_cache import auth_file_generation

    auth = tmp_path / "auth.json"
    auth.write_text("first-private-token", encoding="utf-8")
    monkeypatch.setattr(type(auth), "read_text", lambda *_args, **_kwargs: pytest.fail("auth contents must not be read"))
    first = auth_file_generation({"CODEX_HOME": str(tmp_path)})
    replacement = tmp_path / "replacement"
    replacement.write_text("second-private-token", encoding="utf-8")
    replacement.replace(auth)
    second = auth_file_generation({"CODEX_HOME": str(tmp_path)})
    assert first != second
    assert "private-token" not in json.dumps([first, second])


def test_safe_snapshot_preserves_stale_timestamp_and_only_valid_opaque_metadata():
    value = ready()
    value.update(status="STALE", observationId="a" * 32, authGeneration="b" * 32)
    result = codex_private.safe_account_snapshot(value)
    assert result["status"] == "STALE" and result["observedAt"] == value["observedAt"]
    assert result["observationId"] == "a" * 32 and result["authGeneration"] == "b" * 32
    value.update(observationId="private-token", authGeneration="/auth.json")
    result = codex_private.safe_account_snapshot(value)
    assert "observationId" not in result and "authGeneration" not in result


@pytest.mark.parametrize("status", ["READY", "STALE"])
def test_safe_snapshot_rejects_invalid_auth_type_without_promoting_to_ready(status):
    value = ready()
    value.update(status=status, authType="invalid")
    assert codex_private.safe_account_snapshot(value)["status"] == "UNAVAILABLE"


def test_safe_snapshot_preserves_known_failure_reason_and_discards_arbitrary_codes():
    value = codex_private.unavailable_account("ACCOUNT_AUTH_CHANGED")
    value["limitations"].append("PRIVATE_TOKEN_SHOULD_NOT_PROPAGATE")
    result = codex_private.safe_account_snapshot(value)
    assert result["limitations"] == ["ACCOUNT_AUTH_CHANGED"]
