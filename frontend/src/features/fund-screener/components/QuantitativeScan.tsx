import { useState } from "react";
import type {
  ActionableZoneTrack,
  DailyResearch,
  PlanTrack,
  QuantitativeScanItem,
} from "../../../api/investment";
import {
  completeTrack,
  fundDisplayName,
  planPrice,
  planTimeBasisNote,
  entryReady,
  entryAllowed,
  planEntryStatus,
} from "../plan-display";
import { timeLabel } from "../macro-display";

export interface QuantScanTrackInput {
  reportRunId: string;
  subjectKind: "fund" | "nav";
  subjectId: string;
  planTrack: PlanTrack;
}
type Signal = QuantitativeScanItem["signals"][number];
function ready(
  item: QuantitativeScanItem,
  signal: Signal,
  track?: ActionableZoneTrack,
) {
  return entryReady(signal) && entryAllowed(item, track);
}
function preferred(item: QuantitativeScanItem) {
  return item.signals
    .flatMap((signal) => {
      const track = item.tracks.find(
        (row) => row.track === signal.track && completeTrack(row),
      );
      return track ? [{ signal, track }] : [];
    })
    .sort(
      (a, b) =>
        Number(ready(item, b.signal, b.track)) -
          Number(ready(item, a.signal, a.track)) ||
        distance(a.signal) - distance(b.signal) ||
        a.track.track.localeCompare(b.track.track),
    )[0];
}
function distance(signal?: Signal) {
  return signal?.inEntryZone
    ? 0
    : signal &&
        Number.isFinite(signal.distanceToEntryPct) &&
        signal.distanceToEntryPct >= 0
      ? signal.distanceToEntryPct
      : Infinity;
}
function ScanRow({
  item,
  reportRunId,
  onTrack,
}: {
  item: QuantitativeScanItem;
  reportRunId?: string | null;
  onTrack?: (input: QuantScanTrackInput) => Promise<void>;
}) {
  const chosen = preferred(item);
  const track = chosen?.track;
  const signal = chosen?.signal;
  const status = planEntryStatus(item, track, signal);
  const isReady = !status && !!signal && ready(item, signal, track);
  const code = item.fundCode || item.subjectId;
  const [state, setState] = useState<"idle" | "pending" | "saved">("idle");
  const [error, setError] = useState("");
  const canTrack =
    !!onTrack &&
    !!reportRunId &&
    !!track &&
    (item.subjectKind === "fund" || item.subjectKind === "nav");
  const price = (value: number | string | null | undefined) =>
    planPrice(value, item);
  const rsi = item.indicators.rsi14;
  const other = item.tracks.find(
    (row) => row.track !== track?.track && completeTrack(row),
  );
  const details = (row: ActionableZoneTrack) =>
    `入场 ${price(row.entryZoneMin)}–${price(row.entryZoneMax)} | 止损 ${price(row.stopLossPrice)} | T1 ${price(row.takeProfitT1)} / T2 ${price(row.takeProfitT2)}`;
  async function save() {
    if (
      !canTrack ||
      !onTrack ||
      !reportRunId ||
      !track ||
      state !== "idle" ||
      item.subjectKind === "index"
    )
      return;
    setState("pending");
    setError("");
    try {
      await onTrack({
        reportRunId,
        subjectKind: item.subjectKind,
        subjectId: item.subjectId,
        planTrack: track.track,
      });
      setState("saved");
    } catch (failure) {
      setState("idle");
      setError(
        failure instanceof Error ? failure.message : "保存失败，请重试。",
      );
    }
  }
  return (
    <tr data-fund-code={code}>
      <th scope="row">
        <strong>{fundDisplayName(item.label, code)}</strong>
        {item.subscriptionLabel && (
          <span className="op-subscription-restricted">
            {item.subscriptionLabel}
          </span>
        )}
        <small>{code}</small>
        {planTimeBasisNote(item) && <small>{planTimeBasisNote(item)}</small>}
      </th>
      <td>
        {price(item.currentPrice)}
        <small>{timeLabel(item.asOf)}</small>
      </td>
      <td>
        {rsi != null && Number.isFinite(Number(rsi))
          ? Number(rsi).toFixed(2)
          : "—"}
      </td>
      <td>
        <span
          className={
            status?.warning ? "op-entry-warning" : isReady ? "op-scan-hit" : ""
          }
        >
          {status?.text ??
            (isReady
              ? track?.track === "LEFT_VALUE"
                ? "🎯已入左侧买区"
                : "📈已入右侧买区"
              : signal?.inEntryZone
                ? track?.track === "RIGHT_TREND"
                  ? "价格近均线·待趋势触发"
                  : "已处左侧参考区·待条件触发"
                : Number.isFinite(distance(signal))
                  ? `⏳距买区 ${distance(signal).toFixed(2)}%`
                  : "价格依据待补齐")}
        </span>
        {track && (
          <small>
            {track.track === "LEFT_VALUE" ? "左侧低吸" : "右侧趋势"} ·{" "}
            {signal?.triggerState === "CANDIDATE"
              ? "技术条件成立"
              : "技术条件待触发"}
          </small>
        )}
        {other && (
          <details>
            <summary>
              备选{other.track === "LEFT_VALUE" ? "左" : "右"}轨
            </summary>
            <p>{details(other)}</p>
          </details>
        )}
      </td>
      <td>
        {price(track?.entryZoneMin)}–{price(track?.entryZoneMax)}
      </td>
      <td className="op-scan-stop">{price(track?.stopLossPrice)}</td>
      <td>
        {price(track?.takeProfitT1)} / {price(track?.takeProfitT2)}
      </td>
      <td>
        <button
          type="button"
          disabled={!canTrack || state !== "idle"}
          aria-label={`${state === "saved" ? "已跟踪" : "+ 跟踪"} ${code}`}
          onClick={() => void save()}
        >
          {state === "saved"
            ? "已跟踪"
            : state === "pending"
              ? "保存中…"
              : "+ 跟踪"}
        </button>
        {error && <p role="alert">{error}</p>}
      </td>
    </tr>
  );
}
export default function QuantitativeScan({
  data,
  onTrack,
}: {
  data?: DailyResearch | null;
  onTrack?: (input: QuantScanTrackInput) => Promise<void>;
}) {
  const scan = data?.report?.quantitativeScan;
  if (!scan?.items.length) return null;
  const rows = [...scan.items].sort((a, b) => {
    const first = preferred(a);
    const second = preferred(b);
    return (
      Number(!!a.subscriptionRestricted) - Number(!!b.subscriptionRestricted) ||
      Number(!!second && ready(b, second.signal, second.track)) -
        Number(!!first && ready(a, first.signal, first.track)) ||
      distance(first?.signal) - distance(second?.signal) ||
      (a.fundCode || a.subjectId).localeCompare(b.fundCode || b.subjectId)
    );
  });
  return (
    <details
      key={`scan:${data?.reportRunId ?? data?.report?.asOf ?? "no-report"}`}
      className="op-quant-scan op-print-disclosure"
    >
      <summary>
        查看全市场 {rows.length}{" "}
        只核心赛道基金量化扫描总表（含红利/消费/白酒/纳指等点位）
      </summary>
      <p className="op-scan-description">
        代表池逐项扫描 · {timeLabel(scan.asOf)}
        。技术成立且已入区优先，再按距买区由近及远排序；价格位置与技术条件分别展示。
      </p>
      <div
        className="op-scan-scroll"
        role="region"
        aria-label="量化扫描表，可横向滚动"
        tabIndex={0}
      >
        <table aria-label="核心赛道基金量化扫描">
          <thead>
            <tr>
              {[
                "基金 / 代码",
                "最新披露净值",
                "RSI14",
                "买区与技术状态",
                "建议买入区间",
                "止损参考",
                "止盈 T1 / T2",
                "跟踪",
              ].map((label) => (
                <th scope="col" key={label}>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <ScanRow
                key={`${data?.reportRunId}:${item.subjectKind}:${item.subjectId}`}
                item={item}
                reportRunId={data?.reportRunId}
                onTrack={onTrack}
              />
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
