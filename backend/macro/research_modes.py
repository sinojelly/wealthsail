"""Shared explicit-request gates; automatic scheduling is a separate opt-in."""


def research_request_mode_allowed(settings, mode):
    if not isinstance(mode, str):
        return False
    if mode == "MANUAL":
        return bool(getattr(settings, "daily_research_manual_enabled", False))
    if mode == "DEBUG":
        return (
            getattr(settings, "app_env", None) in {"development", "test"}
            and bool(getattr(settings, "daily_research_debug_enabled", False))
            and bool(getattr(settings, "daily_research_enabled", False))
        )
    return False


def preferred_research_request_mode(settings):
    return next(
        (
            mode
            for mode in ("MANUAL", "DEBUG")
            if research_request_mode_allowed(settings, mode)
        ),
        None,
    )
