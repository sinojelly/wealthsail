import { useEffect, useState } from "react";
import { BrowserRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { AppRoutes } from "./app/router";
import { queryClient } from "./app/query-client";
import { bootstrapSession } from "./app/auth-session";

export default function App() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let mounted = true;
    void bootstrapSession().finally(() => {
      if (mounted) setReady(true);
    });
    return () => {
      mounted = false;
    };
  }, []);

  if (!ready) {
    return <div className="page-loading" role="status">正在恢复安全会话…</div>;
  }
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
