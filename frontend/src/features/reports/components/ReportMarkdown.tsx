import { Children, isValidElement, type ReactNode } from "react";
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
  if (/^\+[0-9]+(?:\.[0-9]+)?%$/.test(value)) return "report-value-up";
  if (/^-[0-9]+(?:\.[0-9]+)?%$/.test(value)) return "report-value-down";
  return undefined;
}

function holdingPriceParts(children: ReactNode) {
  const matched = plainText(children).match(
    /^(.*?)（持仓\s*([+-][0-9]+(?:\.[0-9]+)?%|--)）$/,
  );
  return matched ? { price: matched[1] || "--", pnlRate: matched[2] } : null;
}

function isHoldingsTable(children: ReactNode): boolean {
  return plainText(children).startsWith(
    "代码名称持仓成本价最新价市值当日仓位",
  );
}

export default function ReportMarkdown({ children }: { children: string }) {
  return (
    <div className="report-summary-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children: tableChildren }) => {
            if (!isHoldingsTable(tableChildren)) {
              return (
                <div className="report-markdown-table-scroll">
                  <table>{tableChildren}</table>
                </div>
              );
            }
            return (
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
            );
          },
          td: ({ children: cellChildren }) => {
            const price = holdingPriceParts(cellChildren);
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
            const cellText = plainText(cellChildren);
            return (
              <td className={movementClass(cellChildren)}>
                {cellText ? cellChildren : "--"}
              </td>
            );
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
