import Decimal from "decimal.js";
import { request } from "./client";
import type {
  NavTrend,
  PortfolioSummary,
  PositionDto,
  SettlementPreviewDto,
} from "./types";

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

export async function getSummary(): Promise<PortfolioSummary> {
  const envelope = await request<PortfolioSummary>("/v1/portfolio/summary");
  return envelope.data;
}

export async function getPositions(): Promise<PositionDto[]> {
  const envelope = await request<PositionDto[]>("/v1/portfolio/positions");
  return envelope.data;
}

export async function getNavTrend(days = 60): Promise<NavTrend> {
  const envelope = await request<NavTrend>(
    `/v1/portfolio/nav-trend?days=${days}`,
  );
  return envelope.data;
}

// TARGET: 替换为 POST /fills/{id}/preview；金额在服务端以 NUMERIC 计算。
// 当前为客户端预览实现，仅供成交草稿弹窗展示确定性结果。
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

