"""All investment context queries are database-only and parameterized."""

from backend.investment.schemas import ScreenQuery


async def directions(conn):
    return await conn.fetch("""
        SELECT d.*, count(DISTINCT l.index_id) AS index_count
        FROM reference.research_directions d
        LEFT JOIN reference.direction_index_links l ON l.direction_id=d.id
        GROUP BY d.id ORDER BY d.sort_order, d.id
    """)


async def indices(conn, direction_id):
    return await conn.fetch(
        """
        SELECT i.*, count(DISTINCT f.instrument_id) AS fund_count
        FROM reference.research_indices i
        JOIN reference.direction_index_links d ON d.index_id=i.id
        LEFT JOIN reference.fund_index_links f ON f.index_id=i.id
          AND f.effective_from<=CURRENT_DATE
          AND (f.effective_to IS NULL OR f.effective_to>=CURRENT_DATE)
        WHERE d.direction_id=$1
        GROUP BY i.id ORDER BY i.sort_order, i.id
    """,
        direction_id,
    )


async def macro(conn):
    return await conn.fetch("""
        SELECT c.field_code, c.label, c.dimension, c.max_age_days,
          o.value, o.unit, o.as_of, o.observed_at, o.quality_status, o.revision,
          o.published_at, o.retrieved_at, o.source_name, o.source_uri
        FROM reference.macro_indicator_catalog c
        LEFT JOIN LATERAL (
          SELECT o.*, s.published_at, s.retrieved_at, ds.name AS source_name,
            COALESCE(NULLIF(o.metadata->>'sourceUri',''),s.origin_uri) AS source_uri
          FROM market.market_observations o
          JOIN market.source_snapshots s ON s.id=o.snapshot_id
          LEFT JOIN reference.data_sources ds ON ds.id=o.source_id
          WHERE o.field_code=c.field_code AND o.subject_type='MACRO_SERIES'
            AND o.as_of<=now() AND o.observed_at<=now() AND s.retrieved_at<=now()
            AND (s.published_at IS NULL OR s.published_at<=now())
          ORDER BY o.as_of DESC,o.revision DESC,o.observed_at DESC,s.retrieved_at DESC
          LIMIT 1
        ) o ON TRUE
        ORDER BY c.field_code
    """)


async def macro_history(conn, start, end):
    """Latest available revision per indicator and reporting period, read only."""
    return await conn.fetch(
        """
        SELECT DISTINCT ON (c.field_code,o.as_of)
          c.field_code,c.label,c.dimension,c.max_age_days,
          o.value,o.unit,o.as_of,o.observed_at,o.quality_status,o.revision,
          s.published_at,s.retrieved_at,ds.name AS source_name,
          COALESCE(NULLIF(o.metadata->>'sourceUri',''),s.origin_uri) AS source_uri
        FROM reference.macro_indicator_catalog c
        JOIN market.market_observations o
          ON o.field_code=c.field_code AND o.subject_type='MACRO_SERIES'
        JOIN market.source_snapshots s ON s.id=o.snapshot_id
        LEFT JOIN reference.data_sources ds ON ds.id=o.source_id
        WHERE o.as_of>=($1::date::timestamp AT TIME ZONE 'Asia/Shanghai')
          AND o.as_of<=LEAST($2::timestamptz,now())
          AND o.observed_at<=LEAST($2::timestamptz,now())
          AND s.retrieved_at<=LEAST($2::timestamptz,now())
          AND (s.published_at IS NULL OR s.published_at<=LEAST($2::timestamptz,now()))
        ORDER BY c.field_code,o.as_of,o.revision DESC,o.observed_at DESC,s.retrieved_at DESC
        """,
        start,
        end,
    )


def _where(query):
    conditions, params = [], []

    def add(sql, value):
        params.append(value)
        conditions.append(sql.replace("?", f"${len(params)}"))

    if query.keyword.strip():
        add(
            "(c.fund_name ILIKE ? OR c.fund_code ILIKE ?)", f"%{query.keyword.strip()}%"
        )
    if query.fund_type:
        add("c.fund_type=?", query.fund_type)
    if query.trade_form:
        add("c.trade_form=?", query.trade_form)
    if query.index_id:
        add(
            "EXISTS (SELECT 1 FROM reference.fund_index_links f WHERE f.instrument_id=c.instrument_id AND f.index_id=? AND f.effective_from<=CURRENT_DATE AND (f.effective_to IS NULL OR f.effective_to>=CURRENT_DATE))",
            query.index_id,
        )
    if query.direction_id:
        add(
            "EXISTS (SELECT 1 FROM reference.fund_index_links f JOIN reference.direction_index_links d ON d.index_id=f.index_id WHERE f.instrument_id=c.instrument_id AND d.direction_id=? AND f.effective_from<=CURRENT_DATE AND (f.effective_to IS NULL OR f.effective_to>=CURRENT_DATE))",
            query.direction_id,
        )
    if query.nav_position_min is not None:
        add("r.current_percentile>=?", query.nav_position_min)
    if query.nav_position_max is not None:
        add("r.current_percentile<=?", query.nav_position_max)
    if query.scorable_only:
        conditions.append("a.technical_score IS NOT NULL")
    if query.min_history_days:
        add("a.history_days>=?", query.min_history_days)
    return (" WHERE " + " AND ".join(conditions) if conditions else ""), params


async def screen(conn, query: ScreenQuery):
    where, params = _where(query)
    params.append(query.analysis_at)
    cutoff_param = f"${len(params)}"
    base = f"""FROM reference.research_fund_catalog c
      LEFT JOIN reference.fund_nav_ranges r ON c.trade_form='FUND' AND r.fund_code=c.fund_code
      LEFT JOIN LATERAL (
        SELECT CASE WHEN s.as_of >= (
          SELECT max(calendar_date) FROM reference.trading_calendars
          WHERE market=c.market AND is_trading_day
            AND calendar_date <= reference.research_available_day((s.result->>'publicationHour')::integer,
                (now() AT TIME ZONE 'Asia/Shanghai')::date, now())
        ) THEN (s.result->>'score')::numeric ELSE NULL END AS technical_score,
        jsonb_array_length(s.points) AS history_days,
        CASE WHEN (s.result->>'publicationHour') IS NULL THEN 'UNVERIFIED'
          WHEN s.as_of < (SELECT max(calendar_date) FROM reference.trading_calendars
            WHERE market=c.market AND is_trading_day AND calendar_date<=reference.research_available_day(
              (s.result->>'publicationHour')::integer,(now() AT TIME ZONE 'Asia/Shanghai')::date,now())) THEN 'STALE'
          ELSE s.result->>'state' END AS technical_state
        FROM market.investment_analysis_snapshots s
        WHERE s.subject_kind='fund' AND s.subject_key=c.instrument_id::text
          AND s.retrieved_at<=LEAST(COALESCE({cutoff_param}::timestamptz,now()),now())
        ORDER BY s.retrieved_at DESC LIMIT 1
      ) a ON TRUE"""
    total = await conn.fetchval("SELECT count(*) " + base + where, *params)
    sort = {
        "name": "c.fund_name",
        "code": "c.fund_code",
        "navPosition": "r.current_percentile",
        "technicalScore": "a.technical_score",
    }[query.sort_by]
    order = "DESC" if query.sort_order == "desc" else "ASC"
    rows = await conn.fetch(
        "SELECT c.*, r.latest_nav, r.latest_nav_date, r.current_percentile, r.data_source, r.updated_at, a.technical_score, a.history_days, a.technical_state "
        + base
        + where
        + f" ORDER BY {sort} {order} NULLS LAST, c.market, c.fund_code"
        + f" LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}",
        *params,
        query.page_size,
        (query.page - 1) * query.page_size,
    )
    return rows, total


async def location(conn, instrument_id):
    return await conn.fetchrow(
        """
      SELECT f.index_id, d.direction_id, r.market_id, r.style_id
      FROM reference.fund_index_links f
      JOIN reference.direction_index_links d ON d.index_id=f.index_id
      JOIN reference.research_directions r ON r.id=d.direction_id
      WHERE f.instrument_id=$1 AND f.effective_from<=CURRENT_DATE
        AND (f.effective_to IS NULL OR f.effective_to>=CURRENT_DATE)
      ORDER BY r.sort_order, d.direction_id, f.index_id LIMIT 1
    """,
        instrument_id,
    )


async def search_indices(conn, keyword):
    return await conn.fetch(
        """
      SELECT DISTINCT ON (i.id) i.*, d.direction_id, r.market_id, r.style_id
      FROM reference.research_indices i
      JOIN reference.direction_index_links d ON d.index_id=i.id
      JOIN reference.research_directions r ON r.id=d.direction_id
      WHERE i.name ILIKE $1 OR i.code ILIKE $1
      ORDER BY i.id, r.sort_order, d.direction_id LIMIT 10
    """,
        f"%{keyword}%",
    )
