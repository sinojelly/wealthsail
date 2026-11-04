import { screen, within } from "@testing-library/react";
import { renderWithSession } from "../../test/render-with-session";
import type {
  DailyResearch,
  DeterministicRepair,
  OpportunityContext,
} from "../../api/investment";
import * as api from "../../api/research-acceptance";
import DailyResearchReport from "./components/DailyResearchReport";
import MacroContextStrip from "./components/MacroContextStrip";
import ResearchAcceptance from "./components/ResearchAcceptance";
import serverSummary from "../../test/deterministic-repair-summary.json";

vi.mock("../../api/research-acceptance", () => ({
  getResearchAcceptance: vi.fn(),
}));
vi.mock("../../api/history-repairs", () => ({
  listHistoricalRepairs: vi.fn().mockResolvedValue({ items: [] }),
}));
vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => ({
    tenantId: "tenant-a",
    userId: "owner-a",
    role: "OWNER",
  }),
}));

const reportId = "ea890ebf-a78b-4ac3-8140-6a73593fb45d";
const currentId = "5781972e-71a7-4a23-96bd-6ed08e65b975";
const repair: DeterministicRepair = {
  version: "research-repair-v1",
  originalDecision: "PARTIAL",
  strictRevalidated: true,
  quarantinedCount: 1,
  quarantined: [
    {
      itemType: "VALUE_DIRECTION",
      itemIndex: 1,
      businessKey: "PRIVATE-KEY",
      ruleId: "DIRECTION_EVIDENCE_INSUFFICIENT",
      reason: "该方向证据不足，已隔离。",
    },
  ],
  counts: {},
};
const daily = (): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "FAILED",
  runId: currentId,
  reportRunId: reportId,
  scheduledFor: null,
  startedAt: null,
  completedAt: null,
  asOf: null,
  nextRunAt: null,
  report: Object.assign(
    {
      summary: "旧严格报告继续保留",
      coverage: [],
      sources: [],
      events: [],
      upcoming: [],
      energy: [],
    },
    { deterministicRepair: repair },
  ),
});

beforeEach(() => {
  vi.mocked(api.getResearchAcceptance)
    .mockReset()
    .mockRejectedValue({ status: 404 });
});

it("binds the deterministic repair notice to the displayed report rather than a newer failed run", async () => {
  const data = daily();
  const before = JSON.stringify(data);
  renderWithSession(<DailyResearchReport data={data} />);
  const notice = within(
    screen.getByRole("region", { name: "报告修正与隔离说明" }),
  );
  expect(notice.getByText("已隔离并严格重验")).toBeVisible();
  expect(notice.getByText("该方向证据不足，已隔离。")).toBeVisible();
  expect(notice.getByText(`修正报告：${reportId}`)).toBeVisible();
  expect(notice.queryByText(new RegExp(currentId))).not.toBeInTheDocument();
  expect(notice.queryByText("PRIVATE-KEY")).not.toBeInTheDocument();
  expect(JSON.stringify(data)).toBe(before);
});

it("keeps the report repair notice visible outside the collapsed global research panel", () => {
  renderWithSession(
    <MacroContextStrip
      error={false}
      data={
        {
          state: "READY",
          items: [],
          dailyResearch: daily(),
        } as OpportunityContext
      }
    />,
    "/fund-screener?researchOpen=0",
  );
  expect(
    screen.getByRole("region", { name: "报告修正与隔离说明" }),
  ).toHaveTextContent("该方向证据不足，已隔离。");
});

it("explains that the original partial INITIAL was isolated and strictly republished", async () => {
  vi.mocked(api.getResearchAcceptance).mockResolvedValue({
    id: "initial",
    researchId: reportId,
    kind: "INITIAL",
    decision: "PARTIAL",
    validatorVersion: "v1",
    validatedAt: "2026-11-04T08:00:00+08:00",
    actionable: false,
    formalPublishable: false,
    issueCount: 0,
    issueListComplete: true,
    canReplay: false,
    canInspectArtifacts: false,
    counts: {},
    accepted: {
      sources: [],
      industryCycleReviews: [],
      valueDirections: [],
      events: [],
      upcoming: [],
    },
    ...{ repairedFormalPublished: true, deterministicRepair: repair },
  });
  renderWithSession(<ResearchAcceptance runId={reportId} state="READY" />);
  expect(
    await screen.findByText("原始输出部分未通过，隔离后严格重验已发布"),
  ).toBeVisible();
  expect(
    screen.queryByText("部分验收成果·仅供观察，未正式发布"),
  ).not.toBeInTheDocument();
});

it("reads the actual offline server repair_summary output including aliases while preserving current READY", () => {
  // Generated with tests.unit.test_research_repair.fixture/repair and the production repair_summary function.
  expect(serverSummary.version).toBe(serverSummary.repairVersion);
  expect(serverSummary.repairRevision).toBe(serverSummary.revision);
  expect(serverSummary.counts.fundPlans).toBe(2);
  const data = daily();
  data.state = "READY";
  data.runId = reportId;
  data.report!.deterministicRepair = serverSummary as DeterministicRepair;
  renderWithSession(<DailyResearchReport data={data} />);
  expect(screen.getByText("已隔离并严格重验")).toBeVisible();
  expect(screen.getByText(serverSummary.quarantined[0].reason)).toBeVisible();
  expect(
    screen.queryByText(serverSummary.quarantined[0].businessKey),
  ).not.toBeInTheDocument();
  expect(data.state).toBe("READY");
});
it("keeps a published V2 repair notice readable when its legacy summary omits quarantine reasons", () => {
  const data = daily();
  data.state = "READY";
  data.runId = reportId;
  const legacy = { ...repair };
  delete legacy.quarantined;
  data.report!.deterministicRepair = legacy as DeterministicRepair;
  renderWithSession(<DailyResearchReport data={data} />);
  const notice = within(
    screen.getByRole("region", { name: "报告修正与隔离说明" }),
  );
  expect(notice.getByText("已隔离并严格重验")).toBeVisible();
  expect(notice.queryByRole("listitem")).not.toBeInTheDocument();
  expect(
    notice.queryByText("该方向证据不足，已隔离。"),
  ).not.toBeInTheDocument();
  expect(notice.getByText(`修正报告：${reportId}`)).toBeVisible();
});
