import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AppLoadBoundary from "./AppLoadBoundary";

function BrokenPage(): never {
  throw new TypeError("Failed to fetch dynamically imported module");
}

it("offers an explicit refresh instead of leaving a white screen after an unrecoverable load error", async () => {
  const user = userEvent.setup();
  const reload = vi.fn();
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

  render(
    <AppLoadBoundary reload={reload}>
      <BrokenPage />
    </AppLoadBoundary>,
  );

  expect(screen.getByRole("alert")).toHaveTextContent("页面加载失败");
  await user.click(screen.getByRole("button", { name: "刷新到最新版本" }));
  expect(reload).toHaveBeenCalledTimes(1);
  consoleError.mockRestore();
});
