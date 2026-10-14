import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MacroContextStrip from "./components/MacroContextStrip";
import DirectionExplorer from "./components/DirectionExplorer";
import type { Direction, MacroContext } from "../../api/investment";

const context: MacroContext = {
  state: "UNVERIFIED",
  months: 36,
  research: {
    methodVersion: "macro-research-v1",
    summary: "制造业景气边际改善，信用与市场风险证据仍不足。",
    dimensions: [
      {
        id: "growth",
        label: "经济增长",
        state: "PARTIAL",
        summary: "仅覆盖制造业景气。",
        evidenceCodes: ["PMI"],
        missingEvidence: ["新订单与工业生产"],
      },
      {
        id: "risk",
        label: "市场风险",
        state: "INSUFFICIENT",
        summary: "尚无直接风险评估。",
        evidenceCodes: [],
        missingEvidence: ["市场波动与信用利差"],
      },
    ],
    changes: [{ code: "PMI", summary: "PMI 较前月改善0.3点。" }],
    limitations: ["四项指标不足以判断整体经济周期。"],
  },
  events: [
    {
      id: "official-1",
      title: "官方制造业数据公告",
      category: "DATA_RELEASE",
      sourceType: "OFFICIAL_FACT",
      publisher: "国家统计局",
      sourceUri: "https://stats.example.org/release",
      publishedAt: "2026-09-30T01:30:00Z",
      retrievedAt: "2026-10-14T00:00:00Z",
      summary: "已核对公告元数据。",
      directionIds: ["a-share:broad"],
    },
  ],
  eventState: "READY",
  items: [
    {
      code: "PMI",
      label: "制造业 PMI",
      value: 49.8,
      unit: "",
      state: "UNVERIFIED",
      asOf: "2026-08-31T16:00:00Z",
      referencePeriod: "2026-09",
      publishedAt: null,
      source: "东方财富",
      sourceUri: "https://data.example.org/pmi",
      sampleCount: 2,
      periodChange: 0.3,
      trendState: "OBSERVED",
      valueVerification: "VERIFIED",
      publicationStatus: "UNKNOWN",
      officialEvidence: {
        publisher: "国家统计局",
        sourceUri: "https://stats.example.org/pmi",
        value: 49.8,
        publishedAt: null,
        retrievedAt: "2026-10-14T00:00:00Z",
      },
      nextRelease: {
        status: "SCHEDULED",
        scheduledAt: "2026-10-31T01:30:00Z",
        referencePeriod: "2026-10",
        sourceUri: "https://stats.example.org/calendar",
      },
      benchmark: { value: 50, label: "扩张与收缩分界" },
      statistics: {
        previousValue: 49.5,
        change3Months: 0.2,
        change6Months: null,
        percentile: 25,
        median: 49.9,
        sampleCount: 12,
        firstPeriod: "2025-10",
        lastPeriod: "2026-09",
        missingPeriods: [],
      },
      interpretation: {
        level: "CONTRACTION",
        momentum: "IMPROVING",
        summary: "制造业仍低于分界线，较前月改善。",
        limitations: ["需核查订单及生产分项。"],
        methodVersion: "macro-research-v1",
      },
      history: [
        {
          asOf: "2026-08-01",
          value: 49.5,
          unit: "",
          source: "东方财富",
          revision: 1,
        },
        {
          asOf: "2026-09-01",
          value: 49.8,
          unit: "",
          source: "东方财富",
          revision: 2,
        },
      ],
    },
  ],
};

async function expand() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /市场环境概览/ }));
  return user;
}

it("keeps the latest research summary compact and expands evidence on demand", async () => {
  render(<MacroContextStrip data={context} error={false} />);
  expect(screen.getByText(context.research!.summary)).toBeInTheDocument();
  expect(
    screen.queryByRole("img", { name: "制造业 PMI 历史走势" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/制造业 PMI 49.8 指数点/)).toBeInTheDocument();
  await expand();
  expect(screen.getByText("经济增长")).toBeInTheDocument();
  expect(screen.getByText("市场风险")).toBeInTheDocument();
  expect(screen.getByText(/市场波动与信用利差/)).toBeInTheDocument();
  expect(screen.getByText("仅覆盖制造业景气。")).toBeInTheDocument();
});

it("separates official value verification, publication precision and scheduled releases", async () => {
  render(<MacroContextStrip data={context} error={false} />);
  await expand();
  expect(screen.getByText("数据所属月份：2026年9月")).toBeInTheDocument();
  expect(screen.getByText("数值已核对官方数据")).toBeInTheDocument();
  expect(screen.getByText(/发布时间待确认/)).toBeInTheDocument();
  expect(screen.getByText(/2026年10月31日 09:30/)).toBeInTheDocument();
  expect(screen.getByText(/计划，可能调整/)).toBeInTheDocument();
  expect(
    screen.queryByText(/2026-08-31T16:00:00Z|UNVERIFIED/),
  ).not.toBeInTheDocument();
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "查看制造业 PMI 数据依据" }),
  );
  expect(screen.getByRole("link", { name: "官方核验来源" })).toHaveAttribute(
    "href",
    "https://stats.example.org/pmi",
  );
  expect(screen.getByRole("link", { name: "基础数据来源" })).toHaveAttribute(
    "href",
    "https://data.example.org/pmi",
  );
});

it("includes the benchmark in the plot domain and exposes units and month labels", async () => {
  render(<MacroContextStrip data={context} error={false} />);
  await expand();
  const graph = screen.getByRole("img", { name: "制造业 PMI 历史走势" });
  const baseline = within(graph).getByLabelText("扩张与收缩分界：50 指数点");
  const y = Number(baseline.getAttribute("y1"));
  expect(y).toBeGreaterThanOrEqual(15);
  expect(y).toBeLessThanOrEqual(140);
  expect(within(graph).getByText("指数点")).toBeInTheDocument();
  expect(within(graph).getByText("2026年8月")).toBeInTheDocument();
  expect(within(graph).getByText("2026年9月")).toBeInTheDocument();
});

it("shows the same observed point detail through hover and keyboard focus", async () => {
  render(<MacroContextStrip data={context} error={false} />);
  const user = await expand();
  const august = screen.getByRole("button", {
    name: "查看制造业 PMI 2026年8月",
  });
  await user.hover(august);
  expect(
    screen.getByRole("status", { name: "制造业 PMI 历史明细" }),
  ).toHaveTextContent("2026年8月：49.5 指数点");
  const september = screen.getByRole("button", {
    name: "查看制造业 PMI 2026年9月",
  });
  september.focus();
  await user.keyboard("{Enter}");
  expect(
    screen.getByRole("status", { name: "制造业 PMI 历史明细" }),
  ).toHaveTextContent("2026年9月：49.8 指数点");
  expect(
    screen.getByRole("status", { name: "制造业 PMI 历史明细" }),
  ).toHaveTextContent("修订 2");
});

it("reports longer changes using their actual statistics basis", async () => {
  render(<MacroContextStrip data={context} error={false} />);
  await expand();
  expect(screen.getByText("近3月变化：+0.20 指数点")).toBeInTheDocument();
  expect(screen.getByText("近6月变化：暂不可计算")).toBeInTheDocument();
  expect(screen.getByText(/历史分位：25.00%/)).toBeInTheDocument();
  expect(screen.getByText(/统计基于 12 个实际月样本/)).toBeInTheDocument();
  expect(screen.queryByText(/2\/36|12\/36/)).not.toBeInTheDocument();
});

it.each(["SCHEDULED", "OVERDUE", "UNKNOWN"] as const)(
  "keeps %s release plans separate from actual publication",
  async (status) => {
    render(
      <MacroContextStrip
        error={false}
        data={{
          ...context,
          items: [
            {
              ...context.items[0],
              nextRelease: {
                ...context.items[0].nextRelease!,
                status,
                scheduledAt:
                  status === "UNKNOWN" ? null : "2026-10-31T01:30:00Z",
              },
            },
          ],
        }}
      />,
    );
    await expand();
    expect(screen.getByText(/发布时间待确认/)).toBeInTheDocument();
    expect(
      screen.getByText(
        status === "UNKNOWN"
          ? /暂无已核实的下期发布日程/
          : status === "OVERDUE"
            ? /已过计划，待公告/
            : /计划，可能调整/,
      ),
    ).toBeInTheDocument();
  },
);

it("does not retain an old research conclusion while a read fails or is loading", async () => {
  const { rerender } = render(
    <MacroContextStrip data={context} error loading={false} />,
  );
  expect(screen.queryByText(context.research!.summary)).not.toBeInTheDocument();
  expect(screen.getByText(/宏观数据读取失败/)).toBeInTheDocument();
  rerender(<MacroContextStrip data={context} error={false} loading />);
  expect(screen.queryByText(context.research!.summary)).not.toBeInTheDocument();
  expect(screen.getByText(/宏观数据加载中/)).toBeInTheDocument();
});

it("renders only supplied official events with original sources", async () => {
  render(<MacroContextStrip data={context} error={false} />);
  await expand();
  expect(
    screen.getByRole("link", { name: "官方制造业数据公告" }),
  ).toHaveAttribute("href", "https://stats.example.org/release");
  expect(screen.getByText("已核对公告元数据。")).toBeInTheDocument();
  expect(screen.getByText(/国家统计局 · 官方事实/)).toBeInTheDocument();
});

it("retains third-party facts during conflict while pausing the current interpretation", async () => {
  render(
    <MacroContextStrip
      error={false}
      data={{
        ...context,
        items: [
          {
            ...context.items[0],
            state: "CONFLICT",
            valueVerification: "CONFLICT",
            periodChange: null,
            interpretation: {
              ...context.items[0].interpretation!,
              level: "UNAVAILABLE",
              summary: "数值证据冲突，暂停当前景气解释。",
            },
          },
        ],
      }}
    />,
  );
  await expand();
  expect(screen.getByText("49.8 指数点")).toBeInTheDocument();
  expect(screen.getByText("数值证据冲突")).toBeInTheDocument();
  expect(
    screen.getByText("数值证据冲突，暂停当前景气解释。"),
  ).toBeInTheDocument();
  expect(screen.getByText("连续月变化：暂不可计算")).toBeInTheDocument();
});

it("handles flat negative histories and a zero reference line without invalid coordinates", async () => {
  render(
    <MacroContextStrip
      error={false}
      data={{
        ...context,
        items: [
          {
            ...context.items[0],
            code: "CPI_YOY",
            label: "CPI 同比",
            unit: "%",
            value: -0.2,
            benchmark: { value: 0, label: "同比涨跌参照" },
            history: [
              {
                asOf: "2026-08-01",
                value: -0.2,
                unit: "%",
                source: "东方财富",
              },
              {
                asOf: "2026-09-01",
                value: -0.2,
                unit: "%",
                source: "东方财富",
              },
            ],
          },
        ],
      }}
    />,
  );
  await expand();
  const graph = screen.getByRole("img", { name: "CPI 同比 历史走势" });
  const baseline = within(graph).getByLabelText("同比涨跌参照：0 %");
  expect(Number(baseline.getAttribute("y1"))).toBeGreaterThan(0);
  expect(Number(baseline.getAttribute("y1"))).toBeLessThan(140);
  for (const point of graph.querySelectorAll("circle")) {
    expect(Number.isFinite(Number(point.getAttribute("cy")))).toBe(true);
  }
});

it("does not connect or expose a conflicted historical observation", async () => {
  render(
    <MacroContextStrip
      error={false}
      data={{
        ...context,
        items: [
          {
            ...context.items[0],
            benchmark: null,
            history: [
              { asOf: "2026-07-01", value: 49.4, source: "东方财富" },
              {
                asOf: "2026-08-01",
                value: 99,
                source: "东方财富",
                state: "CONFLICT",
              },
              { asOf: "2026-09-01", value: 49.8, source: "东方财富" },
            ],
          },
        ],
      }}
    />,
  );
  await expand();
  const graph = screen.getByRole("img", { name: "制造业 PMI 历史走势" });
  expect(graph.querySelectorAll("line")).toHaveLength(0);
  expect(
    screen.queryByRole("button", { name: "查看制造业 PMI 2026年8月" }),
  ).not.toBeInTheDocument();
});

it("keeps unknown event dates and invalid source links honest", async () => {
  render(
    <MacroContextStrip
      error={false}
      data={{
        ...context,
        events: [
          {
            ...context.events![0],
            publishedAt: null,
            sourceUri: "javascript:alert(1)",
          },
        ],
      }}
    />,
  );
  await expand();
  expect(screen.getByText("官方制造业数据公告")).toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "官方制造业数据公告" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/实际发布时间待确认/)).toBeInTheDocument();
});

it("shows unavailable event sources without inventing a current headline", async () => {
  render(
    <MacroContextStrip
      error={false}
      data={{ ...context, events: [], eventState: "UNAVAILABLE" }}
    />,
  );
  await expand();
  expect(screen.getByText(/官方事件来源暂不可用/)).toBeInTheDocument();
  expect(screen.queryByText("官方制造业数据公告")).not.toBeInTheDocument();
});

it("marks stale reporting periods without treating a recent retrieval as fresh evidence", async () => {
  render(
    <MacroContextStrip
      error={false}
      data={{
        ...context,
        items: [
          {
            ...context.items[0],
            state: "STALE",
            valueVerification: "VERIFIED",
            referencePeriod: "2026-01",
            officialEvidence: {
              ...context.items[0].officialEvidence!,
              retrievedAt: "2026-10-14T00:00:00Z",
            },
          },
        ],
      }}
    />,
  );
  await expand();
  expect(screen.getByText("数据所属月份：2026年1月")).toBeInTheDocument();
  expect(screen.getByText("旧期证据，需更新")).toBeInTheDocument();
});

it("expands the remaining supplied event list only when requested", async () => {
  render(
    <MacroContextStrip
      error={false}
      data={{
        ...context,
        events: Array.from({ length: 4 }, (_, i) => ({
          ...context.events![0],
          id: `event-${i}`,
          title: `官方公告 ${i + 1}`,
        })),
      }}
    />,
  );
  const user = await expand();
  expect(
    screen.queryByRole("link", { name: "官方公告 4" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "查看全部 4 条事件" }));
  expect(screen.getByRole("link", { name: "官方公告 4" })).toBeInTheDocument();
});

it("does not label non-finite statistics as a real percentile", async () => {
  render(
    <MacroContextStrip
      error={false}
      data={{
        ...context,
        items: [
          {
            ...context.items[0],
            statistics: {
              ...context.items[0].statistics!,
              percentile: Number.NaN,
            },
          },
        ],
      }}
    />,
  );
  await expand();
  expect(screen.getByText("历史分位：暂不可计算")).toBeInTheDocument();
  expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
});

it("presents direction transmission, counter-cases and follow-up checks", async () => {
  const user = userEvent.setup();
  const direction: Direction = {
    id: "a-share:broad",
    marketId: "a-share",
    marketLabel: "A股",
    styleId: "broad",
    label: "宽基",
    framework: "盈利与估值研究",
    macroResearch: {
      methodVersion: "macro-research-v1",
      summary: "制造业景气是宽基研究的局部证据。",
      channels: [
        {
          label: "盈利渠道",
          evidenceCodes: ["PMI"],
          observation: "景气较前月改善。",
          impact: "盈利修复需要进一步验证。",
          counterCase: "市场可能已计价。",
          watchFor: ["企业利润与估值"],
        },
      ],
      missingEvidence: ["实际利润与融资利率"],
    },
    relatedEvents: context.events,
  };
  render(
    <DirectionExplorer
      directions={[direction]}
      selected={direction}
      onSelect={() => undefined}
    />,
  );
  await user.click(screen.getByRole("button", { name: /查看驱动与风险/ }));
  expect(screen.getByText("反方条件：市场可能已计价。")).toBeInTheDocument();
  expect(screen.getByText(/企业利润与估值/)).toBeInTheDocument();
  expect(screen.getByText(/实际利润与融资利率/)).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "官方制造业数据公告" }),
  ).toBeInTheDocument();
});
