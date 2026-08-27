import { useState, type DragEvent } from "react";
import { FileArchive, Upload } from "lucide-react";

const MAX_ZIP_BYTES = 20 * 1024 * 1024;

interface ImportAccountOption {
  accountId: string;
  name: string;
}

interface Props {
  file: File | null;
  accountId: string;
  accountName: string;
  existingAccounts: ImportAccountOption[];
  pending: boolean;
  error: string | null;
  onFile: (file: File | null) => void;
  onAccountId: (accountId: string) => void;
  onAccountName: (name: string) => void;
  onPreview: () => void;
}

function validatePackage(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".zip")) return "请选择 .zip 导入包";
  if (file.size > MAX_ZIP_BYTES) return "ZIP 导入包不能超过 20 MB";
  return null;
}

export default function ImportPackageStep({
  file,
  accountId,
  accountName,
  existingAccounts,
  pending,
  error,
  onFile,
  onAccountId,
  onAccountName,
  onPreview,
}: Props) {
  const [fileError, setFileError] = useState<string | null>(null);

  function selectFile(nextFile: File | null) {
    if (!nextFile) {
      setFileError(null);
      onFile(null);
      return;
    }
    const validationError = validatePackage(nextFile);
    setFileError(validationError);
    onFile(validationError ? null : nextFile);
  }

  function dropFile(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    selectFile(
      event.dataTransfer.files.item?.(0) ?? event.dataTransfer.files[0] ?? null,
    );
  }

  const hasTarget = Boolean(accountId || accountName.trim());
  return (
    <section
      className="panel tiantian-package-step"
      aria-labelledby="tiantian-import-title"
    >
      <div className="panel-header">
        <div>
          <h2 id="tiantian-import-title">导入或更新天天基金持仓</h2>
          <p>
            上传本地采集器生成的单个 ZIP；先预览差异，确认后才写入持仓基线。
          </p>
        </div>
        <FileArchive size={20} aria-hidden="true" />
      </div>
      <div className="panel-body tiantian-package-form">
        <div
          className="tiantian-drop-zone"
          data-testid="tiantian-drop-zone"
          onDragOver={(event) => event.preventDefault()}
          onDrop={dropFile}
        >
          <label className="field-stack">
            <span>天天基金 ZIP 导入包</span>
            <input
              aria-label="天天基金 ZIP 导入包"
              accept=".zip,application/zip"
              type="file"
              onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
            />
            <small>
              {file
                ? `已选择：${file.name}（${Math.max(1, Math.ceil(file.size / 1024))} KB）`
                : "可点击选择或拖入采集器生成的 v2 ZIP，最大 20 MB。"}
            </small>
          </label>
        </div>
        <div className="target-account-fields">
          <label className="field-stack">
            <span>目标 WealthSail 账户</span>
            <select
              aria-label="目标 WealthSail 账户"
              className="select"
              value={accountId}
              onChange={(event) => onAccountId(event.target.value)}
            >
              <option value="">创建并绑定新账户</option>
              {existingAccounts.map((account) => (
                <option value={account.accountId} key={account.accountId}>
                  {account.name}
                </option>
              ))}
            </select>
          </label>
          {!accountId ? (
            <label className="field-stack">
              <span>新账户名称</span>
              <input
                aria-label="WealthSail 账户名称"
                value={accountName}
                maxLength={120}
                onChange={(event) => onAccountName(event.target.value)}
              />
              <small>
                同一天天基金账号后续导入会自动沿用已经建立的来源绑定。
              </small>
            </label>
          ) : (
            <small className="muted">
              将更新所选账户；服务端仍会校验天天基金来源绑定。
            </small>
          )}
        </div>
        {fileError || error ? (
          <p className="form-error" role="alert">
            {fileError ?? error}
          </p>
        ) : null}
        {pending ? (
          <p className="import-progress" role="status">
            正在上传并安全解析导入包，请勿重复提交…
          </p>
        ) : null}
        <details className="collector-help">
          <summary>还没有 ZIP？查看本地采集步骤</summary>
          <p>
            在项目的 <code>tools/tiantian-fund-importer</code>{" "}
            目录运行采集器；浏览器登录只保存在本机， 生成的 ZIP
            和真实资产数据不要提交到 Git。
          </p>
        </details>
        <div className="form-actions">
          <button
            className="btn-primary"
            type="button"
            disabled={!file || !hasTarget || pending}
            onClick={onPreview}
          >
            <Upload size={14} />
            {pending ? "正在安全解析…" : "解析并预览"}
          </button>
        </div>
      </div>
    </section>
  );
}
