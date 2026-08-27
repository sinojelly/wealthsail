import assert from "node:assert/strict";
import test from "node:test";

import { toCsv } from "../src/csv.mjs";

test("CSV 正确转义逗号、双引号和换行", () => {
  const csv = toCsv([{ name: "基金,\"A\"", note: "第一行\n第二行" }], ["name", "note"]);

  assert.equal(csv, '\uFEFFname,note\r\n"基金,""A""","第一行\n第二行"\r\n');
});

test("CSV 阻断可能被表格软件执行的公式", () => {
  const csv = toCsv(
    [{ name: "=HYPERLINK(\"https://example.invalid\")", amount: "-10.00" }],
    ["name", "amount"],
  );

  assert.equal(
    csv,
    '\uFEFFname,amount\r\n"\'=HYPERLINK(""https://example.invalid"")",\'-10.00\r\n',
  );
});
