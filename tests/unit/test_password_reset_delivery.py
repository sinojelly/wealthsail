from __future__ import annotations

from backend.config import Settings
from backend.integrations import email as email_adapter


def test_password_reset_url_uses_fragment_not_query(monkeypatch) -> None:
    captured = {}

    class FakeSMTP:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def starttls(self) -> None:
            pass

        def login(self, *_args) -> None:
            pass

        def send_message(self, message) -> None:
            captured["body"] = message.get_content()

    monkeypatch.setattr(email_adapter.smtplib, "SMTP", FakeSMTP)
    settings = Settings(
        _env_file=None,
        smtp_host="smtp.example.test",
        smtp_from="security@example.test",
        web_base_url="https://wealthsail.example.test",
    )

    import asyncio

    asyncio.run(
        email_adapter.send_password_reset_email(
            settings,
            recipient="user@example.test",
            token="secret/+ token",
        )
    )

    assert "?token=" not in captured["body"]
    assert "/login#reset-token=" in captured["body"]
    assert "secret/+ token" not in captured["body"]
