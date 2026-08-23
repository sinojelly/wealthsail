import Decimal from "decimal.js";

export interface ImportRow {
  rowNumber: number;
  assetType: string;
  symbol: string;
  name: string;
  quantity: string;
  referencePrice: string;
  valid: boolean;
  issue?: string;
}

const supportedTypes = new Set(["STOCK", "ETF", "FUND"]);

function validDecimal(value: string): boolean {
  try {
    return new Decimal(value).greaterThan(0);
  } catch {
    return false;
  }
}

export function parseImportRows(source: string): ImportRow[] {
  return source
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line, index) => {
      const [
        assetType = "",
        symbol = "",
        name = "",
        quantity = "",
        referencePrice = "",
      ] = line.split(",").map((cell) => cell.trim());
      const issue = !supportedTypes.has(assetType)
        ? "资产类型不支持"
        : !symbol
          ? "缺少证券/基金代码"
          : !name
            ? "缺少资产名称"
            : !validDecimal(quantity)
              ? "份额必须大于 0"
              : !validDecimal(referencePrice)
                ? "价格/净值必须大于 0"
                : undefined;
      return {
        rowNumber: index + 1,
        assetType,
        symbol,
        name,
        quantity,
        referencePrice,
        valid: !issue,
        issue,
      };
    });
}
