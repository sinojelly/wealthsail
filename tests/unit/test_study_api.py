"""The study API exposes actor-scoped journals, never executions."""

from importlib import import_module, util


def test_study_api_has_explicit_commands_and_no_delete_or_trade_execution():
    assert util.find_spec("backend.investment.study_api"), "study API missing"
    router = import_module("backend.investment.study_api").router
    routes = {
        (route.path, method) for route in router.routes for method in route.methods
    }
    base = "/investment/study-tracks"
    for suffix, method in [
        ("", "GET"),
        ("", "POST"),
        ("/{track_id}", "GET"),
        ("/{track_id}/versions", "POST"),
        ("/{track_id}/status", "POST"),
        ("/{track_id}/check", "POST"),
        ("/{track_id}/notes", "POST"),
        ("/{track_id}/trade-links", "POST"),
        ("/{track_id}/eligible-trades", "GET"),
    ]:
        assert (base + suffix, method) in routes
    assert not any(method in {"DELETE", "PUT"} for _, method in routes)
