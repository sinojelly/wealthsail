import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PersonalFundReference } from "../../api/personal-funds";
import { groupPersonalFunds } from "./personal-fund-model";
import CurrentPersonalFundsReference from "../operations/components/CurrentPersonalFundsReference";
import FundNavTrendChart from "./components/FundNavTrendChart";
import { fundTrack, heldFund, frozenPlan } from "./personal-funds.test-data";

const session = vi.hoisted(() => ({
  tenantId: "tenant",
  userId: "actor",
  role: "OWNER",
}));
vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => session,
}));
function reference(code = "011035"): PersonalFundReference {
  return {
    key: `FUND_CN:${code}`,
    fundCode: code,
    fundMarket: "FUND_CN",
    label: `本地基金${code}`,
    relationships: ["WATCHLIST"],
    investmentEligible: false,
    confirmedNav: null,
    currentEstimate: null,
    plan: { ...frozenPlan(), fundCode: code },
    history: {
      methodVersion: "fund-unit-nav-trend-v1",
      fundCode: code,
      fundName: code,
      period: "all",
      priceBasis: "UNIT_NAV",
      unit: "CNY/份",
      state: "READY",
      asOf: "2026-11-06",
      retrievedAt: "2026-11-07T08:00:00+08:00",
      sourceUri: `https://fund.eastmoney.com/${code}.html`,
      points: [
        { date: "2025-01-01", unitNav: "1" },
        { date: "2025-06-01", unitNav: "1" },
        { date: "2026-06-01", unitNav: "2" },
        { date: "2026-11-06", unitNav: "2" },
      ],
      coverage: {
        startDate: "2025-01-01",
        endDate: "2026-11-06",
        fullHistory: true,
        requestedStartDate: null,
        sampleCount: 4,
      },
      summary: {
        minimumNav: "1",
        maximumNav: "2",
        currentNav: "2",
        percentilePct: null,
        percentileMethod: "EMPIRICAL_MIDRANK_V1",
      },
      refresh: { state: "IDLE", runId: null, pollAfterMs: null },
    },
    missingReasons: [],
  };
}
function show(ui: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {ui}
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 600,
    height: 220,
    top: 0,
    right: 600,
    bottom: 220,
    left: 0,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
});
it("unions holdings, independent frozen tracks and watchlist while excluding research-only mentions", () => {
  const held = heldFund();
  const rows = groupPersonalFunds(
    { items: [fundTrack()], heldFunds: [held], total: 1 },
    "2026-11-08",
    [
      { ...reference(held.fundCode!), relationships: ["HELD", "TRACKED"] },
      reference("013477"),
      {
        ...reference("011035"),
        relationships: ["RESEARCH_MENTIONED"],
        plan: null,
      },
    ],
  );
  expect(rows.map((row) => row.code).sort()).toEqual(
    [held.fundCode, "013477"].sort(),
  );
  expect(rows.find((row) => row.holding)?.tracks).toHaveLength(1);
  expect(rows.find((row) => row.code === "011035")).toBeUndefined();
});
it("reads current personal funds only after opt-in, keeps all action controls disabled and never fetches live charts", async () => {
  const calls: string[] = [];
  const local = {
    ...reference(),
    currentEstimate: {
      value: "1.2345",
      changePct: "2.88",
      asOf: "2026-11-08T09:55:00+08:00",
      retrievedAt: "2026-11-08T09:56:00+08:00",
      usable: false,
      sourceUri: null,
      sourceVerification: "LOCAL_RECORD_ONLY" as const,
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, init) => {
      calls.push(`${init?.method ?? "GET"} ${String(input)}`);
      return new Response(
        JSON.stringify({
          data: {
            version: "personal-funds-reference-v1",
            source: "LOCAL_DATABASE_ONLY",
            readOnly: true,
            asOf: "2026-11-08T10:00:00+08:00",
            funds: [local],
          },
          meta: {},
        }),
      );
    }),
  );
  show(<CurrentPersonalFundsReference />);
  expect(calls).toEqual([]);
  await userEvent.click(screen.getByRole("checkbox"));
  const fund = await screen.findByRole("article", { name: "当前基金 011035" });
  expect(fund).toHaveTextContent("1.2345");
  expect(fund).toHaveTextContent("+2.88%");
  expect(within(fund).getByRole("note")).toHaveTextContent(
    "本地Worker记录，原公开来源未保存/待核",
  );
  expect(
    within(fund).getByRole("button", { name: "保存当前跟踪" }),
  ).toBeDisabled();
  expect(
    within(fund).getByRole("button", { name: "设置当前提醒" }),
  ).toBeDisabled();
  expect(
    within(fund).getByRole("region", { name: "参考买卖点与操作状态" }),
  ).toBeVisible();
  await userEvent.click(
    await within(fund).findByRole("button", { name: "成立以来" }),
  );
  expect(calls).toEqual([
    "GET /api/v1/investment/personal-funds-reference?includeResearchMentions=false",
  ]);
  await userEvent.click(screen.getByRole("checkbox"));
  expect(screen.queryByRole("article")).not.toBeInTheDocument();
});
it("recomputes snapshot percentile from each selected period instead of displaying unknown or a wider-window rank", async () => {
  show(<FundNavTrendChart fundCode="011035" snapshot={reference().history} />);
  expect(await screen.findByText(/研究时处于该周期/)).toHaveTextContent(
    "75.0%",
  );
  await userEvent.click(screen.getByRole("button", { name: "近半年" }));
  expect(screen.getByText(/研究时处于该周期/)).toHaveTextContent("50.0%");
  await userEvent.click(screen.getByRole("button", { name: "冻结全窗口" }));
  expect(screen.getByText(/研究时处于该周期/)).toHaveTextContent("75.0%");
});
