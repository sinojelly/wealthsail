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

it("preserves a long judgment's final negation and conditions within the three-sentence limit", () => {
  const first =
    "需求侧的设备更新订单较此前增加，部分企业披露新增项目和产能安排，信贷与库存的变化仍需结合后续月度统计核对，外部需求和能源成本也可能影响不同产业的利润修复，当前记录只说明若干观察线索出现改善，不能把单一企业公告等同于整个行业的盈利拐点，资金流向与终端销售仍缺少同期完整证据，相关基金的技术条件和价格区间也须分别读取原研究，所以现有资料并不能证明需求已经全面复苏，只有后续订单和价格持续验证时才讨论趋势确认。";
  const second = "信用条件仍有分化，尚不支持无条件入场。";
  const third = "下一期数据公布后再核对。";
  expect(
    marketJudgment(data(`${first}${second}${third}第四句不进入首页。`)),
  ).toEqual([first, second, third]);
});

it("labels retained report judgment and links to full evidence", async () => {
  const open = vi.fn();
  renderWithSession(
    <TodayFocus
      data={data("信用修复仍需后续事实确认。")}
      onViewEvidence={open}
    />,
  );
  expect(
    screen.getByRole("region", { name: "今日宏观总纲" }),
  ).toHaveTextContent("历史宏观研判");
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
  expect(screen.getByText(/综合研究尚未生成/)).toBeVisible();
  expect(screen.queryByText("暂无投资机会")).not.toBeInTheDocument();
});

it("shows all observation value directions and mentioned funds without opening evidence", () => {
  const value = data("观察方向保留。");
  value.dailyResearch!.report!.valueDirections = Array.from(
    { length: 15 },
    (_, i) => ({
      key: `direction-${i}`,
      title: `价值方向${i + 1}`,
      thesis: "AI研究观点",
      qualificationStatus: "OBSERVATION_ONLY",
      investmentEligible: false,
      signal: "WATCH",
      sourceIds: [],
      macroCodes: [],
      watchFor: [],
      plans: [],
    }),
  );
  value.dailyResearch!.report!.mentionedFunds = [
    { fundCode: "000051", name: "提及基金" },
  ];
  renderWithSession(<TodayFocus data={value} onViewEvidence={() => {}} />);
  expect(
    screen.getByText("价值方向15", { selector: ".op-today-insight strong" }),
  ).toBeVisible();
  expect(
    screen.getByRole("region", { name: "按操作紧迫度排序的基金机会" }),
  ).toHaveTextContent("000051");
});
