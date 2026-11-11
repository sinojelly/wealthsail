function diagnosticsEnabled(): boolean {
  return new URLSearchParams(window.location.search).get("perf") === "1";
}

function milliseconds(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Explicit opt-in only; never include account data or request query strings. */
export function recordFrontendTiming(
  phase: string,
  durationMs = performance.now(),
): void {
  if (!diagnosticsEnabled()) return;
  console.info(
    "frontend_performance",
    JSON.stringify({ phase, durationMs: milliseconds(durationMs) }),
  );
}

export function installFrontendPerformance(): () => void {
  if (!diagnosticsEnabled()) return () => {};
  recordFrontendTiming("javascript_ready");

  const reportResource = (resource: PerformanceResourceTiming) => {
    const url = new URL(resource.name, window.location.origin);
    if (
      url.origin !== window.location.origin ||
      !url.pathname.startsWith("/assets/")
    )
      return;
    console.info(
      "frontend_performance",
      JSON.stringify({
        phase: "asset",
        asset: url.pathname.slice("/assets/".length),
        durationMs: milliseconds(resource.duration),
        // The pre-request interval includes connection/setup; headers is the
        // request-to-first-byte wait, and body is the response download.
        queueMs: milliseconds(
          Math.max(0, resource.requestStart - resource.fetchStart),
        ),
        headersMs: milliseconds(
          Math.max(0, resource.responseStart - resource.requestStart),
        ),
        bodyMs: milliseconds(
          Math.max(0, resource.responseEnd - resource.responseStart),
        ),
        transferBytes: resource.transferSize,
        encodedBytes: resource.encodedBodySize,
        decodedBytes: resource.decodedBodySize,
        protocol: resource.nextHopProtocol,
      }),
    );
  };
  const observers: PerformanceObserver[] = [];
  const supported =
    typeof PerformanceObserver === "undefined"
      ? []
      : PerformanceObserver.supportedEntryTypes;
  if (supported.includes("resource")) {
    const observer = new PerformanceObserver((entries) => {
      for (const entry of entries.getEntries())
        reportResource(entry as PerformanceResourceTiming);
    });
    observer.observe({ type: "resource", buffered: true });
    observers.push(observer);
  }
  const reportNavigation = () => {
    const navigation = performance.getEntriesByType("navigation")[0] as
      PerformanceNavigationTiming | undefined;
    if (navigation) {
      console.info(
        "frontend_performance",
        JSON.stringify({
          phase: "navigation",
          ttfbMs: milliseconds(navigation.responseStart - navigation.startTime),
          htmlDownloadMs: milliseconds(
            navigation.responseEnd - navigation.responseStart,
          ),
          domContentLoadedMs: milliseconds(navigation.domContentLoadedEventEnd),
          loadMs: milliseconds(navigation.loadEventEnd),
        }),
      );
    }
    if (!supported.includes("resource")) {
      for (const entry of performance.getEntriesByType("resource"))
        reportResource(entry as PerformanceResourceTiming);
    }
  };
  // Let navigation timing's loadEventEnd be finalized before taking the snapshot.
  const onLoad = () => {
    window.setTimeout(reportNavigation, 0);
  };
  if (supported.includes("paint")) {
    const observer = new PerformanceObserver((entries) => {
      for (const paint of entries.getEntries())
        recordFrontendTiming(paint.name, paint.startTime);
    });
    observer.observe({ type: "paint", buffered: true });
    observers.push(observer);
  }
  if (document.readyState === "complete") onLoad();
  else window.addEventListener("load", onLoad, { once: true });
  return () => {
    window.removeEventListener("load", onLoad);
    observers.forEach((observer) => observer.disconnect());
  };
}
