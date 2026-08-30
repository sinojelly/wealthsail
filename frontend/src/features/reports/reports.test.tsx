import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReportsPage from "./ReportsPage";
import type { ReportSummary } from "../../api/types";
import { renderWithSession } from "../../test/render-with-session";
import { reportDetailFixture, reportsFixture } from "../../test/fixtures";

const reportState = vi.hoisted(() => ({
  reports: [] as ReportSummary[],
}));

vi.mock("../../api/hooks", () => ({
  useReportsList: () => ({
    data: reportState.reports,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useReportDetail: (reportId: string | null) => ({
    data: reportId ? reportDetailFixture : undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useGenerateReport: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

beforeEach(() => {
  reportState.reports = reportsFixture;
});

it("opens a report evidence record with traceable provenance metadata", async () => {
  const user = userEvent.setup();
  renderWithSession(<ReportsPage />);

  await user.click(screen.getByRole("button", { name: /盘后交互复盘/ }));
  expect(screen.getByRole("heading", { name: "盘后交互复盘" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /查看证据/ }));
  const drawer = screen.getByRole("complementary", { name: "证据链溯源" });
  expect(drawer).toHaveTextContent("SHA-256");
  expect(drawer).toHaveTextContent("源快照");
  expect(drawer).toHaveTextContent("WealthSail-Fund-Estimator");
});

it("presents an empty report workspace with structured guidance", () => {
  reportState.reports = [];

  renderWithSession(<ReportsPage />);

  const emptyTimeline = screen.getByRole("status", {
    name: "暂无报告记录",
  });
  expect(emptyTimeline).toHaveTextContent(
    "报告生成后会按交易日和版本显示在这里",
  );
  expect(emptyTimeline.closest(".report-list")).toHaveClass("is-empty");
  expect(screen.getByRole("status", { name: "暂无研报" })).toHaveTextContent(
    "盘前、盘中、基金 2:30 和盘后报告生成后",
  );
});
