import { useEffect, useRef, useState, type ComponentProps } from "react";
import FundNavTrendChart from "./FundNavTrendChart";

type ChartProps = ComponentProps<typeof FundNavTrendChart>;

function ChartSlot(props: ChartProps) {
  const { fundCode } = props;
  const container = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(
    () => typeof IntersectionObserver === "undefined",
  );

  useEffect(() => {
    if (active || !container.current) return;
    let disposed = false;
    const observer = new IntersectionObserver(
      (entries) => {
        if (disposed || !entries.some((entry) => entry.isIntersecting)) return;
        disposed = true;
        setActive(true);
        observer.disconnect();
      },
      { rootMargin: "240px" },
    );
    observer.observe(container.current);
    return () => {
      disposed = true;
      observer.disconnect();
    };
  }, [active]);

  return (
    <div
      ref={container}
      className="op-match-chart"
      role="region"
      aria-label={`${fundCode}净值走势图区域`}
    >
      {active ? (
        <FundNavTrendChart {...props} />
      ) : (
        <div className="op-match-chart-placeholder">
          <strong>单位净值走势</strong>
          <p>正在加载单位净值走势…</p>
        </div>
      )}
    </div>
  );
}

export default function ViewportFundChart(props: ChartProps) {
  return <ChartSlot key={props.fundCode} {...props} />;
}
