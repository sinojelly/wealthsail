"""Persistence queries for governed investment recommendations."""

from __future__ import annotations

import asyncpg


async def latest_publishable_recommendations(
    conn: asyncpg.Connection,
    tenant_id: str,
    *,
    limit: int = 4,
) -> list[asyncpg.Record]:
    """Return only current recommendations that passed every publication gate."""
    return await conn.fetch(
        """
        SELECT rec.id, rec.instrument_id, rec.recommendation_type,
               rec.summary, rec.counter_case, rec.confidence,
               rec.policy_check_status, rec.valid_until, rec.created_at,
               instrument.canonical_name,
               COALESCE(alias.alias_value, '') AS ticker,
               count(evidence.id)::int AS evidence_count,
               CASE
                   WHEN bool_or(evidence.quality_status IN ('STALE', 'REVISED'))
                       THEN 'STALE'
                   WHEN bool_or(evidence.quality_status = 'ESTIMATED')
                       THEN 'ESTIMATED'
                   ELSE 'FRESH'
               END AS evidence_quality,
               run.model, run.prompt_template_version
        FROM research.recommendations rec
        JOIN research.model_runs run
          ON run.id = rec.model_run_id AND run.tenant_id = rec.tenant_id
        LEFT JOIN reference.instruments instrument ON instrument.id = rec.instrument_id
        LEFT JOIN LATERAL (
            SELECT alias_value
            FROM reference.instrument_aliases
            WHERE instrument_id = rec.instrument_id AND alias_type = 'TICKER'
              AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
            ORDER BY effective_from DESC LIMIT 1
        ) alias ON TRUE
        JOIN research.evidence_refs evidence
          ON evidence.tenant_id = rec.tenant_id
         AND evidence.recommendation_id = rec.id
        WHERE rec.tenant_id = $1
          AND rec.status = 'PUBLISHED'
          AND rec.valid_until > now()
          AND rec.policy_check_status <> 'REJECTED'
          AND run.status = 'COMPLETED'
          AND EXISTS (
              SELECT 1 FROM research.evidence_refs required_evidence
              WHERE required_evidence.tenant_id = rec.tenant_id
                AND required_evidence.recommendation_id = rec.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM research.evidence_refs blocked_evidence
              WHERE blocked_evidence.tenant_id = rec.tenant_id
                AND blocked_evidence.recommendation_id = rec.id
                AND blocked_evidence.quality_status IN ('UNAVAILABLE', 'CONFLICT')
          )
        GROUP BY rec.id, instrument.canonical_name, alias.alias_value,
                 run.model, run.prompt_template_version
        ORDER BY rec.created_at DESC, rec.id
        LIMIT $2
        """,
        tenant_id,
        limit,
    )
