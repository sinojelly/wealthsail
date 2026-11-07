import { screen } from "@testing-library/react";
import { renderWithSession } from "../../test/render-with-session";
import type { OpportunityContext } from "../../api/investment";
import TodayFocus from "./components/TodayFocus";
import { marketJudgment } from "./today-focus-model";

const data = (text: string) =>
  ({
    state: "READY",
    items: [],
    dailyResearch: {
      state: "FAILED",
      reportRunId: "old-report",
      reportCompletedAt: "2026-11-06T08:00:00+08:00",
      report: { macroCycle: text },
    },
  }) as unknown as OpportunityContext;

it("keeps only the first three actual sentences without inventing conclusions", () => {
  expect(
    marketJudgment(
      data("需求仍待修复。信用出现改善？仍需价格验证！第四句原结论。"),
    ),
  ).toEqual(["需求仍待修复。", "信用出现改善？", "仍需价格验证！"]);
});

it("does not split decimal observations into sentences or pad a short judgment", () => {
  expect(marketJudgment(data("制造业PMI49.3，仍低于50。等待下一期。"))).toEqual(
    ["制造业PMI49.3，仍低于50。", "等待下一期。"],
  );
});

it("labels retained report judgment and links to full evidence", async () => {
  const open = vi.fn();
  renderWithSession(
    <TodayFocus
      data={data("信用修复仍需后续事实确认。")}
      onViewEvidence={open}
    />,
  );
  expect(screen.getByRole("region", { name: "市场判断" })).toHaveTextContent(
    "历史研判",
  );
  expect(screen.getByText("信用修复仍需后续事实确认。")).toBeVisible();
  screen.getByRole("button", { name: "查看完整研判与市场证据" }).click();
  expect(open).toHaveBeenCalledOnce();
});

it("keeps missing judgment distinct from no market opportunities", () => {
  renderWithSession(
    <TodayFocus
      data={{ state: "READY", items: [] }}
      onViewEvidence={() => {}}
    />,
  );
  expect(screen.getByText(/尚无有效报告研判/)).toBeVisible();
  expect(screen.queryByText("暂无投资机会")).not.toBeInTheDocument();
});
