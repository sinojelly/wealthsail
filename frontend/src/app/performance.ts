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
  console.info("frontend_performance", {
    phase,
    durationMs: milliseconds(durationMs),
  });
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
    console.info("frontend_performance", {
      phase: "asset",
      asset: url.pathname.slice("/assets/".length),
      durationMs: milliseconds(resource.duration),
      transferBytes: resource.transferSize,
      decodedBytes: resource.decodedBodySize,
    });
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
      console.info("frontend_performance", {
        phase: "navigation",
        ttfbMs: milliseconds(navigation.responseStart - navigation.startTime),
        htmlDownloadMs: milliseconds(
          navigation.responseEnd - navigation.responseStart,
        ),
        domContentLoadedMs: milliseconds(navigation.domContentLoadedEventEnd),
        loadMs: milliseconds(navigation.loadEventEnd),
      });
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
