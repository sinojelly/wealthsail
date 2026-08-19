export type Sentiment = "bullish" | "bearish" | "neutral";
export type View = "chat" | "portfolio" | "watchlist";
export type Channel = "premarket" | "diagnosis";

export interface StockData {
  symbol: string;
  name: string;
  exchange: "HK" | "SH" | "SZ";
  price: number;
  change: number;
  changePercent: number;
  support: number;
  resistance: number;
  sentiment: Sentiment;
  volume: string;
  pe: number;
  high52w: number;
  low52w: number;
  sparkline: number[];
}

export interface Position {
  stock: StockData;
  shares: number;
  costBasis: number;
}

export type MessageType =
  | "premarket-report"
  | "stock-analysis"
  | "trade-confirm"
  | "thinking"
  | "text";

export interface TradeData {
  action: "buy" | "sell";
  symbol: string;
  name: string;
  shares: number;
  price: number;
}

export interface Message {
  id: string;
  role: "agent" | "user";
  type: MessageType;
  content?: string;
  timestamp: string;
  stocks?: StockData[];
  tradeData?: TradeData;
}

export const stocks: Record<string, StockData> = {
  "00700": {
    symbol: "00700",
    name: "腾讯控股",
    exchange: "HK",
    price: 375.2,
    change: -4.8,
    changePercent: -1.26,
    support: 368.0,
    resistance: 385.0,
    sentiment: "bullish",
    volume: "18.3M",
    pe: 22.4,
    high52w: 430.2,
    low52w: 298.0,
    sparkline: [
      340, 355, 348, 362, 358, 371, 368, 375, 380, 378, 382, 375.2,
    ],
  },
  "600519": {
    symbol: "600519",
    name: "贵州茅台",
    exchange: "SH",
    price: 1420.5,
    change: 12.5,
    changePercent: 0.89,
    support: 1400.0,
    resistance: 1450.0,
    sentiment: "bearish",
    volume: "2.1M",
    pe: 38.2,
    high52w: 1550.0,
    low52w: 1180.0,
    sparkline: [
      1280, 1310, 1350, 1380, 1370, 1390, 1410, 1405, 1415, 1420, 1418,
      1420.5,
    ],
  },
  "300750": {
    symbol: "300750",
    name: "宁德时代",
    exchange: "SZ",
    price: 228.4,
    change: -16.6,
    changePercent: -6.78,
    support: 220.0,
    resistance: 240.0,
    sentiment: "bearish",
    volume: "42.8M",
    pe: 28.6,
    high52w: 312.0,
    low52w: 180.0,
    sparkline: [
      280, 265, 258, 252, 261, 248, 243, 240, 245, 238, 232, 228.4,
    ],
  },
  "03690": {
    symbol: "03690",
    name: "美团",
    exchange: "HK",
    price: 156.8,
    change: 14.8,
    changePercent: 10.42,
    support: 150.0,
    resistance: 165.0,
    sentiment: "bullish",
    volume: "36.5M",
    pe: 45.8,
    high52w: 168.0,
    low52w: 95.0,
    sparkline: [
      112, 120, 118, 125, 132, 138, 142, 148, 152, 154, 158, 156.8,
    ],
  },
  "09988": {
    symbol: "09988",
    name: "阿里巴巴",
    exchange: "HK",
    price: 86.4,
    change: 2.15,
    changePercent: 2.55,
    support: 82.0,
    resistance: 92.0,
    sentiment: "bullish",
    volume: "28.2M",
    pe: 18.9,
    high52w: 104.5,
    low52w: 68.0,
    sparkline: [72, 74, 78, 76, 80, 82, 85, 84, 83, 86, 85, 86.4],
  },
  "00941": {
    symbol: "00941",
    name: "中国移动",
    exchange: "HK",
    price: 78.25,
    change: -0.35,
    changePercent: -0.45,
    support: 76.0,
    resistance: 82.0,
    sentiment: "neutral",
    volume: "8.4M",
    pe: 12.1,
    high52w: 88.0,
    low52w: 62.0,
    sparkline: [
      68, 70, 72, 74, 76, 75, 78, 77, 79, 78, 78.5, 78.25,
    ],
  },
};

export const portfolio: Position[] = [
  { stock: stocks["00700"], shares: 500, costBasis: 380.0 },
  { stock: stocks["600519"], shares: 30, costBasis: 1380.0 },
  { stock: stocks["300750"], shares: 200, costBasis: 245.0 },
  { stock: stocks["03690"], shares: 1000, costBasis: 142.0 },
];

export const watchlist: StockData[] = [
  stocks["00700"],
  stocks["600519"],
  stocks["300750"],
  stocks["03690"],
  stocks["09988"],
  stocks["00941"],
];

export const preMarketStocks = [
  stocks["00700"],
  stocks["600519"],
  stocks["300750"],
  stocks["03690"],
];

export const initialMessages: Message[] = [
  {
    id: "1",
    role: "agent",
    type: "premarket-report",
    timestamp: "08:30",
    stocks: preMarketStocks,
  },
  {
    id: "2",
    role: "user",
    type: "text",
    content: "帮我看看宁德时代今天怎么跌了？还能拿吗？",
    timestamp: "10:23",
  },
  {
    id: "3",
    role: "agent",
    type: "thinking",
    content: "正在检索 [宁德时代] 实时新闻与技术面...",
    timestamp: "10:23",
  },
  {
    id: "4",
    role: "agent",
    type: "stock-analysis",
    timestamp: "10:24",
    stocks: [stocks["300750"]],
    content:
      "**下跌原因**\n外资大幅减仓新能源板块，欧盟对华电动车反补贴税落地预期升温，今日放量跌幅 6.78%，出货信号明显。\n**持仓建议**\n您当前成本 ¥245，现价 ¥228.4，浮亏约 ¥3,320（-6.78%）\n短线支撑位 ¥220 是关键防守线。若跌破且成交量继续放大，建议减仓 1/2 止损\n中线若 ¥220–228 企稳且有明显买盘介入，可持仓观望等待反弹至 ¥240 附近减仓\n**操作预案**\n设置 ¥218 为硬止损价，触发后无论情绪如何坚决执行",
  },
  {
    id: "5",
    role: "user",
    type: "text",
    content: "/buy 03690 156 300",
    timestamp: "10:31",
  },
  {
    id: "6",
    role: "agent",
    type: "trade-confirm",
    timestamp: "10:31",
    tradeData: {
      action: "buy",
      symbol: "03690",
      name: "美团",
      shares: 300,
      price: 156,
    },
  },
];
