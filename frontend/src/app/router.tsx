import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "../components/layout/AppShell";
import { useAuthSession } from "./auth-session";

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
const LoginPage = lazy(() => import("../features/login/LoginPage"));

function LazyPage({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="page-loading" role="status">
          正在加载工作区…
        </div>
      }
    >
      {children}
    </Suspense>
  );
}

/** 未登录访问受保护路由 → 登录页。 */
function RequireSession({ children }: { children: ReactNode }) {
  const session = useAuthSession();
  if (!session) return <Navigate replace to="/login" />;
  return <>{children}</>;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
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
            <LazyPage>
              <OperationsPage />
            </LazyPage>
          }
        />
      </Route>
      <Route path="*" element={<Navigate replace to="/dashboard" />} />
    </Routes>
  );
}
