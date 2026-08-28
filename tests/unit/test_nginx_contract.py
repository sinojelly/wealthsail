"""Static contracts for the production Nginx reverse-proxy boundary."""

from __future__ import annotations

import re
from pathlib import Path


NGINX_CONFIG = Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf"


def _config() -> str:
    return NGINX_CONFIG.read_text(encoding="utf-8")


def _location_body(source: str, declaration: str) -> str:
    match = re.search(
        rf"^    {re.escape(declaration)}\s*\{{(?P<body>.*?)^    \}}",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing Nginx location: {declaration}"
    return match.group("body")


def test_chat_stream_has_an_exact_unbuffered_proxy_location() -> None:
    source = _config()
    body = _location_body(source, "location = /api/v1/chat/stream")

    assert "proxy_pass http://api:8000;" in body
    assert "proxy_http_version 1.1;" in body
    assert "proxy_buffering off;" in body
    assert "proxy_cache off;" in body
    assert "proxy_read_timeout 300s;" in body


def test_api_proxy_normalizes_forwarded_protocol_and_host() -> None:
    source = _config()
    assert "map $http_x_forwarded_proto $wealthsail_forwarded_proto" in source
    assert re.search(r"~\*\^https\$\s+https;", source)
    assert re.search(r"~\*\^http\$\s+http;", source)
    assert re.search(r"default\s+\$scheme;", source)

    for declaration in (
        "location = /api/v1/chat/stream",
        "location /api/",
    ):
        body = _location_body(source, declaration)
        assert "proxy_set_header Host $host;" in body
        assert "proxy_set_header X-Forwarded-Host $host;" in body
        assert (
            "proxy_set_header X-Forwarded-Proto $wealthsail_forwarded_proto;"
            in body
        )


def test_avatar_proxy_allows_only_generated_webp_object_names() -> None:
    source = _config()
    allowed = re.search(
        r'^    location ~ "\^/static/avatars/[^"\r\n]+\\\.webp\$" \{'
        r"(?P<body>.*?)^    \}",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert allowed is not None, "missing generated .webp avatar allowlist"
    assert "proxy_pass http://api:8000;" in allowed.group("body")

    denied = _location_body(source, "location /static/avatars/")
    assert "return 404;" in denied
    assert "location ^~ /static/avatars/" not in source
