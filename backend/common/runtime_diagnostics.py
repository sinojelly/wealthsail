"""Pure closed runtime metadata; imports grant no execution authority."""

import re

VERSION = "codex-diagnostic-v1"
ORIGINS = frozenset({"CLI", "RUNNER", "TRANSPORT", "ACCOUNT_RPC", "CLIENT"})
STAGES = frozenset(
    {
        "CLI_EXECUTION",
        "CLI_STDOUT",
        "CLI_STDERR",
        "CLI_OUTPUT",
        "RUNNER_HTTP",
        "REQUEST",
        "SPAWN",
        "INITIALIZE",
        "ACCOUNT_READ",
        "RATE_LIMITS_READ",
        "CLEANUP",
        "SNAPSHOT",
        "TRANSPORT",
        "CACHE_FETCH",
    }
)
REASON_CODES = frozenset(
    {
        "CLI_FAILED_UNCLASSIFIED",
        "AUTHENTICATION_FAILED",
        "ACCESS_DENIED",
        "RATE_LIMITED",
        "QUOTA_EXHAUSTED",
        "PROVIDER_UNAVAILABLE",
        "NETWORK_UNAVAILABLE",
        "TLS_ERROR",
        "CLI_OUTPUT_INVALID",
        "CLI_OUTPUT_LIMIT",
        "CLI_REPORT_MISSING",
        "CLI_MODEL_TIMEOUT",
        "RUNNER_HTTP_ERROR",
        "TRANSPORT_TIMEOUT",
        "TRANSPORT_DISCONNECTED",
        "ACCOUNT_RPC_FAILED",
        "ACCOUNT_RPC_TIMEOUT",
        "ACCOUNT_UNAVAILABLE",
        "ACCOUNT_UNAUTHENTICATED",
        "TRANSPORT_FAILED_UNCLASSIFIED",
        "RPC_RESPONSE_TIMEOUT",
        "WORKSPACE_ROUTING_TIMEOUT",
    }
)


def sanitize_diagnostic(value):
    if not isinstance(value, dict) or value.get("version") != VERSION:
        return None
    if any(
        not isinstance(value.get(key), str) or value[key] not in allowed
        for key, allowed in (
            ("origin", ORIGINS),
            ("stage", STAGES),
            ("reasonCode", REASON_CODES),
        )
    ):
        return None
    result = {key: value[key] for key in ("version", "origin", "stage", "reasonCode")}
    for key, low, high in (
        ("httpStatus", 100, 599),
        ("exitCode", -2147483648, 2147483647),
        ("rpcCode", -32768, -32000),
        ("elapsedMs", 0, 2_000_000_000),
    ):
        item = value.get(key)
        if type(item) is int and low <= item <= high:
            result[key] = item
    return result


def make_diagnostic(origin, stage, reason_code, **numbers):
    return sanitize_diagnostic(
        {
            "version": VERSION,
            "origin": origin,
            "stage": stage,
            "reasonCode": reason_code,
            **numbers,
        }
    )


def attach_diagnostic(error, diagnostic):
    safe = sanitize_diagnostic(diagnostic)
    if safe is not None:
        error.codex_diagnostic = safe
    return error


def preferred_diagnostic(*values):
    safe = [
        item for value in values if (item := sanitize_diagnostic(value)) is not None
    ]
    unknown = {
        "CLI_FAILED_UNCLASSIFIED",
        "ACCOUNT_RPC_FAILED",
        "ACCOUNT_UNAVAILABLE",
        "TRANSPORT_FAILED_UNCLASSIFIED",
    }
    return next(
        (item for item in safe if item["reasonCode"] not in unknown),
        safe[0] if safe else None,
    )


_CODES = {
    "insufficient_quota": "QUOTA_EXHAUSTED",
    "usage_limit_reached": "QUOTA_EXHAUSTED",
    "quota_exceeded": "QUOTA_EXHAUSTED",
    "rate_limit_exceeded": "RATE_LIMITED",
    "rate_limit_error": "RATE_LIMITED",
    "authentication_error": "AUTHENTICATION_FAILED",
    "invalid_api_key": "AUTHENTICATION_FAILED",
    "token_expired": "AUTHENTICATION_FAILED",
    "refresh_token_reused": "AUTHENTICATION_FAILED",
    "unauthorized": "AUTHENTICATION_FAILED",
    "server_error": "PROVIDER_UNAVAILABLE",
    "service_unavailable": "PROVIDER_UNAVAILABLE",
}
_HTTP = re.compile(
    r"\b(?:http(?:/\d(?:\.\d)?)?\s+|(?:http\s+)?status(?:\s+code)?\s*[:=]?\s*)([1-5]\d\d)\b",
    re.I,
)
_PATTERNS = (
    (
        "QUOTA_EXHAUSTED",
        r"\b(?:insufficient_quota|usage_limit_reached|quota_exceeded)\b|\byou(?:'ve| have) hit your usage limit\b",
    ),
    (
        "AUTHENTICATION_FAILED",
        r"\b(?:invalid_api_key|authentication_error|token_expired|refresh_token_reused)\b|\brefresh token (?:has expired|was already used)\b|\bauthentication token (?:is invalid|has expired)\b",
    ),
    (
        "RATE_LIMITED",
        r"\brate[_ ]limit(?:[_ ]exceeded|[_ ]error| reached)\b|\btoo many requests\b",
    ),
    (
        "TLS_ERROR",
        r"\bcertificate verify failed\b|\binvalid peer certificate\b|\bunknown issuer\b|\btls handshake (?:failed|failure)\b",
    ),
    (
        "NETWORK_UNAVAILABLE",
        r"\bconnection reset by peer\b|\bconnection refused\b|\bname or service not known\b|\bfailed to lookup address\b|\bdns (?:error|resolution failed)\b|\bstream disconnected before completion\b|\bsocket hang up\b",
    ),
)
_PATTERNS = tuple((code, re.compile(pattern, re.I)) for code, pattern in _PATTERNS)


def classify_failure(
    value, *, origin, stage, default="CLI_FAILED_UNCLASSIFIED", **numbers
):
    """Recognize explicit codes/statuses and narrow known messages, retaining none."""
    code, text, http = None, "", None
    if isinstance(value, dict):
        source = value.get("error") if isinstance(value.get("error"), dict) else value
        code = source.get("code")
        text = source.get("message") if isinstance(source.get("message"), str) else ""
        for key in ("status", "status_code", "httpStatus"):
            item = source.get(key)
            if type(item) is int and 100 <= item <= 599:
                http = item
                break
        if type(code) is int and -32768 <= code <= -32000:
            numbers["rpcCode"] = code
    elif isinstance(value, bytes):
        text = value[:8192].decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value[:8192]
    reason = _CODES.get(code) if isinstance(code, str) else None
    if reason is None and re.search(
        r"\bworkspace\s+routing\s+discovery\s+timed\s+out\b", text, re.I
    ):
        reason = "WORKSPACE_ROUTING_TIMEOUT"
    if (
        reason is None
        and "rpcCode" in numbers
        and re.search(r"\btimed out\b|\btimeout\b", text, re.I)
    ):
        reason = "RPC_RESPONSE_TIMEOUT"
    if http is None and (match := _HTTP.search(text)):
        http = int(match.group(1))
    if reason is None:
        reason = next(
            (kind for kind, pattern in _PATTERNS if pattern.search(text)), None
        )
    if reason is None and http is not None:
        reason = (
            "AUTHENTICATION_FAILED"
            if http == 401
            else "ACCESS_DENIED"
            if http == 403
            else "RATE_LIMITED"
            if http == 429
            else "PROVIDER_UNAVAILABLE"
            if http >= 500
            else None
        )
    if http is not None:
        numbers["httpStatus"] = http
    return make_diagnostic(origin, stage, reason or default, **numbers)


class StderrDiagnostic:
    """Bounded streaming classifier; only a closed result survives each line."""

    def __init__(self):
        self.pending = b""
        self.diagnostic = None

    def feed(self, chunk):
        if not isinstance(chunk, bytes):
            return
        for part in chunk.splitlines(keepends=True):
            self.pending = (self.pending + part)[-8192:]
            self._classify()
            if part.endswith((b"\n", b"\r")):
                self.pending = b""

    def _classify(self):
        safe = classify_failure(self.pending, origin="CLI", stage="CLI_STDERR")
        if safe["reasonCode"] != "CLI_FAILED_UNCLASSIFIED":
            self.diagnostic = safe

    def finish(self):
        self._classify()
        self.pending = b""
        return self.diagnostic


class SanitizedStderr(bytes):
    """Maintain the subprocess tuple's bytes contract without retaining stderr."""

    def __new__(cls, diagnostic):
        value = super().__new__(cls, b"")
        value.codex_diagnostic = sanitize_diagnostic(diagnostic)
        return value
