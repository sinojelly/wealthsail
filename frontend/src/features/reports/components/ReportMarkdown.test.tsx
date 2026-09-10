import { render, screen, within } from "@testing-library/react";
import ReportMarkdown from "./ReportMarkdown";

const summary = `## 持仓概览

| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 仓位 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 600036 | 招商银行⚠️ | 8,500股 | 38.82 | 39.15（持仓 +0.85%） | 332,775 | +0.84% | 34.85% |
| 510300 | 沪深300ETF | 62,000份 | 4.14 | 4.12（持仓 -0.48%） | 255,440 | -0.31% | 29.68% |
| 110022 | 易方达消费行业⚠️ | 100份 | 1.50 | --（持仓 --） | 200 | -1.03%（9/7） | 20.00% |`;

function nineColumnSummary(marketValue: string): string {
  return `## 持仓概览

| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 连续变化 | 仓位 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 600036 | 招商银行 | 100股 | 38.82 | 39.15（持仓 +0.85%） | ${marketValue} | +0.84% | 📈 连涨2天 (+3.00%) | 34.85% |`;
}

const fundEstimateSummary = `📊 今日基金估值（数据截至 14:51）：

| 基金名称 | 代码 | 当日预估 | 市值 | 累计盈亏 | 连续变化 |
| --- | --- | ---: | ---: | ---: | --- |
| 易方达消费中 | 110011 | +4.10% | 1,041.00 | +21.00 | 📉连跌2天(-2.10%) |
| 招商中证白酒 | 161725 | -4.00% | 768.00 | -32.00 | 📉连跌3天(-3.20%) |`;

const fundEstimateWithSpacing = `📊 今日基金估值（数据截至 14:51）：

| 基金名称 | 代码 | 当日预估涨跌 | 估算市值 | 连续变化 |
| --- | --- | ---: | ---: | --- |
| 易方达消费中（估） | 110011 | +4.10% | 336,397.17  （累计盈亏 +131529.67） | 📉连跌2天(-2.10%) |`;

it("renders a structured holdings table with complete aligned values", () => {
  render(<ReportMarkdown>{summary}</ReportMarkdown>);

  const table = screen.getByRole("table");
  expect(table).toHaveClass("report-holdings-table");
  expect(within(table).getByText("市值")).toBeInTheDocument();
  expect(within(table).getByText("39.15")).toHaveClass(
    "report-holding-price-value",
  );
  expect(within(table).getByText("+0.85%")).toHaveClass(
    "report-value-up",
    "report-holding-return",
  );
  expect(within(table).getByText("+0.84%")).toHaveClass("report-value-up");
  expect(within(table).getByText("-0.31%")).toHaveClass("report-value-down");
  expect(within(table).getByText("-1.03%（9/7）")).toHaveClass(
    "report-value-down",
  );
  expect(within(table).getByText("34.85%")).not.toHaveClass(
    "report-value-up",
  );
  expect(within(table).getByText("易方达消费行业⚠️")).toBeInTheDocument();
  expect(screen.getByText("盈利 / 上涨")).toBeInTheDocument();
  expect(screen.getByText("亏损 / 下跌")).toBeInTheDocument();
});

it("does not execute raw HTML from a report", () => {
  render(
    <ReportMarkdown>{`<img src=x onerror="window.__reportXss=true">`}</ReportMarkdown>,
  );

  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("renders a market value with red profit below it", () => {
  render(<ReportMarkdown>{nineColumnSummary("185,000（盈亏 +1,234.56）")}</ReportMarkdown>);

  expect(screen.getByText("185,000")).toHaveClass("report-holding-market-value");
  expect(screen.getByText("+1,234.56")).toHaveClass(
    "report-holding-pnl",
    "report-pnl-profit",
  );
});

it.each([
  ["-1,234.56", ["report-pnl-loss", "report-value-down"]],
  ["0.00", ["report-pnl-flat"]],
  ["--", ["report-pnl-flat"]],
])("renders %s using %s", (amount, classNames) => {
  render(<ReportMarkdown>{nineColumnSummary(`185,000（盈亏 ${amount}）`)}</ReportMarkdown>);

  expect(screen.getByText(amount)).toHaveClass(...classNames);
});

it("renders the FUND_1430 audit summary as a colored estimate table", () => {
  render(<ReportMarkdown>{fundEstimateSummary}</ReportMarkdown>);

  const table = screen.getByRole("table");
  expect(table).toHaveClass(
    "report-holdings-table",
    "report-fund-estimate-table",
  );
  expect(within(table).getByText("基金名称")).toBeInTheDocument();
  expect(within(table).getByText("+4.10%")).toHaveClass("report-value-up");
  expect(within(table).getByText("-4.00%")).toHaveClass("report-value-down");
  expect(within(table).getByText("1,041.00")).toHaveClass("report-holding-market-value");
  expect(within(table).getByText("+21.00")).toHaveClass(
    "report-fund-estimate-pnl-value",
    "report-pnl-profit",
  );
  expect(within(table).getByText("-32.00")).toHaveClass(
    "report-fund-estimate-pnl-value",
    "report-pnl-loss",
  );
  expect(within(table).getByText("📉连跌2天(-2.10%)")).toBeInTheDocument();
});

it("splits a spaced fund estimate value into a two-line colored market cell", () => {
  render(<ReportMarkdown>{fundEstimateWithSpacing}</ReportMarkdown>);

  const table = screen.getByRole("table");
  expect(table).toHaveClass("report-holdings-table");
  expect(within(table).getByText("336,397.17")).toHaveClass(
    "report-holding-market-value",
    "report-fund-estimate-market-value",
  );
  expect(within(table).getByText("+131529.67")).toHaveClass(
    "report-holding-pnl",
    "report-pnl-profit",
    "report-fund-estimate-pnl",
  );
  expect(
    within(table).queryByText("336,397.17  （累计盈亏 +131529.67）"),
  ).not.toBeInTheDocument();
});
