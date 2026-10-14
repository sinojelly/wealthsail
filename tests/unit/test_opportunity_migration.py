"""Three-phase facts and personal monitoring require separate trust boundaries."""

from pathlib import Path


def test_079_has_immutable_market_facts_and_actor_private_monitoring():
    path = (
        Path(__file__).resolve().parents[2] / "migrations/079_opportunity_decisions.sql"
    )
    assert path.exists(), (
        "opportunity evidence and private monitoring migration missing"
    )
    sql = path.read_text(encoding="utf-8")
    for table in (
        "market.opportunity_series_observations",
        "reference.opportunity_source_permissions",
        "research.opportunity_strategy_validations",
        "risk.opportunity_condition_monitors",
        "risk.opportunity_decision_snapshots",
        "risk.opportunity_condition_notices",
    ):
        assert f"CREATE TABLE {table}" in sql
    assert "user_id=auth.uid()" in sql
    assert "identity.is_tenant_member(tenant_id)" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "UPDATE(enabled,enabled_at)" in sql
    assert "UPDATE(read_at)" in sql
    assert "CHINABOND" in sql and "CFETS" in sql and "UNKNOWN" in sql
    assert "GRANT INSERT ON reference.opportunity_source_permissions" not in sql
    assert "workflow.outbox" not in sql
    assert "canonical_hash)," not in sql
