import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { positionsFixture } from "../../test/fixtures";
import { renderWithSession } from "../../test/render-with-session";
import HoldingsTable from "./components/HoldingsTable";

it("accepts all visible estimated holding starts with one explicit action", async () => {
  const user = userEvent.setup();
  const confirmAll = vi.fn().mockResolvedValue(undefined);
  const estimated = {
    ...positionsFixture.at(-1)!,
    holdingPeriod: {
      ...positionsFixture.at(-1)!.holdingPeriod!,
      startedOn: "2021-01-26",
      method: "PORTFOLIO_ACTIVITY_ESTIMATE" as const,
      confidence: "LOW" as const,
    },
    rangeMetrics: {
      ...positionsFixture.at(-1)!.rangeMetrics!,
      status: "PARTIAL" as const,
      qualityReasons: ["PARTIAL_HOLDING_HISTORY"],
    },
  };

  renderWithSession(
    <HoldingsTable
      rows={[estimated]}
      onConfirmEstimatedHoldingStarts={confirmAll}
    />,
  );

  expect(
    screen.getByText(/认可系统推荐日期，可一次确认并消除提示/),
  ).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "确认全部估算日期（1）" }),
  );
  expect(confirmAll).toHaveBeenCalledWith([
    {
      accountId: estimated.accountId,
      instrumentId: estimated.instrumentId,
      startedOn: "2021-01-26",
    },
  ]);
});
