import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OperationsPage from "./OperationsPage";
import { renderWithSession } from "../../test/render-with-session";

it("switches from job health to the high-risk audit trail", async () => {
  const user = userEvent.setup();
  renderWithSession(<OperationsPage />);

  expect(screen.getByText("report.postmarket.generate")).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "审计日志" }));

  expect(screen.getByText("policy.confirm")).toBeInTheDocument();
  expect(screen.getByText("HIGH")).toBeInTheDocument();
});
