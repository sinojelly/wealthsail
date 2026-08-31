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

function parseCsvLine(line: string): { cells: string[]; complete: boolean } {
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (character === '"') {
      if (quoted && line[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === "," && !quoted) {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += character;
    }
  }
  cells.push(cell.trim());
  return { cells, complete: !quoted };
}

export function parseImportRows(source: string): ImportRow[] {
  return source
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line, index) => {
      const parsed = parseCsvLine(line);
      const { cells } = parsed;
      const [
        assetType = "",
        symbol = "",
        name = "",
        quantity = "",
        referencePrice = "",
      ] = cells;
      const issue = !parsed.complete
        ? "CSV 引号不完整"
        : cells.length !== 5
          ? "每行必须包含 5 个字段"
          : !supportedTypes.has(assetType)
            ? "资产类型不支持"
            : !/^\d{6}$/.test(symbol)
              ? "代码必须为 6 位数字"
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
