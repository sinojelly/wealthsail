import { render, screen, within } from "@testing-library/react";
import ReportMarkdown from "./ReportMarkdown";

const summary = `## 持仓概览

| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 仓位 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 600036 | 招商银行⚠️ | 8,500股 | 38.82 | 39.15（持仓 +0.85%） | 332,775 | +0.84% | 34.85% |
| 510300 | 沪深300ETF | 62,000份 | 4.14 | 4.12（持仓 -0.48%） | 255,440 | -0.31% | 29.68% |
| 110022 | 易方达消费行业 | 100份 | 1.50 | --（持仓 --） | 200 | -- | 20.00% |`;

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
  expect(within(table).getByText("34.85%")).not.toHaveClass(
    "report-value-up",
  );
  expect(within(table).getByText("易方达消费行业")).toBeInTheDocument();
  expect(screen.getByText("盈利 / 上涨")).toBeInTheDocument();
  expect(screen.getByText("亏损 / 下跌")).toBeInTheDocument();
});

it("does not execute raw HTML from a report", () => {
  render(
    <ReportMarkdown>{`<img src=x onerror="window.__reportXss=true">`}</ReportMarkdown>,
  );

  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
