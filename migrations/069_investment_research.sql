BEGIN;

CREATE TABLE reference.research_directions (
    id TEXT PRIMARY KEY, parent_id TEXT REFERENCES reference.research_directions(id),
    market_id TEXT NOT NULL, market_label TEXT NOT NULL, style_id TEXT NOT NULL,
    label TEXT NOT NULL, framework TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE reference.research_indices (
    id TEXT PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL,
    market TEXT NOT NULL, currency CHAR(3) NOT NULL DEFAULT 'CNY',
    exposure TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0,
    provider_symbol TEXT, source_uri TEXT, verified_at TIMESTAMPTZ,
    UNIQUE(market, code)
);
CREATE TABLE reference.direction_index_links (
    direction_id TEXT NOT NULL REFERENCES reference.research_directions(id),
    index_id TEXT NOT NULL REFERENCES reference.research_indices(id),
    PRIMARY KEY(direction_id, index_id)
);
CREATE TABLE reference.fund_index_links (
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id),
    index_id TEXT NOT NULL REFERENCES reference.research_indices(id),
    effective_from DATE NOT NULL, effective_to DATE,
    source_uri TEXT NOT NULL CHECK(length(btrim(source_uri))>0),
    verified_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(instrument_id, index_id, effective_from),
    CHECK(effective_to IS NULL OR effective_to>=effective_from)
);
CREATE INDEX fund_index_links_index ON reference.fund_index_links(index_id, instrument_id);
CREATE TABLE market.direction_analysis_snapshots (
    direction_id TEXT NOT NULL REFERENCES reference.research_directions(id),
    as_of TIMESTAMPTZ NOT NULL, state TEXT NOT NULL, assessment JSONB,
    input_hash TEXT NOT NULL, method_version TEXT NOT NULL,
    PRIMARY KEY(direction_id, as_of, method_version)
);
CREATE TABLE reference.macro_indicator_catalog (
    field_code TEXT PRIMARY KEY, label TEXT NOT NULL, dimension TEXT NOT NULL,
    max_age_days INTEGER NOT NULL DEFAULT 45 CHECK(max_age_days>0)
);
INSERT INTO reference.macro_indicator_catalog(field_code,label,dimension) VALUES
 ('PMI','制造业 PMI','growth'),('CPI_YOY','CPI 同比','inflation');

-- A research taxonomy, not a current market assessment or fund-product mapping.
INSERT INTO reference.research_directions(id,market_id,market_label,style_id,label,framework,sort_order) VALUES
 ('a-share:broad','a-share','A股','broad','宽基','研究盈利周期、估值、流动性与风险偏好。',1),
 ('a-share:dividend','a-share','A股','dividend','红利','研究股息持续性与行业集中度。',2),
 ('a-share:growth','a-share','A股','growth','成长','研究盈利增速、估值与利率敏感性。',3),
 ('a-share:value','a-share','A股','value','价值','研究估值、现金流与盈利质量。',4),
 ('a-share:technology','a-share','A股','technology','科技','研究产业趋势与集中风险。',5),
 ('a-share:consumer','a-share','A股','consumer','消费','研究消费需求与盈利变化。',6),
 ('a-share:cyclical','a-share','A股','cyclical','周期','研究经济增长与供需周期。',7),
 ('a-share:low-vol','a-share','A股','low-vol','低波','研究波动与组合分散价值。',8),
 ('hk:broad','hk','港股','broad','宽基','研究盈利、美元流动性与汇率风险。',10),
 ('hk:dividend','hk','港股','dividend','红利','研究分红与汇率影响。',11),
 ('hk:technology','hk','港股','technology','科技','研究成长、估值与风险偏好。',12),
 ('us:broad','us','美股','broad','宽基','研究盈利、估值和美元利率。',20),
 ('us:technology','us','美股','technology','科技','研究创新与集中风险。',21),
 ('bond:government','bond','债券','government','利率债','研究通胀、利率与久期风险。',30),
 ('bond:credit','bond','债券','credit','信用债','研究信用利差与违约风险。',31),
 ('gold:physical','gold','黄金','physical','黄金现货','研究实际利率、汇率与避险需求。',40),
 ('other:broad','other','其他','broad','全部','研究覆盖尚待建设。',50);
INSERT INTO reference.research_indices(id,code,name,market,exposure,sort_order) VALUES
 ('hs300','000300','沪深300','SSE','大盘核心资产',1),
 ('a500','000510','中证A500','SSE','行业均衡核心资产',2),
 ('zz500','000905','中证500','SSE','中盘成长',3),
 ('csdiv','000922','中证红利','SSE','高股息',4),
 ('chinext','399006','创业板指','SZSE','创新成长',5),
 ('star50','000688','科创50','SSE','硬科技',6),
 ('hsi','HSI','恒生指数','HK','港股大盘',7),
 ('hscei','HSCEI','恒生中国企业指数','HK','在港中国企业',8),
 ('sp500','SPX','标普500','US','美国大盘',9),
 ('nasdaq100','NDX','纳斯达克100','US','美国成长',10),
 ('au9999','AU9999','上海金 AU9999','SGE','人民币黄金',11);
INSERT INTO reference.direction_index_links(direction_id,index_id) VALUES
 ('a-share:broad','hs300'),('a-share:broad','a500'),('a-share:broad','zz500'),
 ('a-share:dividend','csdiv'),('a-share:growth','chinext'),('a-share:growth','star50'),
 ('a-share:technology','star50'),('hk:broad','hsi'),('hk:broad','hscei'),
 ('us:broad','sp500'),('us:technology','nasdaq100'),('gold:physical','au9999');

CREATE VIEW reference.research_fund_catalog AS
 SELECT i.id AS instrument_id, a.alias_value AS fund_code,
   i.canonical_name AS fund_name, COALESCE(m.fund_type,i.asset_type) AS fund_type,
   CASE WHEN i.asset_type='EXCHANGE_FUND' THEN 'ETF' ELSE 'FUND' END AS trade_form,
   i.primary_market AS market, i.trading_currency AS currency
 FROM reference.instruments i
 JOIN LATERAL (
   SELECT alias_value FROM reference.instrument_aliases
   WHERE instrument_id=i.id AND alias_type='TICKER'
     AND effective_from<=CURRENT_DATE AND (effective_to IS NULL OR effective_to>=CURRENT_DATE)
   ORDER BY effective_from DESC, alias_value LIMIT 1
 ) a ON TRUE
 LEFT JOIN reference.fund_master m ON i.asset_type='OFF_EXCHANGE_FUND' AND m.fund_code=a.alias_value
 WHERE i.asset_type IN ('EXCHANGE_FUND','OFF_EXCHANGE_FUND') AND i.lifecycle_status='ACTIVE'
 UNION ALL
 SELECT NULL::uuid, m.fund_code,m.fund_name,m.fund_type,'FUND','FUND_CN','CNY'
 FROM reference.fund_master m WHERE NOT EXISTS (
   SELECT 1 FROM reference.instrument_aliases a JOIN reference.instruments i ON i.id=a.instrument_id
   WHERE i.asset_type='OFF_EXCHANGE_FUND' AND a.alias_type='TICKER' AND a.alias_value=m.fund_code
     AND a.effective_from<=CURRENT_DATE AND (a.effective_to IS NULL OR a.effective_to>=CURRENT_DATE)
 );

REVOKE ALL ON reference.research_directions, reference.research_indices,
 reference.direction_index_links, reference.fund_index_links, reference.macro_indicator_catalog,
 reference.research_fund_catalog, market.direction_analysis_snapshots FROM PUBLIC, authenticated;
GRANT SELECT ON reference.research_directions, reference.research_indices,
 reference.direction_index_links, reference.fund_index_links, reference.macro_indicator_catalog,
 reference.research_fund_catalog, market.direction_analysis_snapshots TO wealthsail_api, wealthsail_worker;
GRANT INSERT, UPDATE ON reference.research_indices, reference.fund_index_links,
 market.direction_analysis_snapshots TO wealthsail_worker;
COMMIT;
