import type { FillDraft } from "../../api/portfolio";

// 本地演示用的成交草稿（后端成交草稿 API 尚未接入）。
export const pendingFills: FillDraft[] = [
  {
    id: "F2026082301",
    symbol: "002230",
    name: "科大讯飞",
    side: "BUY",
    quantity: "1000",
    price: "43.28",
    fee: "43.28",
    source: "企业微信草稿",
    occurredAt: "09:32",
  },
  {
    id: "F2026082302",
    symbol: "600036",
    name: "招商银行",
    side: "SELL",
    quantity: "500",
    price: "38.82",
    fee: "19.41",
    source: "手工录入",
    occurredAt: "14:17",
  },
];
