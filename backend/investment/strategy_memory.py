"""Actor-owned review counts; public memory contains templates, never prose."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import json
import re
from zoneinfo import ZoneInfo


THEME_CODES = frozenset(
    {
        "HAINAN_FTP",
        "PUDONG_DEV",
        "GEO_CRUDE_OIL",
        "AI_MEMORY_HBM",
        "COMMERCIAL_SPACE",
        "UNCLASSIFIED",
    }
)
CATEGORY_LABELS = {
    "BASELINE": "整体研究判断",
    "FACT": "事实核验",
    "TRANSMISSION": "传导路径",
    "EXPECTATIONS": "预期差",
    "INSTRUMENT": "标的选择",
    "TIMING": "时点判断",
    "RISK": "风险约束",
    "DISCIPLINE": "执行纪律",
}
_VERDICTS = {
    "SUPPORTED": "supported_count",
    "MIXED": "mixed_count",
    "REFUTED": "refuted_count",
    "INCONCLUSIVE": "inconclusive_count",
}
_COUNTS = tuple(_VERDICTS.values())
_PROFIT_COUNTS = ("profit_win_count", "profit_loss_count", "profit_flat_count")


def _decimal(value):
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def realized_roundtrip(rows, now):
    """Known net cash only for a fully closed, currently verified linked track.

    DAY precision never proves same-day ordering. Cash is rounded per ledger leg,
    and version links do not turn one real fill into multiple executions.
    """
    now = _clock(now)
    trade_today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    unique = {}
    signature_keys = (
        "account_id",
        "instrument_id",
        "currency",
        "side",
        "quantity",
        "unit_price",
        "fee",
        "trade_date",
        "confirmed_at",
        "status",
        "ledger_event_id",
        "ledger_verified",
        "access_verified",
        "shared_track_count",
    )
    for row in rows:
        key = str(row.get("fill_id") or "")
        signature = tuple(str(row.get(field)) for field in signature_keys)
        if not key or (key in unique and unique[key][0] != signature):
            return None
        unique[key] = signature, row
    buy, sell = [], []
    identity = None
    with localcontext() as context:
        context.prec = 80
        for _, row in unique.values():
            confirmed = _stamp(row.get("confirmed_at"))
            traded = row.get("trade_date")
            if isinstance(traded, str):
                try:
                    traded = date.fromisoformat(traded)
                except ValueError:
                    return None
            currency = row.get("currency")
            quantity, price, fee = (
                _decimal(row.get(key)) for key in ("quantity", "unit_price", "fee")
            )
            current = (
                str(row.get("account_id") or ""),
                str(row.get("instrument_id") or ""),
                currency,
            )
            if (
                row.get("status") != "CONFIRMED"
                or not row.get("ledger_event_id")
                or row.get("ledger_verified") is not True
                or row.get("access_verified") is not True
                or row.get("shared_track_count") != 1
                or confirmed is None
                or confirmed > now
                or type(traded) is not date
                or traded > trade_today
                or quantity is None
                or quantity <= 0
                or price is None
                or price <= 0
                or fee is None
                or fee < 0
                or not isinstance(currency, str)
                or not re.fullmatch(r"[A-Z]{3}", currency)
                or not current[0]
                or not current[1]
                or (identity is not None and identity != current)
            ):
                return None
            identity = current
            gross = (quantity * price).quantize(
                Decimal("0.00000001"), rounding=ROUND_HALF_UP
            )
            item = (traded, quantity, gross, fee)
            if row.get("side") == "BUY":
                buy.append(item)
            elif row.get("side") == "SELL":
                sell.append(item)
            else:
                return None
        if (
            not buy
            or not sell
            or max(item[0] for item in buy) >= min(item[0] for item in sell)
            or sum(item[1] for item in buy) != sum(item[1] for item in sell)
        ):
            return None
        return sum(item[2] - item[3] for item in sell) - sum(
            item[2] + item[3] for item in buy
        )


def _clock(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("memory requires an aware clock")
    return value


def _stamp(value):
    try:
        return _clock(
            datetime.fromisoformat(value) if isinstance(value, str) else value
        )
    except (ValueError, TypeError):
        return None


def theme_for_snapshot(snapshot):
    """Only a frozen, enumerated scope or program plan can identify a theme."""
    for field in ("scope", "actionablePlan"):
        item = snapshot.get(field) or {}
        code = item.get("themeCode") if isinstance(item, dict) else None
        if code in THEME_CODES:
            return code
    return "UNCLASSIFIED"


def _rate(counts):
    denominator = counts["supported_count"] + counts["refuted_count"]
    return Decimal(counts["supported_count"]) / denominator if denominator else None


def _rule_text(category, counts):
    return (
        f"{CATEGORY_LABELS[category]}：{counts['sample_count']}个独立研究复盘；"
        f"支持{counts['supported_count']}、混合{counts['mixed_count']}、"
        f"反驳{counts['refuted_count']}、无法判断{counts['inconclusive_count']}。"
        "支持率仅描述研究判断，不代表盈利胜率。"
        f"已核验关联成交完整闭环{counts.get('profit_sample_count', 0)}个："
        f"盈利{counts.get('profit_win_count', 0)}、亏损{counts.get('profit_loss_count', 0)}、"
        f"持平{counts.get('profit_flat_count', 0)}；仅描述关联成交，不证明策略因果。"
    )


def aggregate_reviews(rows, tenant_id, user_id, theme_code, now, *, fills=()):
    """One latest REVIEW per track; a missing lesson removes prior contribution."""
    now = _clock(now)
    theme_code = theme_code if theme_code in THEME_CODES else "UNCLASSIFIED"
    latest = {}
    by_track, fill_tracks = {}, {}
    for fill in fills:
        if str(fill.get("tenant_id")) != str(tenant_id) or str(
            fill.get("user_id")
        ) != str(user_id):
            continue
        track = str(fill.get("track_id") or "")
        by_track.setdefault(track, []).append(fill)
        fill_tracks.setdefault(str(fill.get("fill_id") or ""), set()).add(track)
    for row in rows:
        stamp = _stamp(row.get("created_at"))
        if (
            str(row.get("tenant_id")) != str(tenant_id)
            or str(row.get("user_id")) != str(user_id)
            or not row.get("track_id")
            or stamp is None
            or stamp > now
        ):
            continue
        key = str(row["track_id"])
        order = (stamp, str(row.get("id") or ""))
        if key not in latest or order > latest[key][0]:
            latest[key] = order, row
    rules = {
        category: {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "theme_code": theme_code,
            "category": category,
            "sample_count": 0,
            **{key: 0 for key in _COUNTS},
            "profit_sample_count": 0,
            **{key: 0 for key in _PROFIT_COUNTS},
            "updated_at": now,
        }
        for category in CATEGORY_LABELS
    }
    for _, row in latest.values():
        raw_theme = row.get("theme_code")
        row_theme = raw_theme if raw_theme in THEME_CODES else "UNCLASSIFIED"
        if (
            row_theme != theme_code
            or row.get("verdict") not in _VERDICTS
            or row.get("has_lessons") is not True
        ):
            continue
        categories = row.get("error_categories") or []
        if isinstance(categories, str):
            try:
                categories = json.loads(categories)
            except ValueError:
                categories = []
        categories = categories if isinstance(categories, list) else []
        selected = {"BASELINE"} | {
            category
            for category in categories
            if isinstance(category, str) and category in CATEGORY_LABELS
        }
        track_fills = by_track.get(str(row["track_id"]), [])
        profit = (
            realized_roundtrip(track_fills, now)
            if all(
                len(fill_tracks[str(fill.get("fill_id") or "")]) == 1
                for fill in track_fills
            )
            else None
        )
        for category in selected:
            counts = rules[category]
            counts["sample_count"] += 1
            counts[_VERDICTS[row["verdict"]]] += 1
            if profit is not None:
                counts["profit_sample_count"] += 1
                counts[
                    "profit_win_count"
                    if profit > 0
                    else "profit_loss_count"
                    if profit < 0
                    else "profit_flat_count"
                ] += 1
    for row in rules.values():
        row["support_rate"] = _rate(row)
        row["win_rate"] = (
            Decimal(row["profit_win_count"]) / row["profit_sample_count"]
            if row["profit_sample_count"]
            else None
        )
        row["rule_text"] = _rule_text(row["category"], row)
    return list(rules.values())


async def _review_evidence(conn, tenant_id, user_id, now):
    return await conn.fetch(
        """SELECT DISTINCT ON (n.track_id)
          n.id,n.tenant_id,n.user_id,n.track_id,n.version_id,n.created_at,
          COALESCE(v.snapshot->'scope'->>'themeCode',
            v.snapshot->'actionablePlan'->>'themeCode','UNCLASSIFIED') AS theme_code,
          n.payload->>'verdict' AS verdict,
          n.payload->'errorCategories' AS error_categories,
          COALESCE(n.payload->>'lessons','') ~ '[^[:space:]]' AS has_lessons
        FROM research.study_notes n
        JOIN research.study_versions v ON v.tenant_id=n.tenant_id
          AND v.user_id=n.user_id AND v.track_id=n.track_id AND v.id=n.version_id
        WHERE n.tenant_id=$1 AND n.user_id=$2 AND n.kind='REVIEW'
          AND n.created_at<=$3 AND v.created_at<=$3
          AND EXISTS(SELECT 1 FROM identity.tenant_members m
            WHERE m.tenant_id=$1 AND m.user_id=$2 AND m.status='ACTIVE')
        ORDER BY n.track_id,n.created_at DESC,n.id DESC""",
        tenant_id,
        user_id,
        now,
    )


async def _linked_fill_evidence(conn, tenant_id, user_id, now):
    # Keep a missing/inaccessible fill as an unverified row: dropping one side
    # must never turn a partially visible track into a smaller apparent roundtrip.
    return await conn.fetch(
        """SELECT l.tenant_id,l.user_id,l.track_id,l.version_id,l.fill_id,
          f.account_id,f.instrument_id,i.trading_currency AS currency,
          f.side,f.quantity,f.unit_price,f.fee,f.trade_date,f.confirmed_at,
          f.status,f.ledger_event_id,(f.id IS NOT NULL) AS access_verified,
          (SELECT count(DISTINCT peer.track_id) FROM research.study_trade_links peer
            WHERE peer.tenant_id=$1 AND peer.user_id=$2 AND peer.fill_id=l.fill_id
              AND peer.linked_at<=$3) AS shared_track_count,
          COALESCE(e.status='CONFIRMED' AND e.event_type=f.side
            AND e.trade_date=f.trade_date AND e.account_id=f.account_id
            AND f.created_at<=$3 AND e.created_at<=$3 AND e.reverses_event_id IS NULL
            AND NOT EXISTS(SELECT 1 FROM ledger.ledger_events reversal
              WHERE reversal.tenant_id=$1 AND reversal.reverses_event_id=e.id
                AND reversal.status='CONFIRMED' AND reversal.created_at<=$3)
            AND legs.leg_count=2+CASE WHEN f.fee>0 THEN 1 ELSE 0 END
            AND legs.instrument_count=1 AND legs.cash_count=1
            AND legs.fee_count=CASE WHEN f.fee>0 THEN 1 ELSE 0 END
            AND legs.valid, FALSE) AS ledger_verified
        FROM research.study_trade_links l
        JOIN research.study_versions v ON v.tenant_id=l.tenant_id
          AND v.user_id=l.user_id AND v.track_id=l.track_id AND v.id=l.version_id
          AND v.created_at<=$3
        LEFT JOIN ledger.trade_fills f ON f.tenant_id=l.tenant_id AND f.id=l.fill_id
          AND EXISTS(SELECT 1 FROM identity.accounts account
            JOIN identity.account_members owner ON owner.tenant_id=account.tenant_id
              AND owner.account_id=account.id AND owner.user_id=$2
              AND owner.relationship IN ('BENEFICIAL_OWNER','JOINT_OWNER')
              AND owner.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
              AND (owner.effective_to IS NULL OR owner.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date)
            WHERE account.tenant_id=$1 AND account.id=f.account_id AND account.status='ACTIVE')
        LEFT JOIN reference.instruments i ON i.id=f.instrument_id
        LEFT JOIN ledger.ledger_events e ON e.tenant_id=$1 AND e.id=f.ledger_event_id
        LEFT JOIN LATERAL (
          SELECT count(*) AS leg_count,
            count(*) FILTER(WHERE leg.leg_type='INSTRUMENT') AS instrument_count,
            count(*) FILTER(WHERE leg.leg_type='CASH') AS cash_count,
            count(*) FILTER(WHERE leg.leg_type='FEE') AS fee_count,
            bool_and(COALESCE(CASE leg.leg_type
              WHEN 'INSTRUMENT' THEN leg.instrument_id=f.instrument_id
                AND leg.quantity=CASE WHEN f.side='BUY' THEN f.quantity ELSE -f.quantity END
                AND leg.unit_price=f.unit_price
              WHEN 'CASH' THEN leg.currency=i.trading_currency
                AND leg.amount=round(CASE WHEN f.side='BUY' THEN -f.quantity*f.unit_price ELSE f.quantity*f.unit_price END,8)
              WHEN 'FEE' THEN leg.currency=i.trading_currency AND leg.amount=-f.fee
              ELSE FALSE END AND leg.created_at<=$3,FALSE)) AS valid
          FROM ledger.ledger_legs leg WHERE leg.tenant_id=$1 AND leg.event_id=e.id
        ) legs ON TRUE
        WHERE l.tenant_id=$1 AND l.user_id=$2 AND l.linked_at<=$3
          AND EXISTS(SELECT 1 FROM identity.tenant_members m
            WHERE m.tenant_id=$1 AND m.user_id=$2 AND m.status='ACTIVE')""",
        tenant_id,
        user_id,
        now,
    )


async def refresh_strategy_memory(conn, tenant_id, user_id, theme_code, now):
    """Recompute in the caller's REVIEW/link transaction; errors roll it back."""
    now = _clock(now)
    theme_code = theme_code if theme_code in THEME_CODES else "UNCLASSIFIED"
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        f"strategy-memory:{tenant_id}:{user_id}",
    )
    rows = await _review_evidence(conn, tenant_id, user_id, now)
    fills = await _linked_fill_evidence(conn, tenant_id, user_id, now)
    rules = aggregate_reviews(rows, tenant_id, user_id, theme_code, now, fills=fills)
    await conn.executemany(
        """INSERT INTO research.strategy_memory_rules
        (tenant_id,user_id,theme_code,category,rule_text,sample_count,
          supported_count,mixed_count,refuted_count,inconclusive_count,support_rate,updated_at,
          profit_sample_count,profit_win_count,profit_loss_count,profit_flat_count,win_rate)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT(tenant_id,user_id,theme_code,category) DO UPDATE SET
          rule_text=EXCLUDED.rule_text,sample_count=EXCLUDED.sample_count,
          supported_count=EXCLUDED.supported_count,mixed_count=EXCLUDED.mixed_count,
          refuted_count=EXCLUDED.refuted_count,inconclusive_count=EXCLUDED.inconclusive_count,
          support_rate=EXCLUDED.support_rate,updated_at=EXCLUDED.updated_at,
          profit_sample_count=EXCLUDED.profit_sample_count,profit_win_count=EXCLUDED.profit_win_count,
          profit_loss_count=EXCLUDED.profit_loss_count,profit_flat_count=EXCLUDED.profit_flat_count,
          win_rate=EXCLUDED.win_rate""",
        [
            (
                tenant_id,
                user_id,
                theme_code,
                row["category"],
                row["rule_text"],
                row["sample_count"],
                *(row[key] for key in _COUNTS),
                row["support_rate"],
                now,
                row["profit_sample_count"],
                *(row[key] for key in _PROFIT_COUNTS),
                row["win_rate"],
            )
            for row in rules
        ],
    )
    return rules


async def read_strategy_memory(conn, tenant_id, user_id, now):
    """Read owner aggregates, rechecking current MINE/ledger proof without writes.

    The Worker is trusted to read this configured owner only. API calls still
    obey actor RLS; no role switch or policy bypass is performed here.
    """
    now = _clock(now)
    rows = await conn.fetch(
        """SELECT theme_code,category,rule_text,sample_count,supported_count,
          mixed_count,refuted_count,inconclusive_count,support_rate,updated_at,
          profit_sample_count,profit_win_count,profit_loss_count,profit_flat_count,win_rate
        FROM research.strategy_memory_rules
        WHERE tenant_id=$1 AND user_id=$2 AND updated_at<=$3
          AND EXISTS(SELECT 1 FROM identity.tenant_members m
            WHERE m.tenant_id=$1 AND m.user_id=$2 AND m.status='ACTIVE')
        ORDER BY theme_code,category""",
        tenant_id,
        user_id,
        now,
    )
    if not rows:
        return []
    reviews = await _review_evidence(conn, tenant_id, user_id, now)
    fills = await _linked_fill_evidence(conn, tenant_id, user_id, now)
    themes = {row["theme_code"] for row in rows if row["theme_code"] in THEME_CODES}
    current = {
        (theme, rule["category"]): rule
        for theme in themes
        for rule in aggregate_reviews(
            reviews, tenant_id, user_id, theme, now, fills=fills
        )
    }
    # Recompute counts from the same current review set as profit proof. Stored
    # templates and rates never override current permission or reversal facts.
    return [
        current[(row["theme_code"], row["category"])]
        for row in rows
        if (row["theme_code"], row["category"]) in current
    ]


async def refresh_memory_for_trade(conn, tenant_id, user_id, fill_id, theme_code, now):
    """A reused fill invalidates every affected owner theme, not just this link."""
    now = _clock(now)
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        f"strategy-memory:{tenant_id}:{user_id}",
    )
    rows = await conn.fetch(
        """SELECT DISTINCT COALESCE(v.snapshot->'scope'->>'themeCode',
          v.snapshot->'actionablePlan'->>'themeCode','UNCLASSIFIED') AS theme_code
        FROM research.study_trade_links l
        JOIN research.study_versions v ON v.tenant_id=l.tenant_id AND v.user_id=l.user_id
          AND v.track_id=l.track_id AND v.id=l.version_id
        WHERE l.tenant_id=$1 AND l.user_id=$2 AND l.fill_id=$3 AND l.linked_at<=$4
          AND v.created_at<=$4""",
        tenant_id,
        user_id,
        fill_id,
        now,
    )
    themes = {theme_code if theme_code in THEME_CODES else "UNCLASSIFIED"}
    themes.update(
        row["theme_code"] if row["theme_code"] in THEME_CODES else "UNCLASSIFIED"
        for row in rows
    )
    for theme in sorted(themes):
        await refresh_strategy_memory(conn, tenant_id, user_id, theme, now)


def public_memory(rows, now):
    """Rebuild a public whitelist and derive both rates from verified counts."""
    now = _clock(now)
    output = {}
    for row in rows:
        theme, category = row.get("theme_code"), row.get("category")
        stamp = _stamp(row.get("updated_at"))
        counts = {key: row.get(key) for key in ("sample_count", *_COUNTS)}
        if (
            theme not in THEME_CODES
            or category not in CATEGORY_LABELS
            or stamp is None
            or stamp > now
            or any(type(value) is not int or value < 0 for value in counts.values())
            or counts["sample_count"] == 0
            or sum(counts[key] for key in _COUNTS) != counts["sample_count"]
        ):
            continue
        rate = _rate(counts)
        profits = {
            key: row.get(key, 0) for key in ("profit_sample_count", *_PROFIT_COUNTS)
        }
        if (
            any(type(value) is not int or value < 0 for value in profits.values())
            or sum(profits[key] for key in _PROFIT_COUNTS)
            != profits["profit_sample_count"]
            or profits["profit_sample_count"] > counts["sample_count"]
        ):
            profits = {key: 0 for key in profits}
        counts.update(profits)
        win_rate = (
            Decimal(profits["profit_win_count"]) / profits["profit_sample_count"]
            if profits["profit_sample_count"]
            else None
        )
        key = f"{theme}:{category}"
        output[key] = {
            "key": key,
            "themeCode": theme,
            "category": category,
            "ruleText": _rule_text(category, counts),
            "sampleCount": counts["sample_count"],
            "supportedCount": counts["supported_count"],
            "mixedCount": counts["mixed_count"],
            "refutedCount": counts["refuted_count"],
            "inconclusiveCount": counts["inconclusive_count"],
            "supportRate": format(rate, "f") if rate is not None else None,
            "profitSampleCount": profits["profit_sample_count"],
            "profitWinCount": profits["profit_win_count"],
            "profitLossCount": profits["profit_loss_count"],
            "profitFlatCount": profits["profit_flat_count"],
            "winRate": format(win_rate, "f") if win_rate is not None else None,
            "profitRateMeaning": "CONFIRMED_LINKED_ROUNDTRIP",
            "rateMeaning": "JUDGMENT_SUPPORT_RATE",
            "updatedAt": stamp.isoformat(),
        }
    return [output[key] for key in sorted(output)]


sanitized_memory_for_packet = public_memory
