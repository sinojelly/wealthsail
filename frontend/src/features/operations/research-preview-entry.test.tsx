import { screen } from "@testing-library/react";
import { renderWithSession } from "../../test/render-with-session";
import JobRunsTable from "./components/JobRunsTable";

const runId = "d7810690-ef13-44ad-8591-d3699f3f589a";

it("offers a new-tab simulation only for terminal research with complete materials", () => {
  renderWithSession(
    <JobRunsTable
      jobs={[]}
      runs={[]}
      researchRuns={[
        {
          runId,
          status: "FAILED",
          startedAt: "2026-11-07T21:29:58+08:00",
          finishedAt: "2026-11-07T21:50:47+08:00",
          errorClass: "ResearchValidationError",
          canPreview: true,
        },
        {
          runId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          status: "READY",
          startedAt: "2026-11-07T21:00:00+08:00",
          finishedAt: "2026-11-07T21:01:00+08:00",
          errorClass: null,
          canPreview: false,
        },
        {
          runId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          status: "RUNNING",
          startedAt: "2026-11-07T21:00:00+08:00",
          finishedAt: null,
          errorClass: null,
          canPreview: true,
        },
      ]}
    />,
  );
  const links = screen.getAllByRole("link", { name: "模拟预览" });
  expect(links).toHaveLength(1);
  expect(links[0]).toHaveAttribute(
    "href",
    `/operations/research-preview/${runId}`,
  );
  expect(links[0]).toHaveAttribute("target", "_blank");
  expect(screen.getAllByRole("button", { name: "模拟预览" })).toHaveLength(2);
  for (const button of screen.getAllByRole("button", { name: "模拟预览" }))
    expect(button).toBeDisabled();
});
