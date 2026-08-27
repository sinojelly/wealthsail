import { CheckCircle2, Clock3, RefreshCw, TriangleAlert } from "lucide-react";
import type {
  TiantianImportConfirmation,
  TiantianImportDetail,
} from "../../../api/types";

interface Props {
  result: Omit<TiantianImportConfirmation, "metricsStatus"> & {
    metricsStatus: NonNullable<TiantianImportDetail["metricsStatus"]>;
  };
  restored?: TiantianImportDetail;
  retrying: boolean;
  onRetry: () => void;
}

export default function ImportResultStep({
  result,
  restored,
  retrying,
  onRetry,
}: Props) {
  const metricsStatus = restored?.metricsStatus ?? result.metricsStatus;
  return (
    <div className="import-result-step">
      <CheckCircle2 size={28} aria-hidden="true" />
      <div>
        <h3>账本基线已确认</h3>
        <p>
          已确认 {result.positionCount} 个当前持仓；导入{" "}
          {result.historyImported} 条来源流水， 跳过 {result.historyDuplicated}{" "}
          条重复记录。
        </p>
        {metricsStatus === "PENDING" ? (
          <p className="import-metrics-status">
            <Clock3 size={14} />
            区间指标正在后台计算
          </p>
        ) : null}
        {metricsStatus === "READY" ? (
          <p className="form-ok">持有区间指标已就绪。</p>
        ) : null}
        {metricsStatus === "PARTIAL" || metricsStatus === "CONFLICT" ? (
          <p className="import-warning">
            <TriangleAlert size={14} />
            指标已生成，但存在数据质量提示。
          </p>
        ) : null}
        {metricsStatus === "FAILED" ? (
          <button
            className="btn-secondary"
            type="button"
            disabled={retrying}
            onClick={onRetry}
          >
            <RefreshCw size={14} />
            {retrying ? "正在重试…" : "重试区间指标"}
          </button>
        ) : null}
      </div>
    </div>
  );
}
