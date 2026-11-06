"""Safe execution identities and exit evidence require no model or database."""

from uuid import uuid4

import pytest


def identity():
    return {"runId": str(uuid4()), "ownerToken": str(uuid4()), "callId": str(uuid4())}


def test_exit_receipt_is_bound_to_all_three_identities():
    from backend.integrations.research_lifecycle import (
        ResearchLifecycle,
        sanitize_lifecycle,
    )

    owner = identity()
    state = ResearchLifecycle(owner)
    state.started()
    state.exited(reaped=True, descendants_confirmed=True, returncode=0)
    receipt = state.snapshot()
    assert sanitize_lifecycle(receipt, owner)["exitConfirmed"] is True
    for field in owner:
        wrong = {**owner, field: str(uuid4())}
        assert sanitize_lifecycle(receipt, wrong) is None
    assert owner["ownerToken"] not in str(receipt)


@pytest.mark.parametrize(
    "reaped,descendants", [(False, True), (True, False), (False, False)]
)
def test_partial_cleanup_never_proves_exit(reaped, descendants):
    from backend.integrations.research_lifecycle import (
        ResearchLifecycle,
        sanitize_lifecycle,
    )

    owner = identity()
    state = ResearchLifecycle(owner)
    state.started()
    state.exited(reaped=reaped, descendants_confirmed=descendants, returncode=0)
    assert sanitize_lifecycle(state.snapshot(), owner)["exitConfirmed"] is False


def test_deadline_layers_include_cleanup_and_transport_margin():
    from backend.integrations.research_lifecycle import research_deadlines

    budget = research_deadlines(900)
    assert budget["cliSeconds"] == 900
    assert budget["runnerSeconds"] >= 900 + budget["cleanupSeconds"] + 5
    assert budget["rpcSeconds"] >= budget["runnerSeconds"] + 15
    assert budget["workerSeconds"] > budget["rpcSeconds"]
    assert research_deadlines(1200)["cliSeconds"] == 1200
    with pytest.raises(ValueError):
        research_deadlines(1500)


def test_registry_rejects_duplicate_dispatch_and_restart_is_unknown():
    from backend.integrations.research_lifecycle import ResearchRegistry

    owner = identity()
    registry = ResearchRegistry()
    registry.create(owner)
    with pytest.raises(ValueError):
        registry.create(owner)
    with pytest.raises(ValueError):
        registry.create({**owner, "callId": str(uuid4())})
    assert registry.lookup(owner).snapshot()["state"] == "QUEUED"
    assert registry.lookup({**owner, "ownerToken": str(uuid4())}) is None
    assert ResearchRegistry().lookup(owner) is None


def test_evicted_terminal_status_never_reopens_same_owner_dispatch():
    from backend.integrations.research_lifecycle import ResearchRegistry

    owner = identity()
    registry = ResearchRegistry(retention_seconds=0)
    state = registry.create(owner)
    state.started()
    state.exited(reaped=True, descendants_confirmed=True, returncode=0)
    with pytest.raises(ValueError):
        registry.create({**owner, "callId": str(uuid4())})


def test_lifecycle_drops_unknown_diagnostics_and_malformed_exit_claims():
    from backend.integrations.research_lifecycle import (
        ResearchLifecycle,
        sanitize_lifecycle,
    )

    owner = identity()
    state = ResearchLifecycle(owner)
    dirty = {
        **state.snapshot(),
        "exitConfirmed": True,
        "reaped": True,
        "descendantsConfirmed": True,
        "prompt": "private",
        "completedAt": "private",
    }
    safe = sanitize_lifecycle(dirty, owner)
    assert safe["exitConfirmed"] is False
    assert "prompt" not in safe and "completedAt" not in safe
    assert sanitize_lifecycle({**state.snapshot(), "state": []}, owner) is None
    safe = sanitize_lifecycle({**state.snapshot(), "terminationReason": {}}, owner)
    assert safe["terminationReason"] is None
