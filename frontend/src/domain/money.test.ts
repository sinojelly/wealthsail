import { addMoney, formatMoney, subtractMoney } from "./money";

describe("money domain", () => {
  it("keeps settlement arithmetic exact instead of introducing float residue", () => {
    expect(subtractMoney("201382.15", "43323.28")).toBe("158058.87");
    expect(addMoney("158058.87", "19410.00")).toBe("177468.87");
  });

  it("formats a decimal string without losing cents", () => {
    expect(formatMoney("1921840.12")).toBe("¥1,921,840.12");
  });
});
