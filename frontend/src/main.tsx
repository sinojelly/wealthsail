import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import AppLoadBoundary from "./app/AppLoadBoundary";
import { installBrowserStaleBuildRecovery } from "./app/stale-build-recovery";
import "./index.css";
import "./styles/login.css";

installBrowserStaleBuildRecovery();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppLoadBoundary>
      <App />
    </AppLoadBoundary>
  </StrictMode>,
);
