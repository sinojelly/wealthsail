import type { FillDraft } from "../../api/portfolio";
import type { QualityCode } from "../../api/generated/types";

export const equityCurve = [
  { date: "07-25", portfolio: "1768000", benchmark: "100.0" },
  { date: "07-28", portfolio: "1776000", benchmark: "100.4" },
  { date: "07-30", portfolio: "1769000", benchmark: "99.8" },
  { date: "08-01", portfolio: "1794000", benchmark: "101.1" },
  { date: "08-04", portfolio: "1812000", benchmark: "101.8" },
  { date: "08-06", portfolio: "1803000", benchmark: "101.3" },
  { date: "08-08", portfolio: "1831000", benchmark: "102.2" },
  { date: "08-11", portfolio: "1846000", benchmark: "102.5" },
  { date: "08-13", portfolio: "1838000", benchmark: "101.9" },
  { date: "08-15", portfolio: "1865000", benchmark: "102.8" },
  { date: "08-18", portfolio: "1889000", benchmark: "103.6" },
  { date: "08-19", portfolio: "1876000", benchmark: "103.1" },
  { date: "08-20", portfolio: "1902000", benchmark: "104.0" },
  { date: "08-21", portfolio: "1894000", benchmark: "103.8" },
  { date: "08-22", portfolio: "1909460", benchmark: "104.3" },
  { date: "08-23", portfolio: "1921840", benchmark: "104.7" },
];

export const allocation = [
  { name: "A股", value: "48.3", color: "#0d9488" },
  { name: "场内 ETF", value: "18.7", color: "#0ea5e9" },
  { name: "场外基金", value: "22.5", color: "#8b5cf6" },
  { name: "现金", value: "10.5", color: "#94a3b8" },
];

export interface Holding {
  symbol: string;
  name: string;
  type: string;
  quantity: string;
  marketValue: string;
  weight: string;
  pnl: string;
  pnlRate: string;
  price: string;
  quality: QualityCode;
}

export const holdings: Holding[] = [
  {
    symbol: "600036",
    name: "招商银行",
    type: "A股",
    quantity: "8,500",
    marketValue: "329970.00",
    weight: "17.17",
    pnl: "28472.00",
    pnlRate: "9.56",
    price: "38.82",
    quality: "FRESH",
  },
  {
    symbol: "510300",
    name: "沪深300ETF",
    type: "场内ETF",
    quantity: "62,000",
    marketValue: "256680.00",
    weight: "13.36",
    pnl: "21806.00",
    pnlRate: "9.28",
    price: "4.140",
    quality: "FRESH",
  },
  {
    symbol: "002230",
    name: "科大讯飞",
    type: "A股",
    quantity: "5,000",
    marketValue: "216400.00",
    weight: "11.26",
    pnl: "-18600.00",
    pnlRate: "-7.91",
    price: "43.28",
    quality: "FRESH",
  },
  {
    symbol: "110022",
    name: "易方达消费行业",
    type: "场外基金",
    quantity: "118,420.52",
    marketValue: "205182.15",
    weight: "10.68",
    pnl: "16420.15",
    pnlRate: "8.70",
    price: "1.7326",
    quality: "ESTIMATED",
  },
  {
    symbol: "600519",
    name: "贵州茅台",
    type: "A股",
    quantity: "100",
    marketValue: "169150.00",
    weight: "8.80",
    pnl: "12150.00",
    pnlRate: "7.74",
    price: "1691.50",
    quality: "FRESH",
  },
  {
    symbol: "161725",
    name: "招商中证白酒",
    type: "场外基金",
    quantity: "97,380.41",
    marketValue: "126824.77",
    weight: "6.60",
    pnl: "-8456.23",
    pnlRate: "-6.25",
    price: "1.3024",
    quality: "ESTIMATED",
  },
];

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
