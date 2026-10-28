"""Bounded account metadata inspection, separate from inference observations.

The runner owns the shared cache. Explicit local CLI installations reuse this
service in-process; API/Worker never inspect the runner's authentication files.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
import os
from pathlib import Path
import time
import uuid

from backend.integrations.codex_private import safe_account_snapshot, unavailable_account

logger = logging.getLogger(__name__)


def account_diagnostic(stage, started, error=None, *, status=None):
    """Only fixed stages, status codes, classes and durations enter logs."""
    safe_status = status if isinstance(status, str) and status in {
        "READY", "STALE", "UNAVAILABLE", "UNAUTHENTICATED",
    } else "UNKNOWN"
    logger.info(
        "Codex account stage=%s error=%s elapsed_ms=%.1f status=%s",
        stage, type(error).__name__ if error is not None else "NONE",
        (time.monotonic() - started) * 1000,
        safe_status,
    )


def auth_file_generation(environment=None):
    """Detect login/logout/replacement using file metadata, never credentials."""
    source = os.environ if environment is None else environment
    home = source.get("CODEX_HOME")
    if not home:
        user_home = source.get("HOME") or source.get("USERPROFILE")
        home = Path(user_home).expanduser() / ".codex" if user_home else Path.home() / ".codex"
    try:
        stat = (Path(home).expanduser() / "auth.json").stat()
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    except OSError:
        return None


class AccountMetadataCache:
    """Merge metadata reads and preserve explicitly stale last-known information.

    Fresh quota captures intentionally bypass both the metadata cache and the
    shared in-flight request. Their failures never turn into stale quota values.
    """

    def __init__(self, fetch, *, generation, clock=time.monotonic, ttl=180, cooldown=20):
        self._fetch = fetch
        self._generation = generation
        self._clock = clock
        self._ttl = ttl
        self._cooldown = cooldown
        self._auth_stat = object()
        self._auth_generation = uuid.uuid4().hex
        self._auth_invalidated = False
        self._identity = None
        self._success = None
        self._partial = None
        self._success_until = 0
        self._failure = None
        self._retry_after = 0
        self._inflight = None
        self._sequence = 0
        self._committed_sequence = 0

    def _clear_identity(self):
        self._auth_generation = uuid.uuid4().hex
        self._identity = None
        self._success = self._failure = self._inflight = None
        self._partial = None
        self._success_until = self._retry_after = 0

    def _sync_auth(self):
        current = self._generation()
        if current != self._auth_stat:
            had_identity = self._identity is not None
            self._auth_stat = current
            self._clear_identity()
            if had_identity:
                self._auth_invalidated = True

    def _failure_result(self):
        if self._success is None:
            if self._failure and self._failure["status"] == "READY":
                return deepcopy(self._failure)
            if self._partial is None:
                return deepcopy(self._failure or unavailable_account())
        result = deepcopy(self._success or self._partial)
        result["status"] = "STALE"
        result["limitations"] = list(dict.fromkeys([*result["limitations"], "ACCOUNT_STALE"]))
        return result

    async def get(self, *, fresh=False, refresh=False):
        self._sync_auth()
        if fresh:
            return await self._capture(self._auth_generation, fresh=True)
        now = self._clock()
        if now < self._retry_after:
            return self._failure_result()
        if not refresh and self._success is not None and now < self._success_until:
            return deepcopy(self._success)
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.create_task(self._capture(self._auth_generation, fresh=False))
            self._inflight.add_done_callback(self._metadata_finished)
        # Closing one browser tab must not cancel a read shared by other clients.
        return deepcopy(await asyncio.shield(self._inflight))

    def _metadata_finished(self, task):
        if self._inflight is task:
            self._inflight = None

    async def _capture(self, auth_generation, *, fresh):
        self._sequence += 1
        sequence = self._sequence
        started = time.monotonic()
        try:
            result = safe_account_snapshot(await self._fetch())
        except Exception as error:
            account_diagnostic("cache_fetch", started, error)
            result = unavailable_account()
        else:
            account_diagnostic("cache_fetch", started, status=result["status"])
        self._sync_auth()
        if auth_generation != self._auth_generation:
            return unavailable_account("ACCOUNT_AUTH_CHANGED")
        if result["status"] == "READY":
            identity = (result.get("authType"), result.get("accountFingerprint"))
            if self._identity is not None and self._identity != identity:
                if sequence < self._committed_sequence:
                    return unavailable_account("ACCOUNT_AUTH_CHANGED")
                self._clear_identity()
            result.update(observationId=uuid.uuid4().hex, authGeneration=self._auth_generation)
            if sequence >= self._committed_sequence:
                self._committed_sequence = sequence
                self._auth_invalidated = False
                self._identity = identity
                complete = bool(result["windows"]) and "QUOTA_UNAVAILABLE" not in result["limitations"]
                if complete:
                    self._success = deepcopy(result)
                    self._partial = None
                    self._success_until = self._clock() + self._ttl
                    self._failure = None
                    self._retry_after = 0
                else:
                    # Knowing the account is not a successful quota capture.
                    # Preserve same-identity full history for the page, while
                    # inference callers receive only this new partial reading.
                    self._failure = deepcopy(result)
                    self._partial = deepcopy(result)
                    self._success_until = 0
                    self._retry_after = self._clock() + self._cooldown
                    if not fresh:
                        return self._failure_result()
            return result
        if sequence >= self._committed_sequence:
            self._committed_sequence = sequence
            if result["status"] == "UNAVAILABLE" and self._auth_invalidated:
                result = unavailable_account("ACCOUNT_AUTH_CHANGED")
            if result["status"] == "UNAUTHENTICATED":
                self._clear_identity()
            elif result["status"] == "STALE":
                # A lower-layer stale response is never a successful capture.
                result = unavailable_account()
            self._failure = result
            self._success_until = 0
            self._retry_after = self._clock() + self._cooldown
        return result if fresh else self._failure_result()


class RemoteAccountHistory:
    """Last-known transport fallback, with no second success TTL or cooldown.

    Runner remains authoritative on every request. Until it responds, the API
    cannot detect an external login change and may only show explicit history.
    """

    def __init__(self, key, *, invalidated=False):
        self.key = key
        self._active = True
        self._snapshot = None
        self._sequence = 0
        self._committed = 0
        self._identity_failure = unavailable_account("ACCOUNT_AUTH_CHANGED") if invalidated else None

    def invalidate(self):
        self._active = False
        self._snapshot = None
        self._identity_failure = unavailable_account("ACCOUNT_AUTH_CHANGED")

    def begin(self):
        self._sequence += 1
        return self._sequence

    def accept(self, sequence, result):
        if not self._active:
            return unavailable_account("ACCOUNT_AUTH_CHANGED")
        if sequence < self._committed:
            current = self._snapshot or {}
            if (
                result["status"] not in {"READY", "STALE"}
                or not current
                or result.get("authGeneration") != current.get("authGeneration")
                or result.get("accountFingerprint") != current.get("accountFingerprint")
            ):
                return unavailable_account("ACCOUNT_AUTH_CHANGED")
            return result
        self._committed = sequence
        known_invalid = result["status"] == "UNAUTHENTICATED" or "ACCOUNT_AUTH_CHANGED" in result["limitations"]
        if known_invalid:
            self._identity_failure = deepcopy(result)
        elif result["status"] == "READY":
            self._identity_failure = None
        elif self._identity_failure is not None:
            return deepcopy(self._identity_failure)
        # In particular, an explicit logout or a runner with no valid identity
        # must never fall back to a previous login's account information.
        self._snapshot = deepcopy(result) if result["status"] in {"READY", "STALE"} else None
        return result

    def failure(self, sequence):
        self._committed = max(sequence, self._committed)
        if self._identity_failure is not None:
            return deepcopy(self._identity_failure)
        if not self._active or self._snapshot is None:
            return unavailable_account()
        result = deepcopy(self._snapshot)
        result["status"] = "STALE"
        result["limitations"] = list(dict.fromkeys([*result["limitations"], "ACCOUNT_STALE"]))
        return result
