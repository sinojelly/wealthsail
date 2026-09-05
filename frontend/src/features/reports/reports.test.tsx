import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReportsPage from "./ReportsPage";
import type { ReportDetail, ReportSummary } from "../../api/types";
import { renderWithSession } from "../../test/render-with-session";
import { reportDetailFixture, reportsFixture } from "../../test/fixtures";

const reportState = vi.hoisted(() => ({
  reports: [] as ReportSummary[],
  detail: null as ReportDetail | null,
  regenerate: vi.fn<
    (
      input: unknown,
      options?: {
        onSuccess?: (result: {
          reportId: string | null;
          outboxId: string | null;
        }) => void;
      },
    ) => void
  >(),
  role: "OWNER",
  isPending: false,
}));

vi.mock("../../app/auth-session", () => ({
  useAuthSession: () => ({ role: reportState.role }),
  canViewOperations: (session: { role?: string } | null) =>
    session?.role === "OWNER" || session?.role === "ADMIN",
}));

vi.mock("../../api/hooks", () => ({
  useReportsList: () => ({
    data: reportState.reports,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useReportDetail: (reportId: string | null) => ({
    data: reportId ? (reportState.detail ?? undefined) : undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useGenerateReport: () => ({
    mutate: reportState.regenerate,
    isPending: reportState.isPending,
    isError: false,
    error: null,
  }),
}));

beforeEach(() => {
  reportState.reports = reportsFixture;
  reportState.detail = reportDetailFixture;
  reportState.regenerate.mockClear();
  reportState.role = "OWNER";
  reportState.isPending = false;
});

it("opens a report evidence record with traceable provenance metadata", async () => {
  const user = userEvent.setup();
  renderWithSession(<ReportsPage />);

  await user.click(screen.getByRole("button", { name: /盘后交互复盘/ }));
  expect(
    screen.getByRole("heading", { name: "盘后交互复盘" }),
  ).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /查看证据/ }));
  const drawer = screen.getByRole("complementary", { name: "证据链溯源" });
  expect(drawer).toHaveTextContent("SHA-256");
  expect(drawer).toHaveTextContent("源快照");
  expect(drawer).toHaveTextContent("WealthSail-Fund-Estimator");
});

it("opens the report selected by a reportId deep link", () => {
  renderWithSession(<ReportsPage />, "/reports?reportId=R-POST-20260823");

  expect(
    screen.getByRole("heading", { name: "盘后交互复盘" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /盘后交互复盘/ })).toHaveClass(
    "selected",
  );
});

it("renders report audit timestamps in Asia/Shanghai", () => {
  reportState.reports = [
    {
      ...reportsFixture[0],
      tradeDate: "2026-08-31",
      generatedAt: "2026-08-31T00:30:00Z",
    },
    {
      ...reportsFixture[0],
      id: "R-INTRADAY-20260831",
      session: "INTRADAY",
      title: "盘中跟踪",
      tradeDate: "2026-08-31",
      generatedAt: "2026-08-31T06:55:00Z",
    },
    {
      ...reportsFixture[0],
      id: "R-FUND-20260831",
      session: "FUND_1430",
      title: "基金 2:30 报告",
      tradeDate: "2026-08-31",
      generatedAt: "2026-08-31T06:30:00Z",
    },
    {
      ...reportsFixture[1],
      id: "R-POST-20260831",
      tradeDate: "2026-08-31",
      generatedAt: "2026-08-31T07:30:00Z",
    },
  ];
  reportState.detail = {
    ...reportDetailFixture,
    id: "R-POST-20260831",
    tradeDate: "2026-08-31",
    asOf: "2026-08-31T07:30:00Z",
    versionHistory: [
      {
        ...reportDetailFixture.versionHistory[0],
        asOf: "2026-08-31T07:00:00Z",
      },
    ],
  };

  renderWithSession(<ReportsPage />, "/reports?reportId=R-POST-20260831");

  expect(screen.getByText("08:30")).toBeInTheDocument();
  expect(screen.getByText("14:30")).toBeInTheDocument();
  expect(screen.getByText("14:55")).toBeInTheDocument();
  expect(screen.getByText("15:30")).toBeInTheDocument();
  expect(screen.getByText(/2026-08-31 15:30 生成/)).toBeInTheDocument();
  expect(screen.getByText(/v1 · PUBLISHED · 15:00/)).toBeInTheDocument();
});

it("renders macro and personalized pre-market reports as separate audit rows", () => {
  reportState.reports = [
    {
      ...reportsFixture[0],
      id: "R-PRE-MACRO-20260902",
      variant: "PRE_MARKET_MACRO",
      audienceUserId: null,
      title: "盘前简报",
      generatedAt: "2026-09-02T00:30:00Z",
    },
    {
      ...reportsFixture[0],
      id: "R-PRE-PERSONALIZED-20260902",
      variant: "PRE_MARKET_PERSONALIZED",
      audienceUserId: "usr-owner-001",
      title: "盘前简报",
      generatedAt: "2026-09-02T00:35:00Z",
    },
  ];

  renderWithSession(<ReportsPage />);

  expect(screen.getByText("盘前简报·宏观")).toBeInTheDocument();
  expect(screen.getByText("盘前简报·个性化")).toBeInTheDocument();
  expect(screen.getByText("08:30")).toBeInTheDocument();
  expect(screen.getByText("08:35")).toBeInTheDocument();
});

it("renders a safe placeholder for an invalid report timestamp", () => {
  reportState.reports = [
    {
      ...reportsFixture[0],
      generatedAt: "2026-08-31T07:30:00",
    },
    {
      ...reportsFixture[1],
      generatedAt: "2026-02-30T07:30:00Z",
    },
  ];

  renderWithSession(<ReportsPage />);

  expect(screen.getAllByText("--:--")).toHaveLength(2);
});

it("renders safe placeholders for invalid detail and version timestamps", () => {
  reportState.detail = {
    ...reportDetailFixture,
    asOf: "2026-08-31T07:30:00",
    versionHistory: [
      {
        ...reportDetailFixture.versionHistory[0],
        asOf: "2026-02-30T07:30:00Z",
      },
    ],
  };

  renderWithSession(<ReportsPage />, "/reports?reportId=R-POST-20260823");

  expect(screen.getByText(/时间不可用 生成/)).toBeInTheDocument();
  expect(screen.getByText(/v1 · PUBLISHED · --:--/)).toBeInTheDocument();
});

it("presents an empty report workspace with structured guidance", () => {
  reportState.reports = [];

  renderWithSession(<ReportsPage />);

  // 无任何报告时，时间线仍常驻四时段占位入口，可一键生成。
  for (const session of [
    "盘前简报",
    "盘中跟踪",
    "基金 2:30 报告",
    "盘后交互复盘",
  ]) {
    expect(
      screen.getByRole("button", { name: `生成并发给我 ${session}` }),
    ).toBeInTheDocument();
  }
  expect(screen.getByRole("status", { name: "暂无研报" })).toHaveTextContent(
    "盘前、盘中、基金 2:30 和盘后报告生成后",
  );
});

it("generates a missing FUND_1430 report from the timeline placeholder", async () => {
  // reportsFixture 仅含 PRE_MARKET 与 POST_MARKET → FUND_1430 显示占位入口。
  reportState.reports = reportsFixture.filter(
    (report) => report.session !== "POST_MARKET",
  );
  reportState.detail = null;

  const user = userEvent.setup();
  renderWithSession(<ReportsPage />);
  await user.click(
    screen.getByRole("button", { name: "生成并发给我 基金 2:30 报告" }),
  );

  expect(reportState.regenerate).toHaveBeenCalledWith(
    {
      session: "FUND_1430",
      phase: undefined,
    },
    expect.any(Object),
  );
});

it("regenerate button forwards the selected report's MACRO variant as phase", async () => {
  reportState.reports = [
    {
      id: "R-PRE-MACRO",
      session: "PRE_MARKET",
      variant: "PRE_MARKET_MACRO",
      title: "盘前简报·宏观",
      subtitle: "隔夜市场与今日全球重点",
      tradeDate: "2026-09-03",
      version: 1,
      generatedAt: "2026-09-03T08:30:00+08:00",
      status: "已发布",
      qualityStatus: "FRESH",
    },
  ];
  reportState.detail = null;

  const user = userEvent.setup();
  renderWithSession(<ReportsPage />, "/reports?reportId=R-PRE-MACRO");
  await user.click(screen.getByRole("button", { name: /重新生成/ }));

  expect(reportState.regenerate).toHaveBeenCalledWith(
    {
      session: "PRE_MARKET",
      phase: "MACRO",
    },
    expect.any(Object),
  );
});

it("regenerate button forwards PERSONALIZED phase for the current user", async () => {
  reportState.reports = [
    {
      id: "R-PRE-PERSONALIZED",
      session: "PRE_MARKET",
      variant: "PRE_MARKET_PERSONALIZED",
      audienceUserId: "00000000-0000-0000-0000-000000000099",
      title: "盘前简报·个性化",
      subtitle: "持仓、关注标的与今日操作提醒",
      tradeDate: "2026-09-03",
      version: 1,
      generatedAt: "2026-09-03T08:35:00+08:00",
      status: "已发布",
      qualityStatus: "FRESH",
    },
  ];
  reportState.detail = null;

  const user = userEvent.setup();
  renderWithSession(<ReportsPage />, "/reports?reportId=R-PRE-PERSONALIZED");
  await user.click(screen.getByRole("button", { name: /重新生成/ }));

  expect(reportState.regenerate).toHaveBeenCalledWith(
    {
      session: "PRE_MARKET",
      phase: "PERSONALIZED",
    },
    expect.any(Object),
  );
});

it("hides every manual trigger from EDITOR and VIEWER roles", () => {
  reportState.role = "VIEWER";
  reportState.reports = [];

  renderWithSession(<ReportsPage />);

  expect(
    screen.queryByRole("button", { name: /生成并发给我/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /重新生成/ }),
  ).not.toBeInTheDocument();
  expect(screen.getAllByText("尚未生成")).toHaveLength(4);
});

it("keeps every manual trigger disabled while one generation is pending", () => {
  reportState.isPending = true;
  reportState.reports = reportsFixture.filter(
    (report) => report.session !== "POST_MARKET",
  );

  renderWithSession(
    <ReportsPage />,
    `/reports?reportId=${reportState.reports[0].id}`,
  );

  expect(
    screen.getByRole("button", { name: /重新生成并发给我/ }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: /生成并发给我 基金 2:30 报告/ }),
  ).toBeDisabled();
});

it("regenerates an INTRADAY_ALERT as the same alert variant", async () => {
  reportState.reports = [
    {
      ...reportsFixture[0],
      id: "R-INTRADAY-ALERT",
      session: "INTRADAY",
      variant: "INTRADAY_ALERT",
      audienceUserId: "usr-owner-001",
      title: "盘中异动提醒",
    },
  ];

  const user = userEvent.setup();
  renderWithSession(<ReportsPage />, "/reports?reportId=R-INTRADAY-ALERT");
  await user.click(screen.getByRole("button", { name: /重新生成并发给我/ }));

  expect(reportState.regenerate).toHaveBeenCalledWith(
    { session: "INTRADAY", phase: "ALERT" },
    expect.any(Object),
  );
});

it("reports queued delivery and selects the newly generated version", async () => {
  const selected = reportsFixture[0];
  const nextReport = {
    ...selected,
    id: "R-NEW",
    version: selected.version + 1,
  };
  reportState.regenerate.mockImplementation((_input, options) => {
    reportState.reports = [nextReport];
    options?.onSuccess?.({ reportId: "R-NEW", outboxId: "OUTBOX-NEW" });
  });

  const user = userEvent.setup();
  renderWithSession(<ReportsPage />, `/reports?reportId=${selected.id}`);
  await user.click(screen.getByRole("button", { name: /重新生成并发给我/ }));

  expect(screen.getByRole("status")).toHaveTextContent(
    "报告已生成，企业微信通知已排队",
  );
  expect(screen.getByRole("button", { name: /盘前简报/ })).toHaveClass(
    "selected",
  );
});

it("does not claim a WeCom delivery when no outbox row was created", async () => {
  reportState.regenerate.mockImplementation((_input, options) => {
    options?.onSuccess?.({ reportId: "R-NEW", outboxId: null });
  });

  const user = userEvent.setup();
  renderWithSession(
    <ReportsPage />,
    `/reports?reportId=${reportsFixture[0].id}`,
  );
  await user.click(screen.getByRole("button", { name: /重新生成并发给我/ }));

  expect(
    screen.getByText("报告已生成；当前没有可用的企业微信投递。"),
  ).toBeInTheDocument();
});
