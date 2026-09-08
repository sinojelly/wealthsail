import { render, screen, within } from "@testing-library/react";
import ReportMarkdown from "./ReportMarkdown";

const summary = `## 持仓概览

| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 当日 | 仓位 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 600036 | 招商银行 | 8,500股 | 38.82 | 39.15 | +0.84% | 34.85% |
| 510300 | 沪深300ETF | 62,000份 | 4.14 | 4.12 | -0.31% | 29.68% |`;

it("renders a GFM table and A-share rise/fall colors", () => {
  render(<ReportMarkdown>{summary}</ReportMarkdown>);

  const table = screen.getByRole("table");
  expect(within(table).getByText("+0.84%")).toHaveClass("report-value-up");
  expect(within(table).getByText("-0.31%")).toHaveClass("report-value-down");
  expect(within(table).getByText("34.85%")).not.toHaveClass(
    "report-value-up",
  );
});

it("does not execute raw HTML from a report", () => {
  render(
    <ReportMarkdown>{`<img src=x onerror="window.__reportXss=true">`}</ReportMarkdown>,
  );

  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
