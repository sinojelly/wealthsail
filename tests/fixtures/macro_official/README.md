# Official macro protocol fixtures

These are reduced **synthetic test fixtures**, never economic seed data. Dates
and numbers were changed for deterministic tests. They retain only the relevant
official HTML/RSS structure; production discovers links and parses actual pages.

The following official protocols were checked read-only on 2026-10-14:

- [NBS CPI release](https://www.stats.gov.cn/sj/zxfb/202610/t20261014_1965477.html):
  `ArticleTitle`/`PubDate` metadata and `.TRS_UEDITOR` body; identical desktop and
  mobile copies must not create conflicting values or duplicate observations.
- [NBS PPI release](https://www.stats.gov.cn/sj/zxfb/202610/t20261014_1965476.html):
  national factory-output year-on-year rate, distinct from input-price, monthly
  and cumulative-average rates.
- [NBS manufacturing PMI release](https://www.stats.gov.cn/sj/zxfb/202609/t20260930_1965449.html):
  manufacturing headline, distinct from nonmanufacturing and composite indices.
- [NBS annual calendar](https://www.stats.gov.cn/xw/tjxw/tzgg/202512/t20251224_1962137.html):
  month columns, day/time rows with rowspans, duplicate mobile table and explicit
  Spring Festival exception. Several release dates can occupy one calendar month;
  plans are never actual publication timestamps.
- [PBoC quarterly financial statistics](https://www.pbc.gov.cn/diaochatongjisi/116219/116225/2026101417073743604/index.html):
  `#zoom` contains the month-end M2 growth rate and `#shijian` the actual time.
  `PubDate` can contain only a date; `createDate` is page generation, not release.
  Quarterly, half-year and annual report titles represent March, June, September
  and December observations. No precise future M2 release calendar was verified.
- [Fed RSS catalogue](https://www.federalreserve.gov/feeds/feeds.htm) registers
  [monetary-policy RSS](https://www.federalreserve.gov/feeds/press_monetary.xml).
  RFC-style `pubDate` supplies its own timezone; article links must stay on the
  official host and description HTML is removed from the bounded short summary.

Ordinary verified HTTPS returned the NBS/PBoC pages and Fed RSS. One concurrent
HTTPX collector check encountered a transient Fed `ConnectError` and correctly
reported only that source as FAILED; an isolated check through the same adapter
then returned 200 and parsed 15 monetary-policy events, retaining GMT timestamps.
TLS verification was preserved throughout. Tests run without network access.
