import type { TiantianFreshnessReason } from "../../../api/types";

const reasonCopy: Record<TiantianFreshnessReason, string> = {
  ACCOUNT_FACTS_CHANGED: "账户持仓事实已经变化，需要重新解析",
  PREVIEW_ENGINE_CHANGED: "预览规则已经升级，需要重新解析",
  NEWER_SOURCE_SNAPSHOT_CONFIRMED: "已有更新的来源快照确认，需要重新解析",
};

export function freshnessReasonText(reason: TiantianFreshnessReason): string {
  return reasonCopy[reason];
}
