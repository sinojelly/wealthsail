"""Versioned model-only timing and safe terminal observations remain optional."""

from backend.integrations.research_execution import ExecutionObserver
from backend.macro.research_workload import measured_workload, safe_workload


def test_new_workload_labels_operation_only_timing_and_keeps_old_scope_unchanged():
    current = measured_workload(100, None, {}, model_ms=250)
    assert current["version"] == "research-workload-v2"
    assert current["modelElapsedScope"] == "MODEL_OPERATION"
    legacy = safe_workload({"version": "research-workload-v1", "modelElapsedMs": 250})
    assert legacy["version"] == "research-workload-v1"
    assert "modelElapsedScope" not in legacy


def test_terminal_workload_retains_only_safe_progress_and_repeat_statistics():
    observer = ExecutionObserver()
    observer.feed(b'{"type":"thread.started"}\n{"type":"turn.completed"}\n')
    value = measured_workload(
        100,
        observer.finish(),
        {},
        statistics=observer.statistics(),
        progress={**observer.progress(), "raw": "PRIVATE"},
    )
    assert value["executionStatistics"]["duplicateSearchQueries"] == 0
    assert value["executionProgress"]["modelCompleted"] is True
    assert "PRIVATE" not in str(value)
    assert "inputTokens" not in value


def test_partial_known_cached_tokens_are_not_changed_to_unknown_or_zero():
    value = measured_workload(100, None, {"cached_input_tokens": 12})
    assert value["cachedInputTokens"] == 12
    assert "inputTokens" not in value and "outputTokens" not in value
    invalid = measured_workload(
        100, None, {"prompt_tokens": 10, "cached_input_tokens": 12}
    )
    assert "cachedInputTokens" not in invalid


def test_malformed_optional_progress_and_statistics_cannot_reject_research():
    value = safe_workload(
        {
            "version": "research-workload-v2",
            "executionStatistics": {
                "version": "research-execution-stats-v1",
                "coverage": [],
            },
            "executionProgress": {"version": "research-progress-v1", "phase": []},
        }
    )
    assert value is not None
    assert "executionStatistics" not in value and "executionProgress" not in value


def test_model_execution_exposes_only_closed_termination_and_exit_metadata():
    from backend.integrations.research_lifecycle import ResearchLifecycle
    from uuid import uuid4

    lifecycle = ResearchLifecycle(
        {"runId": str(uuid4()), "ownerToken": str(uuid4()), "callId": str(uuid4())}
    )
    lifecycle.started()
    lifecycle.terminating("CLI_MODEL_TIMEOUT")
    lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=-9)
    value = measured_workload(
        100,
        None,
        {},
        lifecycle={**lifecycle.snapshot(), "raw": "PRIVATE"},
        dispatch_started=True,
    )
    observed = value["modelExecution"]
    assert observed["dispatchStarted"] is True
    assert observed["state"] == "EXITED" and observed["exitConfirmed"] is True
    assert observed["terminationRequested"] is True
    assert observed["terminationReason"] == "CLI_MODEL_TIMEOUT"
    assert observed["reaped"] is True and observed["descendantsConfirmed"] is True
    assert (
        not {"ownerToken", "identityBinding", "callId", "runId", "raw"}
        & observed.keys()
    )


def test_unknown_or_undispatched_execution_is_never_reported_as_confirmed_exit():
    unknown = measured_workload(100, None, {})["modelExecution"]
    assert unknown["state"] == "UNKNOWN" and unknown["dispatchStarted"] is None
    assert unknown["exitConfirmed"] is None
    undispatched = measured_workload(100, None, {}, dispatch_started=False)[
        "modelExecution"
    ]
    assert (
        undispatched["dispatchStarted"] is False
        and undispatched["exitConfirmed"] is not True
    )
    assert "modelExecution" not in safe_workload({"version": "research-workload-v1"})


def test_exit_projection_rejects_malformed_or_inconsistent_confirmation():
    value = safe_workload(
        {
            "version": "research-workload-v2",
            "modelExecution": {
                "state": [],
                "exitConfirmed": True,
                "reaped": True,
                "descendantsConfirmed": False,
                "terminationReason": "PRIVATE",
                "completedAt": "PRIVATE",
                "elapsedMs": -1,
            },
        }
    )["modelExecution"]
    assert value["state"] == "UNKNOWN" and value["exitConfirmed"] is False
    assert value["terminationReason"] is None and value["completedAt"] is None
    assert value["elapsedMs"] is None
    undispatched = measured_workload(
        100,
        None,
        {},
        lifecycle={
            "state": "EXITED",
            "reaped": True,
            "descendantsConfirmed": True,
            "exitConfirmed": True,
        },
        dispatch_started=False,
    )["modelExecution"]
    assert undispatched["state"] == "UNKNOWN"
    assert undispatched["exitConfirmed"] is not True
