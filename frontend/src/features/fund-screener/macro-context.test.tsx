import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EvidenceCard } from "./components/EvidenceCards";
import MacroContextStrip from "./components/MacroContextStrip";
import { MemoryRouter } from "react-router-dom";
import { getMarketContext, type MacroContext } from "../../api/investment";

const context: MacroContext = {
  state: "UNVERIFIED",
  months: 36,
  items: [
    {
      code: "PMI",
      label: "制造业 PMI",
      value: 49.8,
      asOf: "2026-09-01",
      publishedAt: null,
      source: "国家统计局",
      state: "UNVERIFIED",
      unit: "指数点",
      sampleCount: 2,
      periodChange: 0.3,
      trendState: "OBSERVED",
      history: [
        { asOf: "2026-08-01", value: 49.5 },
        { asOf: "2026-09-01", value: 49.8 },
      ],
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

it("shows actual historical coverage, reported units and adjacent-month change", async () => {
  render(<EvidenceFixture data={context} error={false} />);
  expect(screen.getByText("49.8 指数点")).toBeInTheDocument();
  expect(screen.getByText(/已采集 2 个历史样本/)).toBeInTheDocument();
  expect(screen.getByText(/连续月变化：\+0.30 指数点/)).toBeInTheDocument();
  expect(screen.getByText(/公布时间未核实/)).toBeInTheDocument();
  expect(
    screen.getByRole("img", { name: "制造业 PMI 历史走势" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/2026年8月 至 2026年9月/)).toBeInTheDocument();
});

it("preserves current-only fixtures and reports missing historical data honestly", async () => {
  render(
    <EvidenceFixture
      error={false}
      data={{
        state: "UNVERIFIED",
        items: [
          {
            code: "PMI",
            label: "制造业 PMI",
            value: 49.8,
            asOf: "2026-09-01",
            publishedAt: null,
            source: null,
            state: "UNVERIFIED",
          },
        ],
      }}
    />,
  );
  expect(screen.getByText(/历史序列尚未采集/)).toBeInTheDocument();
  expect(screen.getByText(/连续月变化：暂不可计算/)).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("uses percentage-point changes and does not infer them across missing months", async () => {
  const cpi = {
    ...context.items[0],
    code: "CPI_YOY",
    label: "CPI 同比",
    unit: "%",
    value: -0.2,
    periodChange: -0.1,
    history: [
      { asOf: "2026-08-01", value: -0.1 },
      { asOf: "2026-09-01", value: -0.2 },
    ],
  };
  const { rerender } = render(
    <EvidenceFixture
      error={false}
      data={{
        state: "UNVERIFIED",
        items: [cpi],
      }}
    />,
  );
  expect(screen.getByText(/连续月变化：-0.10 个百分点/)).toBeInTheDocument();
  rerender(
    <EvidenceFixture
      error={false}
      data={{
        state: "UNVERIFIED",
        items: [
          {
            ...cpi,
            periodChange: null,
            trendState: "GAP",
          },
        ],
      }}
    />,
  );
  expect(screen.getByText(/连续月变化：暂不可计算/)).toBeInTheDocument();
  expect(screen.getByText(/历史月份有缺口/)).toBeInTheDocument();
});

it("passes the requested month window to the macro read endpoint", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        data: context,
        meta: {},
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const signal = new AbortController().signal;
  expect((await getMarketContext(signal, 12)).items[0].value).toBe(49.8);
  expect(fetcher).toHaveBeenCalledWith(
    "/api/v1/investment/market-context?months=12",
    expect.objectContaining({ signal }),
  );
});

it("does not imply continuity when adjacent samples change their source", async () => {
  render(
    <EvidenceFixture
      data={{
        ...context,
        items: [
          {
            ...context.items[0],
            history: [
              {
                asOf: "2026-08-01",
                value: 49.5,
                unit: "指数点",
                source: "来源 A",
              },
              {
                asOf: "2026-09-01",
                value: 49.8,
                unit: "指数点",
                source: "来源 B",
              },
            ],
          },
        ],
      }}
      error={false}
    />,
  );
  const graph = screen.getByRole("img", { name: "制造业 PMI 历史走势" });
  expect(graph.querySelectorAll("circle")).toHaveLength(2);
  expect(graph.querySelectorAll("line")).toHaveLength(0);
});

it("does not draw a shared axis across incomparable history units", async () => {
  render(
    <EvidenceFixture
      data={{
        ...context,
        items: [
          {
            ...context.items[0],
            history: [
              { asOf: "2026-08-01", value: 49.5, unit: "指数点" },
              { asOf: "2026-09-01", value: 49.8, unit: "%" },
            ],
          },
        ],
      }}
      error={false}
    />,
  );
  expect(screen.getByText(/历史单位发生变化/)).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

function EvidenceFixture({ data }: { data: MacroContext; error?: boolean }) {
  return (
    <>
      {data.items.map((item) => (
        <EvidenceCard key={item.code} item={item} />
      ))}
    </>
  );
}
it("changes the macro observation window without changing the active research identity", async () => {
  const changes: number[] = [];
  render(
    <MemoryRouter initialEntries={["/?researchTab=macro&index=hs300"]}>
      <MacroContextStrip
        data={context}
        error={false}
        months={36}
        onMonthsChange={(value) => changes.push(value)}
      />
    </MemoryRouter>,
  );
  await userEvent.selectOptions(screen.getByLabelText("宏观观察窗口"), "60");
  expect(changes).toEqual([60]);
});
