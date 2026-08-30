import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AppRoutes } from "./router";
import { renderWithSession } from "../test/render-with-session";
import {
  meFixture,
  policiesFixture,
  rulesFixture,
  summaryFixture,
} from "../test/fixtures";
import type { AuthSession } from "./auth-session";

const testSession: AuthSession = {
  userId: "usr-owner-001",
  displayName: "张明",
  email: "zhangming@example.com",
  tenantId: "tenant-demo-001",
  tenantName: "明远家庭账户",
  role: "OWNER",
  platformRoles: [],
  accessToken: "test-token",
  expiresIn: 3600,
};

vi.mock("./auth-session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./auth-session")>();
  return {
    ...actual,
    useAuthSession: () => testSession,
    getSession: () => testSession,
  };
});

vi.mock("../api/hooks", () => ({
  usePortfolioSummary: () => ({ data: summaryFixture, isLoading: false, isError: false, error: null }),
  usePositions: () => ({ data: [], isLoading: false, isError: false, error: null }),
  useNavTrend: () => ({ data: null, isLoading: false, isError: false, error: null }),
  useMarketSentiment: () => ({
    data: {
      tradeDate: null,
      totalUp: null,
      totalDown: null,
      totalFlat: null,
      limitUp: null,
      limitDown: null,
      turnover: null,
      northFlow: null,
      sentimentScore: null,
      source: "unavailable",
      qualityStatus: "UNAVAILABLE",
      updatedAt: null,
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useAiAdvice: () => ({
    data: { items: [], disclaimer: "", generatedAt: null, model: null },
    isLoading: false,
    isError: false,
    error: null,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useConfirmTiantianHoldingStart: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useConfirmTiantianHoldingStartEstimates: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  usePolicies: () => ({ data: policiesFixture, isLoading: false, isError: false, error: null }),
  useRules: () => ({ data: rulesFixture, isLoading: false, isError: false, error: null }),
  useMe: () => ({ data: meFixture, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useCreateBindingCredential: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
  useRevokeBinding: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
  useReportsList: () => ({ data: [], isLoading: false, isError: false, error: null }),
  useReportDetail: () => ({ data: null, isLoading: false, isError: false, error: null }),
  useGenerateReport: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useOpsJobs: () => ({ data: { jobs: [], runs: [] }, isLoading: false, isError: false, error: null }),
  useOpsFreshness: () => ({ data: null, isLoading: false, isError: false, error: null }),
  useOpsOutbox: () => ({ data: null, isLoading: false, isError: false, error: null }),
  useOpsAudit: () => ({ data: [], isLoading: false, isError: false, error: null }),
  useInstrumentSearch: () => ({ data: [], isLoading: false, isError: false, error: null }),
  useAddPosition: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
  useConfirmPositionImport: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
}));

afterEach(() => {
  testSession.role = "OWNER";
});

it("navigates between the main workspaces without losing the application shell", async () => {
  const user = userEvent.setup();
  renderWithSession(<AppRoutes />);

  expect(
    await screen.findByRole(
      "heading",
      { name: "资产看板" },
      { timeout: 5_000 },
    ),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("link", { name: "风控政策" }));

  expect(
    await screen.findByRole(
      "heading",
      { name: "风控政策" },
      { timeout: 5_000 },
    ),
  ).toBeInTheDocument();
  expect(screen.getByText("张明")).toBeInTheDocument();
});

it("blocks a viewer who enters the operations URL directly", async () => {
  testSession.role = "VIEWER";
  renderWithSession(<AppRoutes />, "/operations");

  expect(
    await screen.findByRole("heading", { name: "无权访问运维页面" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "运维中心" })).not.toBeInTheDocument();
});
