"""Import reviewed source mappings, using Worker authority. No name inference.

Usage: python -m scripts.register_investment_reference --input reviewed.json
The file must cite the tracking/index source and the verified publication hour.
"""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from backend.common.database import create_pool, system_connection
from backend.config import get_settings


class IndexSource(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    id: str
    provider_symbol: str = Field(alias="providerSymbol", min_length=1)
    source_uri: HttpUrl = Field(alias="sourceUri")
    publish_hour: int = Field(alias="publishHour", ge=0, le=23)


class FundLink(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    instrument_id: UUID = Field(alias="instrumentId")
    index_id: str = Field(alias="indexId")
    source_uri: HttpUrl = Field(alias="sourceUri")


class FundSource(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    instrument_id: UUID = Field(alias="instrumentId")
    source_uri: HttpUrl = Field(alias="sourceUri")
    publish_hour: int = Field(alias="publishHour", ge=0, le=23, strict=True)
    source_verified: bool = Field(default=True, alias="sourceVerified", strict=True)
    return_series_verified: bool = Field(
        default=False, alias="returnSeriesVerified", strict=True
    )


class ReferenceImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indices: list[IndexSource] = []
    links: list[FundLink] = []
    funds: list[FundSource] = []


async def register(pool, data: ReferenceImport):
    async with system_connection(pool) as conn:
        async with conn.transaction():
            for item in data.indices:
                row = await conn.fetchrow("""UPDATE reference.research_indices SET provider_symbol=$2,
                    source_uri=$3,verified_at=now(),publish_hour=$4 WHERE id=$1 RETURNING id""",
                                          item.id, item.provider_symbol, str(item.source_uri), item.publish_hour)
                if not row:
                    raise ValueError(f"unknown research index: {item.id}")
            for item in data.funds:
                config = item.model_dump(mode="json", by_alias=True, exclude={"instrument_id"})
                row = await conn.fetchrow(
                    """UPDATE reference.instruments SET metadata=jsonb_set(
                      COALESCE(metadata,'{}'::jsonb),'{research}',
                      COALESCE(metadata->'research','{}'::jsonb) || $2::jsonb
                        || jsonb_build_object('verifiedAt',now()),true)
                      WHERE id=$1 AND asset_type IN ('EXCHANGE_FUND','OFF_EXCHANGE_FUND')
                      RETURNING id""",
                    item.instrument_id, json.dumps(config),
                )
                if not row:
                    raise ValueError(f"unknown research fund: {item.instrument_id}")
            for item in data.links:
                await conn.execute("""INSERT INTO reference.fund_index_links(instrument_id,index_id,effective_from,source_uri,verified_at)
                  VALUES($1,$2,CURRENT_DATE,$3,now()) ON CONFLICT(instrument_id,index_id,effective_from)
                  DO UPDATE SET source_uri=EXCLUDED.source_uri,verified_at=EXCLUDED.verified_at""",
                                   item.instrument_id, item.index_id, str(item.source_uri))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    data = ReferenceImport.model_validate(json.loads(args.input.read_text("utf-8")))
    pool = await create_pool(get_settings().worker_database_url, runtime_role="wealthsail_worker")
    try:
        await register(pool, data)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
