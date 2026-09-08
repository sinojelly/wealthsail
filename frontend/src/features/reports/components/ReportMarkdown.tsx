import { Children, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function plainText(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) =>
      typeof child === "string" || typeof child === "number"
        ? String(child)
        : "",
    )
    .join("")
    .trim();
}

function movementClass(children: ReactNode): string | undefined {
  const value = plainText(children);
  if (/^\+[0-9]+(?:\.[0-9]+)?%$/.test(value)) return "report-value-up";
  if (/^-[0-9]+(?:\.[0-9]+)?%$/.test(value)) return "report-value-down";
  return undefined;
}

export default function ReportMarkdown({ children }: { children: string }) {
  return (
    <div className="report-summary-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children: tableChildren }) => (
            <div className="report-markdown-table-scroll">
              <table>{tableChildren}</table>
            </div>
          ),
          td: ({ children: cellChildren }) => (
            <td className={movementClass(cellChildren)}>{cellChildren}</td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
