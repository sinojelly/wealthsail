import { useCallback, useState } from "react";
import "../macro-evidence.css";

/** Keep one SVG unit equal to one CSS pixel as the evidence column resizes. */
export function useEvidenceChart() {
  const [width, setWidth] = useState(360);
  const ref = useCallback((node: HTMLElement | null) => {
    if (!node) return;
    const update = (next: number) => {
      if (Number.isFinite(next) && next > 0) setWidth(next);
    };
    update(node.getBoundingClientRect().width);
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.target === node) update(entry.contentRect.width);
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [
    ref,
    { width, height: 280, left: 52, right: width - 20, top: 30, bottom: 236 },
  ] as const;
}
