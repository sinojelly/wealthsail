import {
  Component,
  lazy,
  Suspense,
  type ComponentProps,
  type ReactNode,
} from "react";

type Props = ComponentProps<
  typeof import("./FundNavTrendChartContent").default
>;

const ChartContent = lazy(() => import("./FundNavTrendChartContent"));

class ChartLoadBoundary extends Component<
  { fundCode: string; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div
          className="op-match-chart-placeholder"
          role="alert"
          aria-label={`${this.props.fundCode}图表加载状态`}
        >
          <strong>净值图表加载失败</strong>
          <p>报告内容和参考买卖点可继续查看，刷新页面后重新加载图表。</p>
          <button type="button" onClick={() => window.location.reload()}>
            刷新页面重载图表
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

/** Keep report text and price panels independent of the chart download. */
export default function FundNavTrendChart(props: Props) {
  if (!/^\d{6}$/.test(props.fundCode)) return null;
  return (
    <ChartLoadBoundary key={props.fundCode} fundCode={props.fundCode}>
      <Suspense
        fallback={
          <div className="op-match-chart-placeholder" role="status">
            <strong>单位净值走势</strong>
            <p>正在加载净值图表组件…</p>
          </div>
        }
      >
        <ChartContent {...props} />
      </Suspense>
    </ChartLoadBoundary>
  );
}
