import { describe, expect, it } from "vitest";
import { parseImportRows } from "./import-parser";

describe("CSV position import parser", () => {
  it("keeps a quoted comma inside the display name", () => {
    expect(
      parseImportRows('STOCK,600036,"招商银行,股份有限公司",1000,38.82'),
    ).toEqual([
      expect.objectContaining({
        symbol: "600036",
        name: "招商银行,股份有限公司",
        valid: true,
      }),
    ]);
  });

  it("rejects symbols that are not exactly six digits", () => {
    expect(parseImportRows("STOCK,60036,招商银行,1000,38.82")[0]).toEqual(
      expect.objectContaining({
        valid: false,
        issue: "代码必须为 6 位数字",
      }),
    );
  });

  it("rejects unmatched quotes instead of enabling a server-doomed submit", () => {
    expect(parseImportRows('STOCK,600036,"招商银行,1000,38.82')[0]).toEqual(
      expect.objectContaining({
        valid: false,
        issue: "CSV 引号不完整",
      }),
    );
  });
});
