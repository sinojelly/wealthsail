"""Codex login preflight and optional one-file auth import; never runs a model."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from backend.integrations.codex_research import preflight_codex

MAX_AUTH_BYTES = 128 * 1024


def import_auth(content: bytes, directory: Path) -> None:
    """Import ChatGPT auth into an empty private runtime; do not overwrite refreshes."""
    if len(content) > MAX_AUTH_BYTES:
        raise ValueError("auth file exceeds the import limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate auth field")
            result[key] = value
        return result

    try:
        data = json.loads(content.decode("utf-8-sig"), object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid auth file") from exc
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("auth_mode") not in {None, "chatgpt"}
            or data.get("OPENAI_API_KEY") or not isinstance(tokens, dict)
            or not all(isinstance(tokens.get(name), str) and tokens[name]
                       for name in ("access_token", "refresh_token"))):
        raise ValueError("a ChatGPT login cache is required; prefer device login")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    destination = directory / "auth.json"
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "import-auth"), nargs="?", default="check")
    args = parser.parse_args(argv)
    try:
        if args.action == "import-auth":
            directory = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            import_auth(sys.stdin.buffer.read(MAX_AUTH_BYTES + 1), directory)
            print("ChatGPT login cache imported into the empty Codex runtime. Run check next.")
        else:
            result = asyncio.run(preflight_codex(os.environ.get("CODEX_PATH")))
            print(json.dumps({"ok": True, "version": result["version"],
                              "selectedModel": result.get("selectedModel")}, ensure_ascii=False))
    except Exception as exc:
        print(f"Codex {args.action} failed ({type(exc).__name__}); check CLI/login and runtime permissions.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
