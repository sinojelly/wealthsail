import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import AppShell from "../components/layout/AppShell";
import { canViewOperations, useAuthSession } from "./auth-session";
import { loginRedirectPath } from "./navigation";
import { recordFrontendTiming } from "./performance";

const DashboardPage = lazy(() => import("../features/dashboard/DashboardPage"));
const OnboardingPage = lazy(
  () => import("../features/onboarding/OnboardingPage"),
);
const OperationsPage = lazy(
  () => import("../features/operations/OperationsPage"),
);
const ReportsPage = lazy(() => import("../features/reports/ReportsPage"));
const RiskPolicyPage = lazy(
  () => import("../features/risk-policy/RiskPolicyPage"),
);
const SettingsPage = lazy(() => import("../features/settings/SettingsPage"));
const MarketHot = lazy(() => import("../features/market/MarketHot"));
const FundScreenerPage = lazy(
  () => import("../features/fund-screener/FundScreenerPage"),
);
const LoginPage = lazy(() => import("../features/login/LoginPage"));

function PageReady({ children }: { children: ReactNode }) {
  const location = useLocation();
  useEffect(() => {
    recordFrontendTiming("route_ready");
  }, [location.pathname]);
  return <>{children}</>;
}

function LazyPage({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="page-loading" role="status">
          正在加载工作区…
        </div>
      }
    >
      <PageReady>{children}</PageReady>
    </Suspense>
  );
}

/** 未登录访问受保护路由时回到登录页。 */
function RequireSession({ children }: { children: ReactNode }) {
  const session = useAuthSession();
  const location = useLocation();
  if (!session) {
    return (
      <Navigate
        replace
        to={loginRedirectPath(
          location.pathname,
          location.search,
          location.hash,
        )}
      />
    );
  }
  return <>{children}</>;
}

/**
 * 运维接口仍会执行服务端 RBAC；此处同时阻止普通成员手工输入 URL 进入页面，
 * 避免只隐藏导航而留下前端直接访问路径。
 */
function RequireOperationsRole({ children }: { children: ReactNode }) {
  const session = useAuthSession();
  if (!canViewOperations(session)) {
    return (
      <section className="empty-state" role="alert">
        <h1>无权访问运维页面</h1>
        <p>该页面仅向家庭账户所有者和管理员开放。</p>
      </section>
    );
  }
  return <>{children}</>;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <LazyPage>
            <LoginPage />
          </LazyPage>
        }
      />
      <Route
        element={
          <RequireSession>
            <AppShell />
          </RequireSession>
        }
      >
        <Route index element={<Navigate replace to="/dashboard" />} />
        <Route
          path="dashboard"
          element={
            <LazyPage>
              <DashboardPage />
            </LazyPage>
          }
        />
        <Route
          path="onboarding"
          element={
            <LazyPage>
              <OnboardingPage />
            </LazyPage>
          }
        />
        <Route
          path="reports"
          element={
            <LazyPage>
              <ReportsPage />
            </LazyPage>
          }
        />
        <Route
          path="risk-policy"
          element={
            <LazyPage>
              <RiskPolicyPage />
            </LazyPage>
          }
        />
        <Route
          path="market-hot"
          element={
            <LazyPage>
              <MarketHot />
            </LazyPage>
          }
        />
        <Route
          path="fund-screener"
          element={
            <LazyPage>
              <FundScreenerPage />
            </LazyPage>
          }
        />
        <Route
          path="settings"
          element={
            <LazyPage>
              <SettingsPage />
            </LazyPage>
          }
        />
        <Route
          path="operations"
          element={
            <RequireOperationsRole>
              <LazyPage>
                <OperationsPage />
              </LazyPage>
            </RequireOperationsRole>
          }
        />
      </Route>
      <Route path="*" element={<Navigate replace to="/dashboard" />} />
    </Routes>
  );
}
