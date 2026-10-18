BEGIN;

-- Public product identity does not require a portfolio instrument UUID. These
-- relationships are distinct from a thematic exposure or an active-fund
-- performance benchmark. Only TRACKS/ETF_LINK enter index-product discovery.
CREATE TABLE reference.product_index_links (
    fund_code TEXT NOT NULL CHECK (fund_code ~ '^[0-9]{6}$'),
    market TEXT NOT NULL CHECK (length(btrim(market)) BETWEEN 1 AND 30),
    index_id TEXT NOT NULL REFERENCES reference.research_indices(id),
    relationship_type TEXT NOT NULL CHECK (relationship_type IN ('TRACKS','ETF_LINK','BENCHMARK')),
    fund_name TEXT NOT NULL CHECK (length(btrim(fund_name)) BETWEEN 1 AND 200),
    effective_from DATE NOT NULL,
    effective_to DATE,
    source_uri TEXT NOT NULL CHECK (source_uri ~ '^https://[^[:space:]]+$' AND length(source_uri)<=2000),
    verified_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(market,fund_code,index_id,relationship_type,effective_from),
    CHECK(effective_to IS NULL OR effective_to>=effective_from)
);
CREATE INDEX product_index_links_index ON reference.product_index_links(index_id,market,fund_code);

-- These three A-share classes and their CSI 300 ETF-link relationships were
-- reviewed against fund-manager sources on 2026-10-18. No history, quote,
-- instrument, portfolio holding or active-fund exposure is manufactured.
INSERT INTO reference.product_index_links
    (fund_code,market,index_id,relationship_type,fund_name,effective_from,source_uri,verified_at)
VALUES
    ('000051','FUND_CN','hs300','ETF_LINK','华夏沪深300ETF联接A','2026-10-18',
     'https://www.chinaamc.com/upload/resources/file/2026/05/29/3a929be438664216bb270e72671b1bf8.pdf',
     '2026-10-18T00:00:00+08:00'),
    ('110020','FUND_CN','hs300','ETF_LINK','易方达沪深300ETF联接A','2026-10-18',
     'https://api.efunds.com.cn/owch/oURL/website/weixin/wx_fundinfo.shtml?fundcode=110020',
     '2026-10-18T00:00:00+08:00'),
    ('202015','FUND_CN','hs300','ETF_LINK','南方沪深300ETF联接A','2026-10-18',
     'https://www.nffund.com/main/files/2026/04/21/748910732015.pdf',
     '2026-10-18T00:00:00+08:00');

-- Keep existing master data authoritative; ensure a new installation can show
-- the verified product identities without waiting for the weekly full master.
INSERT INTO reference.fund_master(fund_code,fund_name,fund_type)
SELECT fund_code,fund_name,'ETF联接基金'
FROM reference.product_index_links
WHERE market='FUND_CN' AND relationship_type='ETF_LINK'
ON CONFLICT(fund_code) DO NOTHING;

CREATE VIEW reference.research_product_index_links WITH (security_invoker=true) AS
SELECT c.fund_code,c.market,c.instrument_id,f.index_id,'TRACKS'::text AS relationship_type,
    f.source_uri,f.verified_at,f.effective_from,f.effective_to
FROM reference.research_fund_catalog c
JOIN reference.fund_index_links f ON f.instrument_id=c.instrument_id
UNION ALL
SELECT c.fund_code,c.market,c.instrument_id,p.index_id,p.relationship_type,
    p.source_uri,p.verified_at,p.effective_from,p.effective_to
FROM reference.research_fund_catalog c
JOIN reference.product_index_links p ON p.fund_code=c.fund_code AND p.market=c.market
WHERE p.relationship_type IN ('TRACKS','ETF_LINK');

REVOKE ALL ON reference.product_index_links,reference.research_product_index_links
    FROM PUBLIC,authenticated;
GRANT SELECT ON reference.product_index_links,reference.research_product_index_links
    TO wealthsail_api,wealthsail_worker;
GRANT INSERT,UPDATE ON reference.product_index_links TO wealthsail_worker;

COMMENT ON TABLE reference.product_index_links IS
    'Reviewed public code+market product-index identity. BENCHMARK never implies index tracking; API is read only.';
COMMENT ON VIEW reference.research_product_index_links IS
    'TRACKS/ETF_LINK relations over actual fund catalog identities. Consumers must apply their Shanghai effective date and verification cutoff.';

COMMIT;
