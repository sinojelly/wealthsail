import { useState, type CSSProperties, type ReactNode } from "react";

export default function ObservationSection({
  title,
  label = title,
  className,
  style,
  defaultExpanded = true,
  children,
}: {
  title: string;
  label?: string;
  className?: string;
  style?: CSSProperties;
  defaultExpanded?: boolean;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <section aria-label={label} className={className} style={style}>
      <details
        className="op-observation-section op-print-disclosure"
        open={expanded}
        onToggle={(event) => setExpanded(event.currentTarget.open)}
      >
        <summary>
          <h3>{title}</h3>
          <span className="op-observation-section-action">
            {expanded ? "收起" : "展开"}
          </span>
        </summary>
        <div className="op-observation-section-body">{children}</div>
      </details>
    </section>
  );
}
