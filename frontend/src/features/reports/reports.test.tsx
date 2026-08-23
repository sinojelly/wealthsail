import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReportsPage from "./ReportsPage";
import { renderWithSession } from "../../test/render-with-session";

it("opens a report evidence record with traceable provenance metadata", async () => {
  const user = userEvent.setup();
  renderWithSession(<ReportsPage />);

  await user.click(screen.getByRole("button", { name: /盘后交互复盘/ }));
  expect(screen.getByRole("heading", { name: "决策审计" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /查看证据 E-POST-001/ }));
  expect(
    screen.getByRole("complementary", { name: "证据链溯源" }),
  ).toHaveTextContent("SHA-256");
  expect(
    screen.getByRole("complementary", { name: "证据链溯源" }),
  ).toHaveTextContent("采集时间");
});
