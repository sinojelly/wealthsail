BEGIN;

-- Representative research identities, not portfolio exposures or certified
-- index relationships. Official fund-manager pages reviewed on 2026-10-19.
CREATE TABLE reference.discovery_fund_universe (
    market TEXT NOT NULL CHECK (market='FUND_CN'),
    fund_code TEXT NOT NULL CHECK (fund_code ~ '^[0-9]{6}$'),
    fund_name TEXT NOT NULL CHECK (length(btrim(fund_name)) BETWEEN 1 AND 200),
    sector TEXT NOT NULL CHECK (sector ~ '^[A-Z][A-Z0-9_]{0,39}$'),
    fund_type TEXT NOT NULL CHECK (length(btrim(fund_type)) BETWEEN 1 AND 100),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 1000),
    overseas_calendar_required BOOLEAN NOT NULL DEFAULT false,
    enabled BOOLEAN NOT NULL DEFAULT true,
    source_uri TEXT NOT NULL CHECK (source_uri ~ '^https://[^[:space:]]+$' AND length(source_uri)<=2000),
    verified_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(market,fund_code)
);

INSERT INTO reference.discovery_fund_universe
    (market,fund_code,fund_name,sector,fund_type,priority,overseas_calendar_required,source_uri,verified_at)
SELECT 'FUND_CN',code,name,sector,kind,priority,overseas,source,'2026-10-19T00:00:00+08:00'::timestamptz
FROM (VALUES
 ('000051','华夏沪深300ETF联接A','BROAD','ETF联接基金',1,false,'https://www.chinaamc.com/fund/000051/index.shtml'),
 ('008298','华夏中证银行ETF联接A','BANK','ETF联接基金',2,false,'https://www.chinaamc.com/fund/008298/index.shtml'),
 ('012733','易方达中证人工智能主题ETF联接A','AI','ETF联接基金',3,false,'https://e.efunds.com.cn/cart/subscriptions?fundCode=012733'),
 ('320007','诺安成长混合A','ACTIVE_GROWTH','混合型',4,false,'https://mall.lionfund.com.cn/webapp/noMonetaryFund/main?fundCode=320007'),
 ('011035','嘉实中证稀土产业ETF联接A','RARE_EARTH','ETF联接基金',5,false,'https://www.jsfund.cn/main/a/20210712/482522.shtml'),
 ('004432','南方有色金属ETF联接A','NONFERROUS','ETF联接基金',6,false,'https://www.nffund.com/main/jjcp/fundproduct/004432.shtml'),
 ('004069','南方中证全指证券公司ETF联接A','BROKER','ETF联接基金',7,false,'https://www.nffund.com/new/personal-financing/detail.html?fundCode=004069'),
 ('013477','华宝中证金融科技主题ETF发起式联接A','FINTECH','ETF联接基金',8,false,'https://www.fsfund.com/fund/013477/fundDetail.shtml'),
 ('018344','华夏中证机器人ETF发起式联接A','ROBOT','ETF联接基金',9,false,'https://www.chinaamc.com/fund/018344/index.shtml'),
 ('003017','广发中证军工ETF联接A','DEFENCE','ETF联接基金',10,false,'https://gfwx.gffunds.com.cn/funds/?fundcode=003017'),
 ('008086','华夏中证5G通信主题ETF联接A','COMMUNICATION','ETF联接基金',11,false,'https://www.chinaamc.com/fund/008086/index.shtml'),
 ('012737','广发创新药ETF联接A','INNOVATIVE_DRUG','ETF联接基金',12,false,'https://gfwx.gffunds.com.cn/funds/?fundcode=012737'),
 ('009179','嘉实中证主要消费ETF发起联接A','CONSUMPTION','ETF联接基金',13,false,'https://www.jsfund.cn/main/a/20211103/482909.shtml'),
 ('000216','华安黄金ETF联接A','GOLD','ETF联接基金',14,false,'https://www.huaan.com.cn/funds/000216/index.shtml'),
 ('162411','华宝油气LOF','GLOBAL_ENERGY','QDII-LOF',15,true,'https://www.fsfund.com/fund/162411/fundDetail.shtml'),
 ('270042','广发纳指100ETF联接（QDII）人民币A','US_TECH','QDII-ETF联接基金',16,true,'https://gfwx.gffunds.com.cn/funds/?fundcode=270042')
) AS v(code,name,sector,kind,priority,overseas,source);

-- No fabricated instrument UUID, price history, current holdings or relation.
INSERT INTO reference.fund_master(fund_code,fund_name,fund_type)
SELECT fund_code,fund_name,fund_type FROM reference.discovery_fund_universe
ON CONFLICT(fund_code) DO NOTHING;

REVOKE ALL ON reference.discovery_fund_universe FROM PUBLIC,authenticated;
GRANT SELECT ON reference.discovery_fund_universe TO wealthsail_api,wealthsail_worker;
COMMENT ON TABLE reference.discovery_fund_universe IS
    'Officially reviewed public product identities and research industries only. Runtime read only; no holdings, tracking relation or trading eligibility inferred.';

COMMIT;
