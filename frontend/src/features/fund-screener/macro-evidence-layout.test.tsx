import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type {
  OpportunityContext,
  OpportunitySeries,
} from "../../api/investment";
import MacroEvidencePanel from "./components/MacroEvidencePanel";
import MacroContextStrip from "./components/MacroContextStrip";
import { SeriesCard } from "./components/OpportunityRadar";

const pmi: OpportunityContext["items"][number] = {
  code: "PMI",
  label: "制造业 PMI",
  value: 50.14,
  unit: "指数点",
  state: "READY",
  asOf: "2026-09-01",
  referencePeriod: "2026-09",
  source: "国家统计局",
  sourceUri: "https://www.stats.gov.cn/pmi",
  publishedAt: null,
  publicationStatus: "UNKNOWN",
  valueVerification: "VERIFIED",
  periodChange: 0.3,
  benchmark: { value: 50, label: "扩张与收缩分界" },
  statistics: {
    previousValue: 49.84,
    change3Months: 0.7,
    change6Months: null,
    percentile: 61.25,
    median: 49.9,
    sampleCount: 36,
    firstPeriod: "2023-10",
    lastPeriod: "2026-09",
    missingPeriods: [],
  },
  history: [
    { asOf: "2026-08-01", value: 49.84, unit: "指数点", source: "国家统计局" },
    { asOf: "2026-09-01", value: 50.14, unit: "指数点", source: "国家统计局" },
  ],
};

function series(code: string, label = code): OpportunitySeries {
  return {
    code,
    label,
    frequency: "DAILY",
    value: 1.91,
    unit: "%",
    asOf: "2026-10-16",
    sourceName: "官方来源",
    sourceUri: "https://example.org/rates",
    publishedAt: null,
    retrievedAt: "2026-10-16T10:00:00Z",
    availableAt: "2026-10-16T10:00:00Z",
    timeBasis: "RETRIEVAL_ONLY",
    methodVersion: "actual-observations-v1",
    state: "READY",
    changes: {
      previousValue: 1.9,
      change5Samples: 2,
      change20Samples: null,
      changeUnit: "bp",
      median: 1.85,
      percentile: 60,
      sampleCount: 20,
    },
    history: [
      { date: "2026-10-15", value: 1.9, state: "READY" },
      { date: "2026-10-16", value: 1.91, state: "READY" },
    ],
  };
}

let resizeCallbacks: Array<{
  callback: ResizeObserverCallback;
  target: Element;
}>;
beforeEach(() => {
  resizeCallbacks = [];
  vi.stubGlobal(
    "ResizeObserver",
    class {
      callback: ResizeObserverCallback;
      constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
      }
      observe(target: Element) {
        resizeCallbacks.push({ callback: this.callback, target });
      }
      disconnect() {}
      unobserve() {}
    },
  );
});
afterEach(() => vi.unstubAllGlobals());

function resizePlot(graph: HTMLElement, width: number) {
  act(() => {
    for (const { callback, target } of resizeCallbacks) {
      if (target.contains(graph) || target === graph) {
        callback(
          [{ target, contentRect: { width } } as ResizeObserverEntry],
          {} as ResizeObserver,
        );
      }
    }
  });
}

it("opens just one full evidence detail outside the compact metric grid", async () => {
  const user = userEvent.setup();
  render(
    <MacroEvidencePanel
      data={{
        state: "READY",
        items: [pmi],
        series: [series("CN_T10", "中债利率")],
      }}
      renderMacro={() => <figure role="img" aria-label="PMI详情图" />}
    />,
  );
  await user.click(
    screen.getByRole("button", { name: "查看制造业 PMI完整证据" }),
  );
  const first = screen.getByRole("img", { name: "PMI详情图" });
  expect(first.closest(".op-compact-metrics")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "查看中债利率完整证据" }),
  );
  expect(
    screen.queryByRole("img", { name: "PMI详情图" }),
  ).not.toBeInTheDocument();
  expect(screen.getAllByRole("img")).toHaveLength(1);
  expect(screen.getByRole("img").closest(".op-compact-metrics")).toBeNull();
});

it("groups actual evidence by economic question and prioritizes dollar rates for gold", () => {
  render(
    <MacroEvidencePanel
      marketId="gold"
      data={{
        state: "READY",
        items: [
          pmi,
          { ...pmi, code: "CPI_YOY", label: "CPI同比", value: 0.2, unit: "%" },
        ],
        series: [
          series("CN_T10"),
          series("US_T10_NOMINAL"),
          series("US_T10_REAL"),
          series("DXY"),
          series("USD_CNY"),
          series("M1_YOY"),
        ],
      }}
      renderMacro={() => null}
    />,
  );
  const core = screen.getByRole("region", { name: "核心指标" });
  expect(within(core).getAllByRole("article")[0]).toHaveAccessibleName(
    "US_T10_REAL",
  );
  expect(within(core).getAllByRole("article")).toHaveLength(6);
  expect(within(core).getByText(/经济增长/)).toBeInTheDocument();
  expect(within(core).getByText(/通胀与成本/)).toBeInTheDocument();
  expect(within(core).getAllByText(/全球风险与市场/).length).toBeGreaterThan(0);
  expect(
    screen.getByRole("button", { name: /利率与信用/ }),
  ).toBeInTheDocument();
  expect(within(core).getByText("50.1 指数点")).toBeInTheDocument();
});

it("resizes monthly plot coordinates to the measured parent without stretching its labels", async () => {
  render(
    <MacroContextStrip data={{ state: "READY", items: [pmi] }} error={false} />,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: /市场环境概览/ }));
  const graph = screen.getByRole("img", { name: "制造业 PMI 历史走势" });
  resizePlot(graph, 840);
  expect(graph).toHaveAttribute("viewBox", "0 0 840 280");
  expect(graph).toHaveAttribute("height", "280");
  expect(graph).not.toHaveAttribute("preserveAspectRatio", "none");
  expect(graph.querySelectorAll("circle")[1]).toHaveAttribute("cx", "820");
  resizePlot(graph, 320);
  expect(graph).toHaveAttribute("viewBox", "0 0 320 280");
  expect(graph.querySelectorAll("circle")[1]).toHaveAttribute("cx", "300");
});

it("uses the same parent-width geometry for market series and keeps its real statistics", () => {
  render(<SeriesCard series={series("US_T10_REAL", "美国实际利率")} />);
  const graph = screen.getByRole("img", { name: "美国实际利率 历史观察" });
  resizePlot(graph, 720);
  expect(graph).toHaveAttribute("viewBox", "0 0 720 280");
  expect(graph.querySelectorAll("circle")[1]).toHaveAttribute("cx", "700");
  expect(
    screen.getByText(/近5个可比较观察样本：\+2.00 基点/),
  ).toBeInTheDocument();
  expect(screen.getByText(/分位：60.00%/)).toBeInTheDocument();
  const publication = screen.getByText("实际公布时刻未确认。");
  expect(publication.closest("details")).not.toHaveAttribute("open");
});

it("does not put different units on a shared market-series axis", () => {
  render(
    <SeriesCard
      series={{
        ...series("CN_T10", "中债利率"),
        history: [
          { date: "2026-10-15", value: 1.9, unit: "%", state: "READY" },
          { date: "2026-10-16", value: 191, unit: "bp", state: "READY" },
        ],
      }}
    />,
  );
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.getByText(/历史单位发生变化/)).toBeInTheDocument();
});

it("places provenance after the plot and facts while preserving the backend statistics basis", async () => {
  render(
    <MacroContextStrip data={{ state: "READY", items: [pmi] }} error={false} />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /市场环境概览/ }));
  const graph = screen.getByRole("img", { name: "制造业 PMI 历史走势" });
  const layout = graph.closest(".op-evidence-layout");
  expect(layout).not.toBeNull();
  expect(
    within(layout as HTMLElement).getByText("近3月变化：+0.70 指数点"),
  ).toBeInTheDocument();
  expect(
    within(layout as HTMLElement).getByText(/历史分位：61.25%/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/统计基于 36 个实际月样本/).closest("details"),
  ).not.toHaveAttribute("open");
  expect(
    screen.getByText(/发布时间待确认/).closest("details"),
  ).not.toHaveAttribute("open");
  await user.click(
    screen.getByRole("button", { name: "查看制造业 PMI 数据依据" }),
  );
  expect(screen.getByRole("link", { name: "基础数据来源" })).toBeVisible();
});
