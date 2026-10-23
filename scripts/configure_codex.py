"""Add only Codex runner settings to an existing NAS install; keep identity keys."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.configure_nas import _env_text  # noqa: E402


def _values(content: bytes) -> dict[str, str]:
    result = {}
    for line in content.decode("utf-8").splitlines():
        match = re.match(r"^\s*(CODEX_RUNNER_URL|CODEX_RUNNER_TOKEN|CODEX_ENV_FILE)\s*=\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        if key in result:
            raise ValueError("duplicate Codex settings; check the existing files manually")
        if value.startswith(("'", '"')):
            value = ast.literal_eval(value)
        if not isinstance(value, str):
            raise ValueError("invalid Codex settings; check the existing files manually")
        result[key] = value
    return result


def add_codex_configuration(root: Path) -> dict[str, Path]:
    """Append a matching narrow runner token without rotating existing secrets."""
    paths = {
        "api": root / "deployment/env/api.env",
        "worker": root / "deployment/env/worker.env",
        "nas": root / "deployment/nas.env",
        "codex": root / "deployment/env/codex.env",
    }
    for key in ("api", "worker", "nas"):
        if not paths[key].is_file():
            raise ValueError("existing NAS configuration required; use configure_nas.py for a fresh install")
    original = {key: path.read_bytes() if path.exists() else None for key, path in paths.items()}
    existing = {key: _values(content or b"") for key, content in original.items()}
    tokens = {values["CODEX_RUNNER_TOKEN"] for values in existing.values() if values.get("CODEX_RUNNER_TOKEN")}
    if len(tokens) > 1:
        raise ValueError("Codex runner tokens disagree; existing credentials were retained")
    token = next(iter(tokens), secrets.token_hex(32))
    if len(token) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise ValueError("Codex runner token is invalid; existing credentials were retained")
    client = {"CODEX_RUNNER_URL": "http://codex-runner:8090", "CODEX_RUNNER_TOKEN": token}
    required = {
        "api": client,
        "worker": client,
        "nas": {"CODEX_ENV_FILE": "deployment/env/codex.env"},
        "codex": {"CODEX_RUNNER_TOKEN": token},
    }
    updates = {}
    for key, values in required.items():
        for name, value in values.items():
            if name in existing[key] and existing[key][name] != value:
                raise ValueError("custom Codex settings require manual review; files were retained")
        missing = {name: value for name, value in values.items() if name not in existing[key]}
        if original[key] is None and key == "codex":
            missing.update({
                "CODEX_PATH": "/usr/local/bin/codex", "CODEX_HOME": "/home/appuser/.codex",
                "CODEX_TIMEOUT_SECONDS": "90", "CODEX_CHAT_TIMEOUT_SECONDS": "900",
                "CODEX_RUNNER_MAX_CONCURRENT": "2",
            })
        if missing:
            prefix = original[key] or b""
            updates[key] = prefix + (b"\n" if prefix and not prefix.endswith(b"\n") else b"") + _env_text(missing).encode("utf-8")
    staged = {}
    applied = []
    try:
        # Prepare all private files before changing any original. Staging files
        # end in .env so both Git and image build ignore rules cover them.
        for key, content in updates.items():
            target = paths[key]
            staged[key] = target.parent / f".codex-setup-{secrets.token_hex(8)}.env"
            fd = os.open(staged[key], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
        for key in updates:
            os.replace(staged[key], paths[key])
            applied.append(key)
    except BaseException:
        for key in reversed(applied):
            if original[key] is None:
                paths[key].unlink(missing_ok=True)
            else:
                # Rollback reuses the same secure staging path.
                fd = os.open(staged[key], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(original[key])
                os.replace(staged[key], paths[key])
        raise
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
    return paths


def main() -> None:
    try:
        paths = add_codex_configuration(ROOT)
    except (ValueError, OSError, SyntaxError) as exc:
        # Do not include arbitrary malformed literal contents in diagnostics.
        raise SystemExit(f"Codex configuration stopped ({type(exc).__name__}); check existing configuration.") from None
    for name, path in paths.items():
        print(f"{name}: {path.relative_to(ROOT)}")
    print("Codex settings ready; existing database and identity secrets retained. Complete device login before enabling research.")


if __name__ == "__main__":
    main()
