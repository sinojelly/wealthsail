import { Component, type ErrorInfo, type ReactNode } from "react";

interface AppLoadBoundaryProps {
  children: ReactNode;
  reload?: () => void;
}

interface AppLoadBoundaryState {
  failed: boolean;
}

/** Keep a failed lazy route actionable instead of leaving an empty root node. */
export default class AppLoadBoundary extends Component<
  AppLoadBoundaryProps,
  AppLoadBoundaryState
> {
  state: AppLoadBoundaryState = { failed: false };

  static getDerivedStateFromError(): AppLoadBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("app_load_failure", error, info.componentStack);
  }

  private readonly reload = () => {
    (this.props.reload ?? (() => window.location.reload()))();
  };

  render() {
    if (this.state.failed) {
      return (
        <main className="app-load-failure" role="alert">
          <section className="app-load-failure-card">
            <span className="app-load-failure-mark" aria-hidden="true">
              W
            </span>
            <h1>页面加载失败</h1>
            <p>
              可能刚刚发布了新版本，或网络暂时不可用。刷新后会加载最新的完整页面。
            </p>
            <button type="button" onClick={this.reload}>
              刷新到最新版本
            </button>
          </section>
        </main>
      );
    }
    return this.props.children;
  }
}
