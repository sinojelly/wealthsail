import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { FundRangeMetricsDto, HoldingPeriodDto } from "../../api/types";
import FundRangeMetrics from "./components/FundRangeMetrics";

const holdingPeriod: HoldingPeriodDto = {
  startedOn: "2024-03-16",
  days: 895,
  method: "TRANSACTION_RECONCILED",
};

const readyMetrics: FundRangeMetricsDto = {
  high: "1.8000",
  highDate: "2025-10-08",
  low: "1.2000",
  lowDate: "2024-04-19",
  fromHighPct: "-20.0000",
  fromLowPct: "20.0000",
  rangePositionPct: "40.0000",
  status: "READY",
  qualityReasons: [],
  asOf: "2026-08-27",
};

it("keeps the holding table compact and expands official unit-NAV range facts", async () => {
  const user = userEvent.setup();
  render(
    <FundRangeMetrics
      holdingPeriod={holdingPeriod}
      metrics={readyMetrics}
      currentNav="1.4400"
      costReturnPct="9.9200"
    />,
  );

  const toggle = screen.getByRole("button", {
    name: "持有 895 天，区间位置 40%",
  });
  expect(toggle).toHaveAttribute("aria-expanded", "false");

  await user.click(toggle);

  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("1.8000")).toBeInTheDocument();
  expect(screen.getByText("2025-10-08")).toBeInTheDocument();
  expect(screen.getByText("1.2000")).toBeInTheDocument();
  expect(screen.getByText("2024-04-19")).toBeInTheDocument();
  expect(screen.getByText("流水对账推导")).toBeInTheDocument();
  expect(screen.getByText("+9.92%")).toBeInTheDocument();
  expect(screen.getByText("距区间最高 -20%")).toBeInTheDocument();
  expect(screen.getByText("较区间最低 +20%")).toBeInTheDocument();
  expect(
    screen.getByText(/最近公布的官方单位净值，不是盘中估值/),
  ).toBeInTheDocument();
});

it("renders a conflict as an explicit quality state rather than a precise conclusion", async () => {
  const user = userEvent.setup();
  render(
    <FundRangeMetrics
      holdingPeriod={{ ...holdingPeriod, method: "MISMATCH" }}
      metrics={{
        ...readyMetrics,
        status: "CONFLICT",
        rangePositionPct: null,
        qualityReasons: ["来源持仓起点与流水对账结果冲突"],
      }}
      currentNav="1.4400"
    />,
  );

  expect(screen.getByText("数据口径冲突")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /持有 895 天/ }));
  expect(
    screen.getByText("来源持仓起点与流水对账结果冲突"),
  ).toBeInTheDocument();
  expect(screen.queryByText("区间位置 0%")).not.toBeInTheDocument();
});

it("never turns unavailable pending metrics into a misleading zero", () => {
  render(
    <FundRangeMetrics
      holdingPeriod={{
        startedOn: null,
        days: null,
        method: "INSUFFICIENT_DATA",
      }}
      metrics={{
        ...readyMetrics,
        high: null,
        highDate: null,
        low: null,
        lowDate: null,
        fromHighPct: null,
        fromLowPct: null,
        rangePositionPct: null,
        status: "PENDING",
        asOf: null,
      }}
      currentNav={null}
    />,
  );

  expect(screen.getByText("区间指标计算中")).toBeInTheDocument();
  expect(screen.getByText("持有起点待确认")).toBeInTheDocument();
  expect(screen.queryByText(/0%/)).not.toBeInTheDocument();
});

it("surfaces partial coverage and its quality reason beside usable facts", async () => {
  const user = userEvent.setup();
  render(
    <FundRangeMetrics
      holdingPeriod={{ ...holdingPeriod, method: "PARTIAL_HISTORY" }}
      metrics={{
        ...readyMetrics,
        status: "PARTIAL",
        qualityReasons: ["基金净值序列起始晚于持有起点"],
      }}
      currentNav="1.4400"
    />,
  );

  expect(screen.getByText("数据不完整")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /持有 895 天/ }));
  expect(screen.getByText("基金净值序列起始晚于持有起点")).toBeInTheDocument();
});
