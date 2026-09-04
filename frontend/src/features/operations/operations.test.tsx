import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OperationsPage from "./OperationsPage";
import { renderWithSession } from "../../test/render-with-session";
import {
  auditFixture,
  opsFreshnessFixture,
  opsFundEstimateFixture,
  opsJobsFixture,
  opsOutboxFixture,
} from "../../test/fixtures";

const runFundEstimate = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

vi.mock("../../api/hooks", () => ({
  useOpsJobs: () => ({ data: opsJobsFixture, isLoading: false, isError: false, error: null }),
  useOpsFreshness: () => ({ data: opsFreshnessFixture, isLoading: false, isError: false, error: null }),
  useOpsOutbox: () => ({ data: opsOutboxFixture, isLoading: false, isError: false, error: null }),
  useOpsAudit: () => ({ data: auditFixture, isLoading: false, isError: false, error: null }),
  useOpsFundEstimate: () => ({ data: opsFundEstimateFixture, isLoading: false, isError: false, error: null }),
  useRunFundEstimate: () => ({ mutateAsync: runFundEstimate, isPending: false, error: null }),
}));

it("switches from job health to the high-risk audit trail", async () => {
  const user = userEvent.setup();
  renderWithSession(<OperationsPage />);

  expect(screen.getByText("report.postmarket.generate")).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "审计日志" }));

  expect(screen.getByText("policy.confirm")).toBeInTheDocument();
});

it("surfaces UNKNOWN and CANCELLED outbox states for reconciliation", () => {
  renderWithSession(<OperationsPage />);

  expect(screen.getByText(/UNKNOWN\s+2/)).toBeInTheDocument();
  expect(screen.getByText(/CANCELLED\s+1/)).toBeInTheDocument();
});

it("shows fund-estimate coverage, calendar diagnostics and a durable run action", async () => {
  const user = userEvent.setup();
  renderWithSession(<OperationsPage />);

  expect(screen.getByText("8 / 12 只已生成")).toBeInTheDocument();
  expect(screen.getByText(/交易日历尚未补齐/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "立即计算基金估值" }));

  expect(runFundEstimate).toHaveBeenCalledTimes(1);
});
