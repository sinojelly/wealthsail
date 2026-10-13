import pytest


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres@127.0.0.1:55432/wealthsail_local",
        "postgresql://postgres@db.example:5432/wealthsail_investment_check_1",
        "postgresql://wealthsail_api@127.0.0.1/wealthsail_investment_check_1",
        "",
    ],
)
def test_smoke_seed_refuses_runtime_and_real_database_targets(dsn):
    from scripts.prepare_investment_evidence_smoke import validate_target

    with pytest.raises(ValueError):
        validate_target(dsn)


def test_smoke_seed_accepts_only_explicit_isolated_loopback_database():
    from scripts.prepare_investment_evidence_smoke import validate_target

    validate_target(
        "postgresql://postgres@127.0.0.1:55432/wealthsail_investment_check_example"
    )
