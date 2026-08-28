import { screen } from "@testing-library/react";
import type { MarketHotResponse } from "../../api/market";
import { renderWithSession } from "../../test/render-with-session";
import MarketHot from "./MarketHot";

const getMarketHotMock = vi.hoisted(() => vi.fn());

vi.mock("../../api/market", async () => {
  const actual = await vi.importActual<typeof import("../../api/market")>(
    "../../api/market",
  );
  return { ...actual, getMarketHot: getMarketHotMock };
});

function response(
  qualityStatus: MarketHotResponse["qualityStatus"],
): MarketHotResponse {
  return {
    items:
      qualityStatus === "UNAVAILABLE"
        ? []
        : [
            {
              symbol: "600000",
              name: "浦发银行",
              price: 12.34,
              change_pct: 1.25,
              volume: 1000,
              turnover: 2_000_000,
            },
          ],
    source: qualityStatus === "UNAVAILABLE" ? "unavailable" : "AkShare-OpenSource",
    sourceAsOf:
      qualityStatus === "UNAVAILABLE" ? null : "2026-08-29T09:31:00+08:00",
    retrievedAt:
      qualityStatus === "UNAVAILABLE" ? null : "2026-08-29T09:31:03+08:00",
    qualityStatus,
    methodVersion:
      qualityStatus === "ESTIMATED"
        ? "market-hot/v1-candidate-pool"
        : qualityStatus === "UNAVAILABLE"
          ? null
          : "market-hot/v1",
    updatedAt: "2026-08-29T09:31:03+08:00",
  };
}

beforeEach(() => {
  getMarketHotMock.mockReset();
});

it("shows a governed fresh snapshot without claiming unconditional realtime data", async () => {
  getMarketHotMock.mockResolvedValue(response("FRESH"));

  renderWithSession(<MarketHot />, "/market");

  expect(
    await screen.findByRole("heading", { name: "市场热点" }),
  ).toBeInTheDocument();
  expect(screen.queryByText(/实时更新/)).not.toBeInTheDocument();
  expect(await screen.findByText("新鲜快照")).toBeInTheDocument();
  expect(screen.getByText(/来源：AkShare-OpenSource/)).toBeInTheDocument();
  expect(screen.getByText(/方法：market-hot\/v1/)).toBeInTheDocument();
});

it.each([
  ["ESTIMATED", "估算热点", "不代表全市场实时排名"],
  ["STALE", "快照已过期", "请勿视为实时行情"],
  ["UNAVAILABLE", "热点数据不可用", "暂无可审计的市场热点快照"],
] as const)(
  "renders an explicit %s quality warning",
  async (qualityStatus, label, explanation) => {
    getMarketHotMock.mockResolvedValue(response(qualityStatus));

    renderWithSession(<MarketHot />, "/market");

    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(explanation))).toBeInTheDocument();
  },
);
