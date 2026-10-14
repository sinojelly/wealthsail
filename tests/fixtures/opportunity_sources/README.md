# Public official source protocol fixtures

These are deliberately small, **synthetic** XML/HTML documents. Their dates,
balances and rates are test inputs, not verified economic facts or seed data.
Production obtains facts only from an accepted official document at collection
time. Tests make no external requests.

## Treasury

The [official XML feed documentation](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed)
documents anonymous GET requests for annual and monthly feeds. Production uses
four annual requests when needed and filters them to a rolling three-year
window in `America/New_York`, including the anniversary date. February 29
anniversaries use February 28 in a non-leap year. The common Atom/EDM schema is
represented in `treasury_nominal.xml` and `treasury_real.xml`:

- Nominal feed `daily_treasury_yield_curve`: `NEW_DATE`, `BC_10YEAR`.
- TIPS real feed `daily_treasury_real_yield_curve`: `NEW_DATE`, `TC_10YEAR`.
- Dates remain literal New York market dates. Neither midnight date fields nor
  feed `updated` fields supply an actual publication timestamp.
- Values are yields in percent. Negative real yields and zero yields are valid;
  missing/null values are not zero. Nominal and real yields have distinct,
  stable definition keys. Annual URL changes do not change their definition.

The [Treasury methodology](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology)
identifies the monotone convex methodology change on 2021-12-06. The adapter
records this definition for the current rolling window; a future methodology
change requires explicit registration and tests. Typical update times are not
used to invent `publishedAt`.

On 2026-10-14 an anonymous, strict-TLS read of the actual annual feeds returned
748 observations per series for the rolling three-year window. Earlier reads
timed out. This is a source-contract check, not a statement of historical
publication-time coverage or a persistent database import.

## People's Bank of China

The [national statistics report directory](https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html)
lists financial statistics and separate total-social-financing stock reports.
`pboc_combined.html` models national report metadata (`ArticleTitle`, `PubDate`),
the actual time field `#shijian`, and report body `#zoom`. `createDate` does not
establish publication. Date-only publication uses retrieval-time availability.

The [actual 2026 first-three-quarters report](https://www.pbc.gov.cn/diaochatongjisi/116219/116225/2026101417073743604/index.html)
was inspected read-only for these structural contracts:

- M1 is explicitly the new January-2025 definition, including personal demand
  deposits and non-bank payment institutions' customer reserve funds. Its
  explicit 2024 comparable backcast table has month headers and YoY percent
  rows. Table balances are not growth rates. No 2020–2023 values are fabricated
  or joined; backcast observations retain the later document's availability.
- TSF is the end-period stock YoY, not the cumulative financing flow. Separate
  stock and combined financial reports share its stable definition.
- Corporate / public-institution medium- and long-term loans are cumulative net
  additions from January to the report month. Their paragraph must state the
  matching YTD period. Household loans and monthly-only changes are excluded.
  Amounts in 亿元 are divided by 10,000 to obtain 万亿元.

The [M1 definition announcement, 2024 No. 17](https://www.pbc.gov.cn/chubanwu/fileDir/resource/cms/2025/06/2025062416082199683.pdf)
is the definition reference, not a blanket release schedule. The adapter does
not infer a monthly release day. Subsequent live requests returned HTTP 403;
this is reported per source as FAILED, while stored facts remain untouched.
No challenge, credential, or access restriction is bypassed.

## Permissions and failure behavior

[ChinaBond](https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=CN) and
[CFETS legal notice](https://www.chinamoney.com.cn/chinese/legaldeclaration/)
require written permissions for the proposed data use. The user confirmed that
these permissions are absent. This build has no licensed clients: both source
results are PERMISSION_REQUIRED and **zero requests** are sent, including when
an unverified caller supplies an APPROVED field.

Each public source has a 45-second total budget, runs independently, accepts
only canonical official HTTPS URLs and same-publisher redirects, and reads at
most 2 MiB per document. PBoC sources inspect two recent eligible periods, with
at most two documents per period to detect same-period conflicts. A financial
report that does not include TSF stock is not treated as a stock release. M1
report candidates before the new 2025 definition are excluded before selection.
This is gradual official history collection, not a claim of complete monthly
coverage since 2025. Failed sources emit no replacement observations;
latest bad values cannot fall back to older current values. The adapter neither
deletes facts nor accesses the database.
