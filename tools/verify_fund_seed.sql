-- 跑完 `python -m scripts.migrate` 后，用 psql 执行本文件确认 055 已落地：
--   psql "$MIGRATION_DATABASE_URL" -f tools/verify_fund_seed.sql
-- 全部应为非零；fund_lookthrough 的 6 只基金为指数/ETF 联接基金，
-- 7 只主动基金待你给季报前五大后用 scripts/ingest_fund_holdings.py 灌入。

\echo '== 12 只场外基金是否注册（应为 12）=='
SELECT count(*) AS funds_registered
  FROM reference.instruments
 WHERE asset_type = 'OFF_EXCHANGE_FUND'
   AND canonical_name IN (
        '001632 天弘中证食品饮料ETF联接C','012733 易方达中证人工智能主题ETF联接A',
        '011103 天弘中证光伏产业指数C','000689 前海开源新经济混合A','008552 东财医药C',
        '161726 招商国证生物医药指数(LOF)A','070019 嘉实价值优势混合A',
        '002593 富国美丽中国混合A','008295 朱雀企业优胜C',
        '004476 景顺长城沪港深领先科技股票A','001705 泓德战略转型股票',
        '005275 中欧创新成长灵活配置混合A');

\echo '== TICKER 别名（应为 12 基金 + 22 持仓股票 = 34）=='
SELECT count(*) FILTER (
    WHERE alias_type = 'TICKER'
      AND alias_value IN ('001632','012733','011103','000689','008552','161726',
        '070019','002593','008295','004476','001705','005275')
) AS fund_aliases,
       count(*) FILTER (
    WHERE alias_type = 'TICKER'
      AND alias_value IN ('600519','000858','000568','600809','000596','300760',
        '603259','600276','300015','300122','603288','600887','601012','600438',
        '002129','300274','002459','002230','002415','603019','688111','000938')
) AS stock_aliases
  FROM reference.instrument_aliases;

\echo '== 申赎状态 fund_profiles（12 只基金应各 1 行 = 12）=='
SELECT count(DISTINCT fp.instrument_id) AS fund_profiles_rows
  FROM reference.fund_profiles fp
  JOIN reference.instruments i ON i.id = fp.instrument_id
 WHERE i.asset_type = 'OFF_EXCHANGE_FUND';

\echo '== 申赎截止规则 instrument_cutoff_rules（应为 12）=='
SELECT count(*) AS cutoff_rules_rows
  FROM reference.instrument_cutoff_rules cr
  JOIN reference.instruments i ON i.id = cr.instrument_id
 WHERE i.asset_type = 'OFF_EXCHANGE_FUND';

\echo '== fund_lookthrough 持仓穿透（应有 30 行：5 指数基金 25 + 008552 5）=='
SELECT count(*) AS lookthrough_rows,
       count(DISTINCT fund_instrument_id) AS funds_with_holdings
  FROM market.fund_lookthrough
 WHERE report_period = DATE '2026-06-30';

\echo '== 这 12 只基金是否出现在你的持仓中（FUND_1430 只扫描持仓 type=场外基金）=='
SELECT i.canonical_name,
       EXISTS (
           SELECT 1 FROM ledger.position_snapshots ps
            WHERE ps.instrument_id = i.id
       ) AS has_position_rows
  FROM reference.instruments i
 WHERE asset_type = 'OFF_EXCHANGE_FUND'
   AND canonical_name IN (
        '001632 天弘中证食品饮料ETF联接C','012733 易方达中证人工智能主题ETF联接A',
        '011103 天弘中证光伏产业指数C','000689 前海开源新经济混合A','008552 东财医药C',
        '161726 招商国证生物医药指数(LOF)A','070019 嘉实价值优势混合A',
        '002593 富国美丽中国混合A','008295 朱雀企业优胜C',
        '004476 景顺长城沪港深领先科技股票A','001705 泓德战略转型股票',
        '005275 中欧创新成长灵活配置混合A')
 ORDER BY canonical_name;
