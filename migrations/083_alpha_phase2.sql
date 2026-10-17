BEGIN;

CREATE TABLE reference.theme_asset_graph (
 theme_code TEXT NOT NULL CHECK(theme_code IN ('HAINAN_FTP','PUDONG_DEV','GEO_CRUDE_OIL','AI_MEMORY_HBM','COMMERCIAL_SPACE')),
 theme_label TEXT NOT NULL,asset_key TEXT NOT NULL,asset_kind TEXT NOT NULL,asset_code TEXT NOT NULL,
 asset_name TEXT NOT NULL,market TEXT NOT NULL,relationship_type TEXT NOT NULL CHECK(relationship_type IN ('DIRECT','INDIRECT','RESEARCH_CANDIDATE')),
 source_uri TEXT NOT NULL CHECK(source_uri LIKE 'https://%'),verified_at TIMESTAMPTZ,
 metadata JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(metadata)='object'),PRIMARY KEY(theme_code,asset_key)
);
CREATE TABLE market.alpha_public_batches (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),run_date DATE NOT NULL UNIQUE,
 claimed_at TIMESTAMPTZ NOT NULL,completed_at TIMESTAMPTZ,
 status TEXT NOT NULL CHECK(status IN ('CLAIMED','READY','PARTIAL','FAILED')),
 payload JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(payload)='object')
);
CREATE TABLE research.strategy_memory_rules (
 tenant_id UUID NOT NULL,user_id UUID NOT NULL,theme_code TEXT NOT NULL,
 category TEXT NOT NULL CHECK(category IN ('BASELINE','FACT','TRANSMISSION','EXPECTATIONS','INSTRUMENT','TIMING','RISK','DISCIPLINE')),
 rule_text TEXT NOT NULL,sample_count INTEGER NOT NULL CHECK(sample_count>=0),
 supported_count INTEGER NOT NULL CHECK(supported_count>=0),mixed_count INTEGER NOT NULL CHECK(mixed_count>=0),
 refuted_count INTEGER NOT NULL CHECK(refuted_count>=0),inconclusive_count INTEGER NOT NULL CHECK(inconclusive_count>=0),
 support_rate NUMERIC CHECK(support_rate BETWEEN 0 AND 1),updated_at TIMESTAMPTZ NOT NULL,
 profit_sample_count INTEGER NOT NULL DEFAULT 0 CHECK(profit_sample_count>=0),
 profit_win_count INTEGER NOT NULL DEFAULT 0 CHECK(profit_win_count>=0),
 profit_loss_count INTEGER NOT NULL DEFAULT 0 CHECK(profit_loss_count>=0),
 profit_flat_count INTEGER NOT NULL DEFAULT 0 CHECK(profit_flat_count>=0),
 win_rate NUMERIC CHECK(win_rate BETWEEN 0 AND 1),
 CHECK(profit_sample_count=profit_win_count+profit_loss_count+profit_flat_count),
 CHECK(sample_count=supported_count+mixed_count+refuted_count+inconclusive_count),
 PRIMARY KEY(tenant_id,user_id,theme_code,category),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity.tenant_members(tenant_id,user_id)
);
CREATE TABLE research.alpha_opportunity_runs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,user_id UUID NOT NULL,run_date DATE NOT NULL,
 slot TEXT NOT NULL CHECK(slot IN ('MORNING','FUND_1430','CONFIRMED_NAV')),
 status TEXT NOT NULL CHECK(status IN ('RUNNING','SENT','NO_ALERT','FAILED','EXPIRED','COMPLETED')),
 claimed_at TIMESTAMPTZ NOT NULL,finished_at TIMESTAMPTZ,result JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(result)='object'),
 UNIQUE(tenant_id,user_id,run_date,slot),FOREIGN KEY(tenant_id,user_id) REFERENCES identity.tenant_members(tenant_id,user_id)
);
CREATE TABLE research.study_excursions (
 tenant_id UUID NOT NULL,user_id UUID NOT NULL,track_id UUID NOT NULL,version_id UUID NOT NULL,
 base_as_of DATE NOT NULL,base_price NUMERIC NOT NULL CHECK(base_price>0),price_basis TEXT NOT NULL,unit TEXT NOT NULL,
 last_as_of DATE NOT NULL,mfe_pct NUMERIC NOT NULL CHECK(mfe_pct>=0),mae_pct NUMERIC NOT NULL CHECK(mae_pct<=0),
 sample_count INTEGER NOT NULL CHECK(sample_count>0),updated_at TIMESTAMPTZ NOT NULL,
 CHECK(last_as_of>=base_as_of),PRIMARY KEY(tenant_id,user_id,track_id,version_id),
 FOREIGN KEY(tenant_id,user_id,track_id,version_id) REFERENCES research.study_versions(tenant_id,user_id,track_id,id)
);

DO $$ DECLARE tab TEXT; BEGIN
 FOREACH tab IN ARRAY ARRAY['strategy_memory_rules','alpha_opportunity_runs','study_excursions'] LOOP
   EXECUTE format('ALTER TABLE research.%I ENABLE ROW LEVEL SECURITY',tab);
   EXECUTE format('ALTER TABLE research.%I FORCE ROW LEVEL SECURITY',tab);
   EXECUTE format('CREATE POLICY alpha_actor_read ON research.%I FOR SELECT TO wealthsail_api USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id))',tab);
 END LOOP;
END $$;
CREATE POLICY alpha_actor_memory_insert ON research.strategy_memory_rules FOR INSERT TO wealthsail_api
 WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
CREATE POLICY alpha_actor_memory_update ON research.strategy_memory_rules FOR UPDATE TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id))
 WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
REVOKE ALL ON reference.theme_asset_graph,market.alpha_public_batches,research.strategy_memory_rules,research.alpha_opportunity_runs,research.study_excursions FROM PUBLIC,authenticated;
GRANT SELECT ON reference.theme_asset_graph,market.alpha_public_batches,research.strategy_memory_rules,research.alpha_opportunity_runs,research.study_excursions TO wealthsail_api,wealthsail_worker;
GRANT INSERT,UPDATE ON market.alpha_public_batches,research.alpha_opportunity_runs,research.study_excursions TO wealthsail_worker;
GRANT INSERT,UPDATE ON research.strategy_memory_rules TO wealthsail_api;
-- Read-only, actor-filtered verification of actually linked and closed fills.
GRANT SELECT(id,tenant_id,account_id,instrument_id,side,quantity,unit_price,fee,trade_date,confirmed_at,status,ledger_event_id,created_at) ON ledger.trade_fills TO wealthsail_worker;
GRANT SELECT(tenant_id,account_id,user_id,relationship,effective_from,effective_to) ON identity.account_members TO wealthsail_worker;

-- Curated research relationships, not certified current holdings or recommendations.
INSERT INTO reference.theme_asset_graph(theme_code,theme_label,asset_key,asset_kind,asset_code,asset_name,market,relationship_type,source_uri,metadata) VALUES
('HAINAN_FTP','海南自贸港','fund:008975','ETF_LINK_FUND','008975','富国中证消费50ETF联接A','FUND_CN','INDIRECT','https://www.fullgoal.com.cn/fundDetail/008975/index.html','{"chainRole":"免税与消费代理，海南持仓需核验","verificationScope":"PRODUCT_IDENTITY_ONLY","holdingsVerified":false}'),
('HAINAN_FTP','海南自贸港','etf:159766','ETF','159766','富国中证旅游主题ETF','SZSE','INDIRECT','https://www.fullgoal.com.cn/','{"chainRole":"旅游需求，全国行业代理","holdingsVerified":false}'),
('HAINAN_FTP','海南自贸港','stock:600515','STOCK','600515','海南机场','SSE','RESEARCH_CANDIDATE','https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=600515','{"chainRole":"海南机场与基础设施","holdingsVerified":false}'),
('PUDONG_DEV','浦东新区','fund:008298','ETF_LINK_FUND','008298','华夏中证银行ETF联接A','FUND_CN','INDIRECT','https://www.chinaamc.com/fund/008298/index.shtml','{"chainRole":"金融服务，全国行业代理，不是浦东专属","verificationScope":"PRODUCT_IDENTITY_ONLY","holdingsVerified":false}'),
('PUDONG_DEV','浦东新区','stock:600663','STOCK','600663','陆家嘴','SSE','RESEARCH_CANDIDATE','https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=600663','{"chainRole":"浦东园区开发运营"}'),
('PUDONG_DEV','浦东新区','stock:600648','STOCK','600648','外高桥','SSE','RESEARCH_CANDIDATE','https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=600648','{"chainRole":"保税园区与贸易服务"}'),
('GEO_CRUDE_OIL','地缘与原油','fund:161129','LOF','161129','易方达原油A人民币','FUND_CN','INDIRECT','https://e.efunds.com.cn/cart/subscriptions?form=&fundCode=161129','{"chainRole":"境外原油QDII-LOF-FOF，非A股石油现价","verificationScope":"PRODUCT_IDENTITY_ONLY","holdingsVerified":false,"overseasCalendarRequired":true}'),
('GEO_CRUDE_OIL','地缘与原油','stock:600028','STOCK','600028','中国石化','SSE','RESEARCH_CANDIDATE','https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=600028','{"chainRole":"炼化与成品油，油价上升不必然利好"}'),
('AI_MEMORY_HBM','AI算力与HBM/DRAM','fund:008281','ETF_LINK_FUND','008281','国泰CES半导体芯片行业ETF联接A','FUND_CN','INDIRECT','https://e.gtfund.com/etrade/Jijin/view/id/008281','{"chainRole":"芯片产业代理，非纯HBM","verificationScope":"PRODUCT_IDENTITY_ONLY","holdingsVerified":false}'),
('AI_MEMORY_HBM','AI算力与HBM/DRAM','fund:320007','ACTIVE_FUND','320007','诺安成长混合A','FUND_CN','RESEARCH_CANDIDATE','https://mall.lionfund.com.cn/webapp/noMonetaryFund/main?fundCode=320007','{"chainRole":"主动半导体研究候选，最新仓位需披露核验","verificationScope":"PRODUCT_IDENTITY_ONLY","holdingsVerified":false,"highPositionPct":null}'),
('AI_MEMORY_HBM','AI算力与HBM/DRAM','stock:603986','STOCK','603986','兆易创新','SSE','RESEARCH_CANDIDATE','https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=603986','{"chainRole":"存储与控制器，HBM敞口需核验"}'),
('COMMERCIAL_SPACE','商业航天与卫星互联网','fund:028777','ETF_LINK_FUND','028777','招商中证卫星产业ETF发起式联接A','FUND_CN','INDIRECT','https://www.cmfchina.com/web/noticedetails/226382/index.html','{"chainRole":"卫星产业联接，新基金短历史需披露","verificationScope":"PRODUCT_IDENTITY_ONLY","holdingsVerified":false}'),
('COMMERCIAL_SPACE','商业航天与卫星互联网','fund:005609','ACTIVE_FUND','005609','富国军工主题混合A','FUND_CN','RESEARCH_CANDIDATE','https://www.fullgoal.com.cn/fundDetail/005609/index.html','{"chainRole":"军工主动候选，商业航天仓位需最新披露","verificationScope":"PRODUCT_IDENTITY_ONLY","holdingsVerified":false,"highPositionPct":null}'),
('COMMERCIAL_SPACE','商业航天与卫星互联网','etf:159218','ETF','159218','招商中证卫星产业ETF','SZSE','INDIRECT','https://static.cmfchina.com/web/fundDetail/159218/index.html','{"chainRole":"卫星产业ETF"}'),
('COMMERCIAL_SPACE','商业航天与卫星互联网','stock:600118','STOCK','600118','中国卫星','SSE','RESEARCH_CANDIDATE','https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=600118','{"chainRole":"卫星研制，订单与收入传导需核验"}');
INSERT INTO reference.theme_asset_graph(theme_code,theme_label,asset_key,asset_kind,asset_code,asset_name,market,relationship_type,source_uri,metadata)
SELECT theme_code,theme_label,'house:'||code,'REGIONAL_HOUSE_PRICE',code,label,'CN','DIRECT','https://www.stats.gov.cn/',jsonb_build_object('chainRole','区域政策传导观察，非可交易房产价格','city',city)
FROM (VALUES
 ('HAINAN_FTP','海南自贸港','HOUSE_HAIKOU_NEW_MOM','海口新房环比','海口'),
 ('HAINAN_FTP','海南自贸港','HOUSE_HAIKOU_SECOND_MOM','海口二手房环比','海口'),
 ('HAINAN_FTP','海南自贸港','HOUSE_SANYA_NEW_MOM','三亚新房环比','三亚'),
 ('HAINAN_FTP','海南自贸港','HOUSE_SANYA_SECOND_MOM','三亚二手房环比','三亚'),
 ('PUDONG_DEV','浦东新区','HOUSE_SHANGHAI_NEW_MOM','上海新房环比','上海'),
 ('PUDONG_DEV','浦东新区','HOUSE_SHANGHAI_SECOND_MOM','上海二手房环比','上海'),
 ('PUDONG_DEV','浦东新区','HOUSE_SHENZHEN_NEW_MOM','深圳新房环比（对照）','深圳'),
 ('PUDONG_DEV','浦东新区','HOUSE_SHENZHEN_SECOND_MOM','深圳二手房环比（对照）','深圳')
) AS x(theme_code,theme_label,code,label,city);
COMMIT;
