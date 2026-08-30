import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RiskPolicyPage from "./RiskPolicyPage";
import type { PoliciesResponse } from "../../api/types";
import { renderWithSession } from "../../test/render-with-session";
import { policiesFixture, rulesFixture } from "../../test/fixtures";

const { previewRiskPolicy, confirmRiskPolicy } = vi.hoisted(() => ({
  previewRiskPolicy: vi.fn().mockResolvedValue({
    draftId: "draft-4",
    expectedVersion: 4,
    previousMaxPosition: "30.00",
    nextMaxPosition: "25.00",
    impactedHoldings: 1,
    newAlerts: 1,
    policyHash: `sha256:${"a".repeat(64)}`,
    status: "PREVIEW",
  }),
  confirmRiskPolicy: vi.fn().mockResolvedValue({
    id: "policy-5",
    version: 5,
    status: "CONFIRMED",
    policyHash: `sha256:${"a".repeat(64)}`,
  }),
}));

const policyState = vi.hoisted(() => ({
  data: null as PoliciesResponse | null,
}));

vi.mock("../../api/risk", () => ({ previewRiskPolicy, confirmRiskPolicy }));
vi.mock("../../api/hooks", () => ({
  usePolicies: () => ({ data: policyState.data, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useRules: () => ({ data: rulesFixture, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}));

beforeEach(() => {
  policyState.data = policiesFixture;
});

it("previews a draft through the policy service before a reauthenticated confirmation", async () => {
  const user = userEvent.setup();
  renderWithSession(<RiskPolicyPage />);

  const positionInput = screen.getByLabelText("单一标的最大仓位");
  await user.clear(positionInput);
  await user.type(positionInput, "25");
  expect(screen.getByText("DRAFT")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "预览策略影响" }));
  expect(
    await screen.findByRole("heading", { name: "策略变更预览" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/30.00% → 25.00%/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "确认并启用" }));
  await user.type(screen.getByLabelText("重新验证口令"), "owner-passphrase");
  await user.click(screen.getByRole("button", { name: "确认启用策略" }));
  expect(await screen.findByText("策略已确认并启用")).toBeInTheDocument();
  expect(confirmRiskPolicy).toHaveBeenCalledWith(
    expect.objectContaining({
      draftId: "draft-4",
      expectedVersion: 4,
      password: "owner-passphrase",
    }),
  );
});

it("explains the unconfigured policy and budget without placeholder metrics", () => {
  policyState.data = {
    current: null,
    history: [],
    budget: {
      maxDrawdownLimit: null,
      singlePositionLimit: null,
      equityTargetPct: null,
      bondTargetPct: null,
      goldTargetPct: null,
      cashTargetPct: null,
      rebalanceTolerancePct: null,
      minCashRatio: null,
      investmentHorizonMonths: null,
      cashNeedAmount: null,
      allowedAssetTypes: null,
      alertBudgetPerDay: null,
    },
  };

  renderWithSession(<RiskPolicyPage />);

  expect(
    screen.getByRole("status", { name: "尚未配置投资政策" }),
  ).toHaveTextContent("在左侧完成预览并确认后");
  expect(
    screen.getByRole("status", { name: "尚无风险预算" }),
  ).toHaveTextContent("确认投资政策后");
  expect(screen.getByText("尚未配置生效策略")).toBeInTheDocument();
  expect(screen.queryByText(/当前生效版本 · v/)).not.toBeInTheDocument();
  expect(screen.queryByText("—%", { exact: false })).not.toBeInTheDocument();
});

it("does not present a partially populated active budget as complete", () => {
  policyState.data = {
    ...policiesFixture,
    budget: {
      ...policiesFixture.budget,
      minCashRatio: null,
    },
  };

  renderWithSession(<RiskPolicyPage />);

  expect(
    screen.getByRole("status", { name: "尚无风险预算" }),
  ).toBeInTheDocument();
});
