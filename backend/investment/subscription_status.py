"""Pure subscription guard shared by collection and offline report validation."""

from decimal import Decimal, InvalidOperation


def normalize_subscription_status(value):
    """Project verified subscription facts and derive the small-limit guard."""
    if not isinstance(value, dict):
        return None
    purchase = value.get("purchaseStatus")
    if not isinstance(purchase, str) or purchase not in {
        "OPEN",
        "LIMITED",
        "SUSPENDED",
    }:
        return None
    raw_limit = value.get("dailyLimitCny")
    limit = None
    if not isinstance(raw_limit, bool) and isinstance(
        raw_limit, (str, int, float, Decimal)
    ):
        try:
            amount = Decimal(str(raw_limit))
            if amount.is_finite() and amount >= 0:
                limit = amount
        except (InvalidOperation, ValueError):
            pass
    label = (
        "⛔ 暂停申购"
        if purchase == "SUSPENDED"
        else f"⛔ 单日限购{format(limit.normalize(), 'f')}元"
        if limit is not None and limit <= 100
        else None
    )
    return {
        "purchaseStatus": purchase,
        "dailyLimitCny": format(limit.normalize(), "f") if limit is not None else None,
        "restricted": label is not None,
        "label": label,
        **({"retrievedAt": value["retrievedAt"]} if value.get("retrievedAt") else {}),
    }
