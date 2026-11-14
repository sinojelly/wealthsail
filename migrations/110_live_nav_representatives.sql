-- Verified CNY share classes for the live representative pool. These seeds
-- add product identities, with no synthetic NAV or asset exposure rows.
INSERT INTO reference.discovery_fund_universe
    (market,fund_code,fund_name,sector,fund_type,priority,overseas_calendar_required,source_uri,verified_at)
VALUES
    ('FUND_CN','161032','富国中证煤炭指数(LOF)A','COAL','指数型-LOF',22,false,
     'https://static.cninfo.com.cn/finalpage/2026-07-21/1225433952.PDF','2026-11-15T00:00:00+08:00'),
    ('FUND_CN','161724','招商中证煤炭等权指数A','COAL','指数型-LOF',23,false,
     'https://cmfchina.com/web/fundDetail/161724/index.html','2026-11-15T00:00:00+08:00'),
    ('FUND_CN','168204','国联中证煤炭指数(LOF)A','COAL','指数型-LOF',24,false,
     'https://static.cninfo.com.cn/finalpage/2026-07-21/1225432505.PDF','2026-11-15T00:00:00+08:00'),
    ('FUND_CN','012552','天弘芯片产业ETF联接A','SEMICONDUCTOR','ETF联接基金',25,false,
     'https://static.cninfo.com.cn/finalpage/2025-07-22/1224241681.PDF','2026-11-15T00:00:00+08:00'),
    ('FUND_CN','012629','广发国证半导体芯片ETF联接A','SEMICONDUCTOR','ETF联接基金',26,false,
     'https://gfwx.gffunds.com.cn/funds/?fundcode=012629','2026-11-15T00:00:00+08:00'),
    ('FUND_CN','001092','广发生物科技指数人民币(QDII)A','BIOTECH','QDII-指数型',27,true,
     'https://www.gffunds.com.cn/jjgg/dqgg/202503/P020250329278767829700.pdf','2026-11-15T00:00:00+08:00')
ON CONFLICT (market,fund_code) DO NOTHING;

INSERT INTO reference.fund_master(fund_code,fund_name,fund_type)
SELECT fund_code,fund_name,fund_type FROM reference.discovery_fund_universe
WHERE market='FUND_CN' AND fund_code IN ('161032','161724','168204','012552','012629','001092')
ON CONFLICT(fund_code) DO NOTHING;
