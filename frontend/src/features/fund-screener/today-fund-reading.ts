import { fundOperationDistance } from "./fund-operation-distance";
import { planPrice } from "./plan-display";
import { finitePrice } from "./quantitative-position";
import { readingText } from "./research-reading";
import type { TodayFund } from "./today-action-board";

function fundamentalText(value?: string | null) {
  return readingText(
    (value ?? "")
      .split(/(?<=[。！？；\n])/)
      .filter(
        (sentence) =>
          !/买区|卖区|追高|低吸|止盈|止损|加仓|减仓|建议买入|买入推荐|增配|风控规则|校验|审校|口径|RETRIEVAL_ONLY|样本不足/.test(
            sentence,
          ),
      )
      .join(""),
    190,
  );
}

export function todayFundReading(fund: TodayFund) {
  const plan = fund.plans[0];
  const position = fundOperationDistance(plan);
  const { tone } = fund.priority;
  const direction = fund.insights
    .map(({ thesis }) => fundamentalText(thesis))
    .find(Boolean);
  const evidence = [
    ...(plan.industryCycle?.riskChecks ?? [])
      .filter(
        (row) =>
          row.status === "FOUND" &&
          row.claimSupported &&
          row.industryScopeVerified,
      )
      .map(({ summary }) => summary),
    plan.industryCycle?.evidenceSummary,
    fund.industry?.driver,
    fund.industry?.summary,
  ]
    .map(fundamentalText)
    .find(Boolean);
  const macro =
    tone === "sell" || tone === "stop"
      ? ""
      : fundamentalText(plan.macroOpportunity?.rationale);
  const insights = [...new Set([evidence, direction, macro].filter(Boolean))]
    .slice(0, 2)
    .join(" ");
  const industry = insights || "暂无可关联的行业催化或基本面更新。";
  const {
    current,
    percentile,
    low,
    high,
    stop,
    buyDistance,
    sellDistance,
    sellLow,
  } = position;
  const price = (value: number) => planPrice(value, plan);
  const readings: string[] = [];
  if (current != null) readings.push(`单位净值 ${price(current)}`);
  if (percentile != null)
    readings.push(`近两年 ${percentile.toFixed(1)}% 分位`);
  if (tone === "buy" && low != null && high != null)
    readings.push(`已落在参考买区 ${price(low)}–${price(high)}`);
  else if (buyDistance != null && buyDistance > 0)
    readings.push(`距买区 ${buyDistance.toFixed(2)}%`);
  if (tone === "sell")
    readings.push(
      position.inSellZone
        ? "已达卖出参考区"
        : `距卖区 ${sellDistance!.toFixed(2)}%`,
    );
  if (tone === "stop" && stop != null)
    readings.push(`低于止损参考价 ${price(stop)}`);
  if (tone === "buy" && current != null) {
    if (stop != null && stop < current)
      readings.push(
        `下行至止损参考价 ${price(stop)} 为 -${((1 - stop / current) * 100).toFixed(1)}%`,
      );
    const firstTarget = finitePrice(position.track?.takeProfitT1) ?? sellLow;
    if (firstTarget != null && firstTarget > current)
      readings.push(
        `到第一止盈参考价的价格差为 +${((firstTarget / current - 1) * 100).toFixed(1)}%`,
      );
    if (
      firstTarget != null &&
      firstTarget > current &&
      stop != null &&
      stop < current
    )
      readings.push(
        `上下行价差比约 ${((firstTarget - current) / (current - stop)).toFixed(1)} 倍`,
      );
  }
  const entryRestricted =
    plan.entryBlocked ||
    position.track?.entryBlocked ||
    plan.subscriptionRestricted ||
    plan.subscriptionStatus?.restricted ||
    plan.subscriptionStatus?.purchaseStatus === "SUSPENDED";
  const advice =
    tone === "stop"
      ? "暂停补仓，复核止损参考和仓位。"
      : tone === "sell"
        ? position.inSellZone
          ? "已达卖区，持仓可评估分批止盈，避免追高。"
          : "高位勿追，持仓可关注接近卖区后的止盈条件。"
        : tone === "buy"
          ? entryRestricted
            ? "净值已到参考区，先核对申购与执行条件。"
            : plan.macroOpportunity?.stance === "HEADWIND"
              ? "处于买区，宏观逆风下控制仓位，分批观察。"
              : "关注买区内分批低吸的条件，跌破止损参考价时暂停补仓。"
          : tone === "near"
            ? "关注净值是否进入买区，等价格触发。"
            : tone === "trend"
              ? "跟踪均线与超额收益能否维持，等待合适价位。"
              : current == null
                ? "等待可靠净值与参考区间补齐。"
                : "维持观察，等待进入参考区或出现有效趋势。";
  return {
    price: readings.length
      ? `${readings.join("，")}。`
      : "净值及参考区间待补齐。",
    industry,
    advice,
  };
}
