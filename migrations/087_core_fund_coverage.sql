BEGIN;

-- Supplement the verified public representative pool. These are corrected
-- product identities, not the mismatched codes in the review text.
INSERT INTO reference.discovery_fund_universe
    (market,fund_code,fund_name,sector,fund_type,priority,overseas_calendar_required,source_uri,verified_at)
VALUES
    ('FUND_CN','021607','南方上证科创板芯片ETF发起联接A','SCI_TECH_CHIP','ETF联接基金',17,false,
     'https://www.nffund.com/main/files/2024/06/28/679551834589.pdf','2026-10-19T00:00:00+08:00'),
    ('FUND_CN','004854','广发中证全指汽车指数A','MOBILITY','指数型',18,false,
     'https://www.gffunds.com.cn/funds/?fundcode=004854','2026-10-19T00:00:00+08:00'),
    ('FUND_CN','007466','华泰柏瑞中证红利低波ETF联接A','DIVIDEND_LOW_VOL','ETF联接基金',19,false,
     'https://static.cninfo.com.cn/finalpage/2025-01-22/1222391389.PDF','2026-10-19T00:00:00+08:00'),
    ('FUND_CN','000962','天弘中证500ETF联接A','BROAD_500','ETF联接基金',20,false,
     'https://cdn-thweb.tianhongjijin.com.cn/fundnotice/1eae355203611701dd25252bb34144af.pdf','2026-10-19T00:00:00+08:00'),
    -- Reuse the already verified legacy product identity; do not claim a new
    -- verification of current holdings or a new product-index relationship.
    ('FUND_CN','008281','国泰CES半导体芯片行业ETF联接A','SEMICONDUCTOR','ETF联接基金',21,false,
     'https://e.gtfund.com/etrade/Jijin/view/id/008281','2026-10-17T00:00:00+08:00');

INSERT INTO reference.fund_master(fund_code,fund_name,fund_type)
SELECT fund_code,fund_name,fund_type FROM reference.discovery_fund_universe
WHERE fund_code IN ('021607','004854','007466','000962','008281')
ON CONFLICT(fund_code) DO NOTHING;

COMMIT;
