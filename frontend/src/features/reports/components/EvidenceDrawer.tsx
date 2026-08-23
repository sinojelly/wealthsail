import { Clock3, ExternalLink, FileKey2, Link2, X } from "lucide-react";
import QualityBadge from "../../../components/data-state/QualityBadge";
import type { EvidenceRecord } from "../report-data";

export default function EvidenceDrawer({
  evidence,
  onClose,
}: {
  evidence: EvidenceRecord | null;
  onClose: () => void;
}) {
  if (!evidence) return null;
  return (
    <>
      <button
        className="drawer-scrim"
        aria-label="关闭证据链"
        onClick={onClose}
      />
      <aside
        className="evidence-drawer"
        role="complementary"
        aria-label="证据链溯源"
      >
        <header>
          <div>
            <span>证据链溯源</span>
            <strong className="mono">{evidence.id}</strong>
          </div>
          <button
            className="icon-button"
            aria-label="关闭证据链溯源"
            onClick={onClose}
          >
            <X size={17} />
          </button>
        </header>
        <div className="evidence-body">
          <div className="evidence-title-row">
            <div className="evidence-icon">
              <FileKey2 size={19} />
            </div>
            <div>
              <span>{evidence.type}</span>
              <h2>{evidence.title}</h2>
            </div>
            <QualityBadge quality={evidence.quality} />
          </div>
          <section className="evidence-content">
            <label>原始事实摘录</label>
            <p>{evidence.content}</p>
          </section>
          <dl className="evidence-metadata">
            <div>
              <dt>来源</dt>
              <dd>
                <Link2 size={12} />
                {evidence.source}
              </dd>
            </div>
            <div>
              <dt>事实截至</dt>
              <dd className="mono">
                <Clock3 size={12} />
                {evidence.asOf}
              </dd>
            </div>
            <div>
              <dt>源发布时间</dt>
              <dd className="mono">{evidence.publishedAt}</dd>
            </div>
            <div>
              <dt>采集时间</dt>
              <dd className="mono">{evidence.retrievedAt}</dd>
            </div>
            <div>
              <dt>采集方法</dt>
              <dd className="mono">{evidence.retrievalMethod}</dd>
            </div>
            <div>
              <dt>结构版本</dt>
              <dd className="mono">{evidence.schemaVersion}</dd>
            </div>
            {evidence.modelRunId ? (
              <div>
                <dt>模型运行</dt>
                <dd className="mono">{evidence.modelRunId}</dd>
              </div>
            ) : null}
          </dl>
          <section className="hash-block">
            <label>SHA-256 内容哈希</label>
            <code>{evidence.hash}</code>
            <p>用于验证当前展示内容与归档证据未被替换。</p>
          </section>
          <a
            className="btn-secondary full-width"
            href={evidence.sourceUrl}
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={13} />
            打开来源定位
          </a>
        </div>
      </aside>
    </>
  );
}
