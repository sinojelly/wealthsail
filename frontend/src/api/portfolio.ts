import { request } from "./client";
import type {
  NavTrend,
  PortfolioSummary,
  PositionDto,
} from "./types";

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
