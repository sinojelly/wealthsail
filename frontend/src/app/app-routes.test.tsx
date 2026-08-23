import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AppRoutes } from "./router";
import { renderWithSession } from "../test/render-with-session";

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
  expect(screen.getByText("李明远")).toBeInTheDocument();
});
