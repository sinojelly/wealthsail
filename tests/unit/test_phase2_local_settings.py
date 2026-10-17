"""Restarting the local supervisor preserves its scoped opportunity settings."""

from backend.config import Settings
from scripts import local_dev


def test_macro_configuration_preserves_operator_webhook_and_scope(tmp_path,monkeypatch):
    from backend.integrations import codex_research
    monkeypatch.setattr(local_dev,"LOCAL",tmp_path)
    monkeypatch.setattr(codex_research,"resolve_codex_executable",lambda *_:None)
    monkeypatch.setenv("WECOM_OPPORTUNITY_WEBHOOK_URL","https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fixture-only-key")
    monkeypatch.setenv("OPPORTUNITY_TENANT_ID","00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("OPPORTUNITY_USER_ID","00000000-0000-0000-0000-000000000002")
    local_dev.configure_macro({"worker_password":"fixture"},55434)
    first=Settings(_env_file=tmp_path/"macro.env")
    monkeypatch.delenv("WECOM_OPPORTUNITY_WEBHOOK_URL")
    monkeypatch.delenv("OPPORTUNITY_TENANT_ID")
    monkeypatch.delenv("OPPORTUNITY_USER_ID")
    local_dev.configure_macro({"worker_password":"fixture"},55434)
    second=Settings(_env_file=tmp_path/"macro.env")
    assert second.wecom_opportunity_webhook_url==first.wecom_opportunity_webhook_url
    assert second.opportunity_tenant_id=="00000000-0000-0000-0000-000000000001"
    assert second.opportunity_user_id=="00000000-0000-0000-0000-000000000002"


def test_only_macro_process_receives_group_sending_credentials():
    env={"WECOM_OPPORTUNITY_WEBHOOK_URL":"fixture-private","OPPORTUNITY_TENANT_ID":"tenant","OPPORTUNITY_USER_ID":"user","PATH":"keep"}
    for name in ("api","frontend"):
        actual=local_dev.service_environment(name,env)
        assert "WECOM_OPPORTUNITY_WEBHOOK_URL" not in actual
        assert actual["PATH"]=="keep"
    assert local_dev.service_environment("macro",env)["WECOM_OPPORTUNITY_WEBHOOK_URL"]=="fixture-private"
