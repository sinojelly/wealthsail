import { useRef, useState } from "react";
import { History } from "lucide-react";
import {
  useCancelTiantianImport,
  useConfirmTiantianImport,
  usePreviewTiantianImport,
  useRetryTiantianMetrics,
  useTiantianImport,
  useTiantianImports,
} from "../../../api/hooks";
import { HttpError } from "../../../api/client";
import { useAuthSession } from "../../../app/auth-session";
import type {
  TiantianImportConfirmation,
  TiantianImportDetail,
} from "../../../api/types";
import Dialog from "../../../components/accessibility/Dialog";
import ImportConfirmStep from "./ImportConfirmStep";
import ImportPackageStep from "./ImportPackageStep";
import ImportPreviewStep from "./ImportPreviewStep";
import ImportResultStep from "./ImportResultStep";

function commandKey(prefix: string): string {
  const suffix =
    globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  return `${prefix}-${suffix}`;
}

function errorText(error: unknown): string {
  if (!(error instanceof HttpError))
    return error instanceof Error ? error.message : "操作失败";
  const fields = error.fieldErrors.map(
    (item) => `${item.field}：${item.message}`,
  );
  return [error.message, ...fields].join("；");
}

export default function TiantianImportWizard() {
  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState("");
  const [accountName, setAccountName] = useState("天天基金账户");
  const [detail, setDetail] = useState<TiantianImportDetail | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [result, setResult] = useState<TiantianImportConfirmation | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const confirmationInFlight = useRef(false);
  const previewCommand = useRef<{ signature: string; key: string } | null>(
    null,
  );
  const confirmCommand = useRef<{ signature: string; key: string } | null>(
    null,
  );
  const session = useAuthSession();
  const previewMutation = usePreviewTiantianImport();
  const confirmMutation = useConfirmTiantianImport();
  const cancelMutation = useCancelTiantianImport();
  const retryMutation = useRetryTiantianMetrics();
  const history = useTiantianImports();
  const restored = useTiantianImport(selectedBatchId);
  const currentDetail = newestDetail(detail, restored.data ?? null);
  const canWrite =
    !session || ["OWNER", "ADMIN", "EDITOR"].includes(session.role);
  const existingAccounts = Array.from(
    new Map(
      (history.data?.items ?? []).map((item) => [
        item.account.accountId,
        item.account,
      ]),
    ).values(),
  );

  async function previewPackage() {
    if (!file || (!accountId && !accountName.trim())) return;
    setError(null);
    try {
      const signature = [
        file.name,
        file.size,
        file.lastModified,
        accountId,
        accountName.trim(),
      ].join(":");
      const next = await previewMutation.mutateAsync({
        file,
        accountId: accountId || undefined,
        accountName: accountId ? undefined : accountName.trim(),
        idempotencyKey: stableCommandKey(
          previewCommand,
          signature,
          "tiantian-preview",
        ),
      });
      setDetail(next);
      setSelectedBatchId(next.batchId);
      setResult(null);
      setDecisions({});
      setConfirmEmpty(false);
      setReviewed(false);
      confirmCommand.current = null;
    } catch (cause) {
      setError(errorText(cause));
    }
  }

  async function confirmImport() {
    if (!currentDetail || confirmationInFlight.current) return;
    confirmationInFlight.current = true;
    setError(null);
    setStale(false);
    try {
      const payload = {
        decisions: Object.entries(decisions)
          .filter(([, startedOn]) => Boolean(startedOn))
          .map(([itemId, startedOn]) => ({ itemId, startedOn })),
        confirmEmpty,
      };
      const confirmation = await confirmMutation.mutateAsync({
        batchId: currentDetail.batchId,
        version: currentDetail.version,
        payload,
        idempotencyKey: stableCommandKey(
          confirmCommand,
          JSON.stringify([
            currentDetail.batchId,
            currentDetail.version,
            payload,
          ]),
          "tiantian-confirm",
        ),
      });
      setResult(confirmation);
      setDetail({
        ...currentDetail,
        status: "CONFIRMED",
        version: confirmation.version,
        metricsStatus: confirmation.metricsStatus,
      });
    } catch (cause) {
      if (cause instanceof HttpError && cause.status === 409) {
        setStale(true);
        setError("预览已经变化，请刷新后重新确认");
      } else {
        setError(errorText(cause));
      }
    } finally {
      confirmationInFlight.current = false;
    }
  }

  async function cancelDraft() {
    if (!currentDetail) return;
    setError(null);
    try {
      await cancelMutation.mutateAsync({
        batchId: currentDetail.batchId,
        version: currentDetail.version,
        idempotencyKey: commandKey("tiantian-cancel"),
      });
      setDetail(null);
      setSelectedBatchId(null);
      setReviewed(false);
    } catch (cause) {
      setError(errorText(cause));
    }
  }

  async function retryMetrics() {
    const batchId =
      result?.batchId ??
      (currentDetail?.status === "CONFIRMED" ? currentDetail.batchId : null);
    if (!batchId) return;
    try {
      await retryMutation.mutateAsync({
        batchId,
        idempotencyKey: commandKey("tiantian-metrics-retry"),
      });
      await restored.refetch();
    } catch (cause) {
      setError(errorText(cause));
    }
  }

  function closeDialog() {
    setDetail(null);
    setResult(null);
    setSelectedBatchId(null);
    setError(null);
    setReviewed(false);
  }

  const displayedResult =
    result ??
    (currentDetail?.status === "CONFIRMED"
      ? {
          batchId: currentDetail.batchId,
          status: "CONFIRMED" as const,
          version: currentDetail.version,
          metricsStatus: currentDetail.metricsStatus ?? ("PENDING" as const),
          positionCount: Math.max(
            0,
            currentDetail.summary.total - currentDetail.summary.closed,
          ),
          historyImported: currentDetail.historySummary.importable,
          historyDuplicated: currentDetail.historySummary.duplicated,
        }
      : null);

  return (
    <div className="tiantian-import-workflow">
      {canWrite ? (
        <ImportPackageStep
          file={file}
          accountId={accountId}
          accountName={accountName}
          existingAccounts={existingAccounts}
          pending={previewMutation.isPending}
          error={!currentDetail ? error : null}
          onFile={(nextFile) => {
            setFile(nextFile);
            previewCommand.current = null;
          }}
          onAccountId={(nextAccountId) => {
            setAccountId(nextAccountId);
            previewCommand.current = null;
          }}
          onAccountName={(nextName) => {
            setAccountName(nextName);
            previewCommand.current = null;
          }}
          onPreview={() => void previewPackage()}
        />
      ) : (
        <section className="panel tiantian-readonly">
          <div className="panel-header">
            <h2>天天基金持仓导入</h2>
          </div>
          <div className="panel-body">
            <strong>当前角色只能查看持仓和导入历史</strong>
            <p className="muted">
              需要 OWNER、ADMIN 或 EDITOR 角色才能上传、确认或取消导入。
            </p>
          </div>
        </section>
      )}
      <section className="panel import-history tiantian-history">
        <div className="panel-header">
          <h2>
            <History size={16} />
            最近天天基金导入
          </h2>
        </div>
        <div className="panel-body">
          {history.isLoading ? (
            <p className="muted">正在读取导入历史…</p>
          ) : null}
          {history.error ? (
            <p className="form-error" role="alert">
              {errorText(history.error)}
            </p>
          ) : null}
          {selectedBatchId && restored.isLoading ? (
            <p className="muted">正在恢复所选批次…</p>
          ) : null}
          {selectedBatchId && restored.error ? (
            <p className="form-error" role="alert">
              恢复批次失败：{errorText(restored.error)}
            </p>
          ) : null}
          {history.data?.items.length === 0 ? (
            <p className="muted">暂无服务器导入批次。</p>
          ) : null}
          <div className="import-history-list">
            {history.data?.items.map((item) => (
              <button
                type="button"
                key={item.batchId}
                onClick={() => {
                  setSelectedBatchId(item.batchId);
                  setResult(null);
                  setDetail(null);
                  setReviewed(false);
                  setError(null);
                  setStale(false);
                  confirmCommand.current = null;
                }}
              >
                <strong>{item.account.name}</strong>
                <span>
                  {item.status} · {item.sourceAsOf.slice(0, 10)}
                </span>
              </button>
            ))}
          </div>
        </div>
      </section>
      <Dialog
        open={Boolean(currentDetail)}
        title="导入或更新天天基金持仓"
        size="xl"
        onClose={closeDialog}
      >
        {currentDetail &&
        !displayedResult &&
        currentDetail.status === "DRAFT" ? (
          <>
            <ImportPreviewStep
              detail={currentDetail}
              decisions={decisions}
              onDecision={(itemId, value) =>
                setDecisions((current) => ({ ...current, [itemId]: value }))
              }
            />
            <ImportConfirmStep
              detail={currentDetail}
              confirmEmpty={confirmEmpty}
              reviewed={reviewed}
              pending={confirmMutation.isPending}
              stale={stale}
              error={error}
              onConfirmEmpty={setConfirmEmpty}
              onReviewed={setReviewed}
              onConfirm={() => void confirmImport()}
              onCancel={() => void cancelDraft()}
              onRefresh={() => void restored.refetch()}
            />
          </>
        ) : null}
        {currentDetail && displayedResult ? (
          <ImportResultStep
            result={displayedResult}
            restored={restored.data}
            retrying={retryMutation.isPending}
            onRetry={() => void retryMetrics()}
          />
        ) : null}
      </Dialog>
    </div>
  );
}

function newestDetail(
  local: TiantianImportDetail | null,
  remote: TiantianImportDetail | null,
): TiantianImportDetail | null {
  if (!local) return remote;
  if (!remote || remote.batchId !== local.batchId) return local;
  if (remote.version !== local.version) {
    return remote.version > local.version ? remote : local;
  }
  // The Worker can update metric status without changing the command version.
  // Prefer the query result for a terminal batch so polling becomes visible.
  return remote.status === "CONFIRMED" ? remote : local;
}

function stableCommandKey(
  reference: { current: { signature: string; key: string } | null },
  signature: string,
  prefix: string,
) {
  if (reference.current?.signature === signature) return reference.current.key;
  const key = commandKey(prefix);
  reference.current = { signature, key };
  return key;
}
