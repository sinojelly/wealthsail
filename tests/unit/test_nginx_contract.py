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


def test_hashed_frontend_assets_are_compressed_without_buffering_chat() -> None:
    source = _config()
    assets = _location_body(source, "location ^~ /assets/")
    assert "gzip on;" in assets
    assert "gzip_vary on;" in assets
    assert "application/javascript" in assets
    assert "text/css" in assets
    assert "gzip on;" not in _location_body(source, "location = /api/v1/chat/stream")


def test_json_api_is_compressed_without_compressing_sse() -> None:
    source = _config()
    api = _location_body(source, "location /api/")
    assert "gzip on;" in api
    assert "gzip_vary on;" in api
    assert "gzip_proxied any;" in api
    assert "gzip_types application/json;" in api
    stream = _location_body(source, "location = /api/v1/chat/stream")
    assert "gzip on;" not in stream
    assert "proxy_buffering off;" in stream


def test_live_release_and_api_cache_headers_keep_server_security_headers() -> None:
    source = _config()
    controls = re.search(r"map \$uri \$wealthsail_cache_control\s*\{([^}]+)\}", source)
    assert controls
    for route in ("/index.html", "/build-info.json", "~^/api/"):
        assert re.search(rf"{re.escape(route)}\s+\"no-store\";", controls.group(1))
    assert "add_header Cache-Control $wealthsail_cache_control always;" in source
    for declaration in ("location = /api/v1/chat/stream", "location /api/"):
        body = _location_body(source, declaration)
        assert "proxy_hide_header Cache-Control;" in body
        assert not re.search(r"^\s*add_header ", body, re.MULTILINE)
    for declaration in ("location = /index.html", "location = /build-info.json"):
        assert "expires -1;" not in _location_body(source, declaration)
    assert "try_files $uri =404;" in _location_body(source, "location ^~ /assets/")
