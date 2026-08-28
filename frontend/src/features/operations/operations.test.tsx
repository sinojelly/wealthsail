import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OperationsPage from "./OperationsPage";
import { renderWithSession } from "../../test/render-with-session";
import {
  auditFixture,
  opsFreshnessFixture,
  opsJobsFixture,
  opsOutboxFixture,
} from "../../test/fixtures";

vi.mock("../../api/hooks", () => ({
  useOpsJobs: () => ({ data: opsJobsFixture, isLoading: false, isError: false, error: null }),
  useOpsFreshness: () => ({ data: opsFreshnessFixture, isLoading: false, isError: false, error: null }),
  useOpsOutbox: () => ({ data: opsOutboxFixture, isLoading: false, isError: false, error: null }),
  useOpsAudit: () => ({ data: auditFixture, isLoading: false, isError: false, error: null }),
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
