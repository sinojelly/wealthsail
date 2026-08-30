import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { FundRangeMetricsDto, HoldingPeriodDto } from "../../api/types";
import FundRangeMetrics from "./components/FundRangeMetrics";

const holdingPeriod: HoldingPeriodDto = {
  startedOn: "2024-03-16",
  days: 895,
  method: "TRANSACTION_RECONCILED",
  confidence: "HIGH",
  canConfirm: true,
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
  expect(screen.getByText("高可信")).toBeInTheDocument();
  expect(screen.getByText("+9.92%")).toBeInTheDocument();
  expect(screen.getByText("距区间最高 -20%")).toBeInTheDocument();
  expect(screen.getByText("较区间最低 +20%")).toBeInTheDocument();
  expect(
    screen.getByText(/最近公布的官方单位净值，不是盘中估值/),
  ).toBeInTheDocument();
});

it("labels portfolio activity estimates as low confidence", async () => {
  const user = userEvent.setup();
  render(
    <FundRangeMetrics
      holdingPeriod={{
        ...holdingPeriod,
        startedOn: "2021-01-26",
        method: "PORTFOLIO_ACTIVITY_ESTIMATE",
        confidence: "LOW",
      }}
      metrics={{ ...readyMetrics, status: "PARTIAL" }}
      currentNav="1.4400"
    />,
  );

  await user.click(screen.getByRole("button", { name: /持有 895 天/ }));
  expect(screen.getByText("组合/调仓估算")).toBeInTheDocument();
  expect(screen.getByText("低可信")).toBeInTheDocument();
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
        confidence: "NONE",
        canConfirm: false,
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

it("explains an estimated holding start and lets the user confirm or correct it", async () => {
  const user = userEvent.setup();
  const confirmHoldingStart = vi.fn().mockResolvedValue(undefined);
  render(
    <FundRangeMetrics
      holdingPeriod={{
        ...holdingPeriod,
        startedOn: "2021-01-26",
        method: "PORTFOLIO_ACTIVITY_ESTIMATE",
        confidence: "LOW",
        canConfirm: true,
      }}
      metrics={{
        ...readyMetrics,
        status: "PARTIAL",
        qualityReasons: ["PARTIAL_HOLDING_HISTORY"],
      }}
      currentNav="1.4400"
      onConfirmHoldingStart={confirmHoldingStart}
    />,
  );

  expect(screen.getByText("持有起点为估算")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /持有 895 天/ }));
  expect(
    screen.getByText(/当前区间按系统推导的持有开始日计算/),
  ).toBeInTheDocument();
  const dateInput = screen.getByLabelText("确认持有开始日期");
  expect(dateInput).toHaveValue("2021-01-26");
  await user.clear(dateInput);
  await user.type(dateInput, "2021-02-23");
  await user.click(screen.getByRole("button", { name: "确认并后台重算" }));
  expect(confirmHoldingStart).toHaveBeenCalledWith("2021-02-23");
});
