import Decimal from "decimal.js";
import type { SettlementPreviewDto } from "./generated/types";

export interface FillDraft {
  id: string;
  symbol: string;
  name: string;
  side: "BUY" | "SELL";
  quantity: string;
  price: string;
  fee: string;
  source: string;
  occurredAt: string;
}

// TARGET: 替换为 POST /fills/{id}/preview；金额在服务端以 NUMERIC 计算。
export async function previewSettlement(
  fill: FillDraft,
  cashBefore: string,
): Promise<SettlementPreviewDto> {
  const gross = new Decimal(fill.price).mul(fill.quantity);
  const delta =
    fill.side === "BUY" ? gross.plus(fill.fee).neg() : gross.minus(fill.fee);
  return {
    fillId: fill.id,
    cashBefore,
    cashDelta: delta.toFixed(2),
    cashAfter: new Decimal(cashBefore).plus(delta).toFixed(2),
  };
}
