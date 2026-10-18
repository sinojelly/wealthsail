import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  ActionableZoneResult,
  OpportunityContext,
  OpportunitySeries,
} from "../../api/investment";
import ActionablePlan from "./components/ActionablePlan";
import MacroEvidencePanel from "./components/MacroEvidencePanel";
import { SeriesCard } from "./components/EvidenceCards";
import { renderWithSession } from "../../test/render-with-session";
import StudyTracking from "./components/StudyTracking";

vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));
const series = (code = "M1_M2_GAP") =>
  ({
    code,
    label: code === "M2_YOY" ? "M2同比（东方财富）" : "M1与M2剪刀差",
    value: -4,
    unit: "percentage_points",
    frequency: "MONTHLY",
    state: "READY",
    asOf: "2026-09-30",
    sourceUri: "https://example.org/provider",
    sourceName: "公开供应商",
    changes: { previousValue: 0, sampleCount: 2 },
    history: [
      { date: "2026-08-31", value: 0, state: "READY" },
      { date: "2026-09-30", value: -4, state: "READY" },
    ],
  }) as OpportunitySeries;
const plan = {
  methodVersion: "actionable-zones-v3",
  subjectKind: "nav",
  subjectId: "000051",
  fundCode: "000051",
  label: "华夏沪深300ETF联接A",
  priceBasis: "UNIT_NAV",
  unit: "CNY/份",
  asOf: "2026-10-16",
  sourceUri: "https://example.org/nav",
  currentPrice: "2.1234",
  indicators: { bollMiddle: 2.1, ma20: 2.1 },
  parameters: { navAbsoluteChangeWindow: 14 },
  calibrationRuleKeys: [],
  calibrationNote: "strategyMemoryRules为空，无真实规则key",
  tracks: [
    {
      track: "LEFT_VALUE",
      state: "CANDIDATE",
      entryZoneMin: "2.0000",
      entryZoneMax: "2.1000",
      stopLossPrice: "1.9",
      takeProfitT1: "2.4",
      takeProfitT2: "2.6",
      reasons: [],
      priceState: "READY",
    },
  ],
} as ActionableZoneResult;
it("shows each macro code once in core cards while retaining the alternative original source", async () => {
  const data = {
    state: "READY",
    items: [
      {
        code: "M2_YOY",
        label: "M2同比",
        value: 7.2,
        unit: "%",
        state: "READY",
        sourceUri: "https://example.org/official",
      },
    ],
    series: [{ ...series("M2_YOY"), value: 7.2, unit: "%" }],
  } as OpportunityContext;
  render(<MacroEvidencePanel data={data} />);
  expect(
    within(screen.getByRole("region", { name: "核心指标" }))
      .getAllByRole("article")
      .filter((row) => row.classList.contains("op-compact-metric")),
  ).toHaveLength(1);
  await userEvent.click(screen.getByText(/其他来源与口径/));
  expect(
    screen.getByRole("link", { name: /查看M2同比（东方财富）来源/ }),
  ).toHaveAttribute("href", "https://example.org/provider");
});
it("translates units in compact values, history graphics and focused evidence including zero and negatives", async () => {
  render(<SeriesCard series={series()} />);
  expect(screen.getByText(/-4.*个百分点/)).toBeInTheDocument();
  expect(
    screen.getByText(/前一同口径有效值：0.00.*个百分点/),
  ).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("percentage_points");
  await userEvent.click(
    screen.getByRole("button", { name: /查看M1与M2剪刀差.*8月31日/ }),
  );
  expect(screen.getByRole("status")).toHaveTextContent("0 个百分点");
});
it("reads algorithm fields in Chinese and hides empty-rule debugging text without changing frozen facts", async () => {
  const original = JSON.stringify(plan);
  render(<ActionablePlan result={plan} frozen />);
  await userEvent.click(screen.getByText("价格依据与算法"));
  expect(screen.getByText(/价格口径：单位净值/)).toBeVisible();
  expect(screen.getByText(/布林中轨/)).toBeVisible();
  expect(screen.getByText(/净值绝对变化窗口/)).toBeVisible();
  expect(document.body).not.toHaveTextContent(
    /bollMiddle|navAbsoluteChangeWindow|strategyMemoryRules为空/,
  );
  expect(JSON.stringify(plan)).toBe(original);
});
it("previews the selected direction, fund, price and zones before saving new research", async () => {
  renderWithSession(
    <StudyTracking
      actor="owner"
      scope={{ fundCode: "000051", fundMarket: "FUND_CN" }}
      preview={{
        direction: "A股宽基",
        subject: "华夏沪深300ETF联接A",
        code: "000051",
        plan,
      }}
    />,
  );
  await userEvent.click(screen.getByText("新建研究"));
  const preview = screen.getByRole("region", { name: "即将跟踪的研究对象" });
  expect(preview).toHaveTextContent("A股宽基");
  expect(preview).toHaveTextContent("华夏沪深300ETF联接A · 000051");
  expect(preview).toHaveTextContent("2.1234");
  expect(preview).toHaveTextContent("2.0000–2.1000");
});
