import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReportsPage from "./ReportsPage";
import { renderWithSession } from "../../test/render-with-session";
import { reportDetailFixture, reportsFixture } from "../../test/fixtures";

vi.mock("../../api/hooks", () => ({
  useReportsList: () => ({
    data: reportsFixture,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useReportDetail: () => ({
    data: reportDetailFixture,
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

it("opens a report evidence record with traceable provenance metadata", async () => {
  const user = userEvent.setup();
  renderWithSession(<ReportsPage />);

  await user.click(screen.getByRole("button", { name: /盘后交互复盘/ }));
  expect(screen.getByRole("heading", { name: "盘后交互复盘" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /查看证据/ }));
  const drawer = screen.getByRole("complementary", { name: "证据链溯源" });
  expect(drawer).toHaveTextContent("SHA-256");
  expect(drawer).toHaveTextContent("采集时间");
});
