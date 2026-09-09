import {
  Children,
  createContext,
  isValidElement,
  useContext,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function plainText(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) => {
      if (typeof child === "string" || typeof child === "number") {
        return String(child);
      }
      if (isValidElement<{ children?: ReactNode }>(child)) {
        return plainText(child.props.children);
      }
      return "";
    })
    .join("")
    .trim();
}

function movementClass(children: ReactNode): string | undefined {
  const value = plainText(children);
  // 场外基金的涨跌幅会附带最近净值日期，例如“-1.03%（9/7）”。
  // 日期只是数据口径说明，不应影响涨跌颜色判断。
  const datedPercentage = "[0-9]+(?:\\.[0-9]+)?%(?:（[0-9]{1,2}/[0-9]{1,2}）)?";
  if (new RegExp(`^\\+${datedPercentage}$`).test(value)) {
    return "report-value-up";
  }
  if (new RegExp(`^-${datedPercentage}$`).test(value)) {
    return "report-value-down";
  }
  return undefined;
}

function holdingPriceParts(children: ReactNode) {
  const matched = plainText(children).match(
    /^(.*?)（持仓\s*([+-][0-9]+(?:\.[0-9]+)?%|--)）$/,
  );
  return matched ? { price: matched[1] || "--", pnlRate: matched[2] } : null;
}

function holdingMarketValueParts(children: ReactNode) {
  const matched = plainText(children).match(
    /^(.*?)（盈亏\s*([+-]?[0-9][0-9,]*(?:\.[0-9]{2})?|--)）$/,
  );
  return matched ? { marketValue: matched[1] || "--", pnlAmount: matched[2] } : null;
}

function isHoldingsTable(children: ReactNode): boolean {
  const header = plainText(children);
  return (
    header.startsWith("代码名称持仓成本价最新价市值当日仓位")
    || header.startsWith("代码名称持仓成本价最新价市值当日连续变化仓位")
  );
}

const HoldingsTableContext = createContext(false);

function ReportTableCell({ children }: ComponentPropsWithoutRef<"td">) {
  const isHoldingTable = useContext(HoldingsTableContext);
  const price = holdingPriceParts(children);
  if (price) {
    return (
      <td className="report-holding-price">
        <span className="report-holding-price-value">{price.price}</span>
        <span
          className={[
            "report-holding-return",
            movementClass(price.pnlRate),
          ].filter(Boolean).join(" ")}
        >
          {price.pnlRate}
        </span>
      </td>
    );
  }
  const marketValue = isHoldingTable ? holdingMarketValueParts(children) : null;
  if (marketValue) {
    const pnlClass = marketValue.pnlAmount === "--"
      ? "report-pnl-flat"
      : marketValue.pnlAmount.startsWith("+")
        ? "report-pnl-profit"
        : marketValue.pnlAmount.startsWith("-")
          ? "report-pnl-loss"
          : "report-pnl-flat";
    return (
      <td className="report-holding-market">
        <span className="report-holding-market-value">
          {marketValue.marketValue}
        </span>
        <span className={["report-holding-pnl", pnlClass].join(" ")}>
          {marketValue.pnlAmount}
        </span>
      </td>
    );
  }
  const cellText = plainText(children);
  return (
    <td className={movementClass(children)}>
      {cellText ? children : "--"}
    </td>
  );
}

export default function ReportMarkdown({ children }: { children: string }) {
  return (
    <div className="report-summary-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children: tableChildren }) => {
            const holdingsTable = isHoldingsTable(tableChildren);
            if (!holdingsTable) {
              return (
                <div className="report-markdown-table-scroll">
                  <table>{tableChildren}</table>
                </div>
              );
            }
            return (
              <HoldingsTableContext.Provider value>
                <div className="report-holdings-table-card">
                  <div className="report-markdown-table-scroll">
                    <table className="report-holdings-table">
                      {tableChildren}
                    </table>
                  </div>
                  <div className="report-holdings-legend" aria-label="涨跌颜色说明">
                    <span><i className="report-legend-up" />盈利 / 上涨</span>
                    <span><i className="report-legend-down" />亏损 / 下跌</span>
                  </div>
                </div>
              </HoldingsTableContext.Provider>
            );
          },
          td: ReportTableCell,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
