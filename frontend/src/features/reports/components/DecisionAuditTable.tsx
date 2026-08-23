import { ExternalLink } from "lucide-react";
import type { EvidenceRecord } from "../report-data";

export default function DecisionAuditTable({
  onEvidence,
}: {
  onEvidence: (evidence: EvidenceRecord) => void;
}) {
  const evidence = {
    id: "E-POST-001",
  } as EvidenceRecord;
  return (
    <section className="report-section">
      <div className="section-title">
        <div>
          <span className="section-number">03</span>
          <div>
            <h2>决策审计</h2>
            <p>把计划、用户决策和最终执行并列，避免事后叙事替代事实。</p>
          </div>
        </div>
      </div>
      <div className="table-wrap report-table-wrap">
        <table className="data-table decision-table">
          <thead>
            <tr>
              <th>标的</th>
              <th>盘前计划</th>
              <th>用户决策</th>
              <th>最终执行</th>
              <th>偏差/说明</th>
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <strong>招商银行</strong>
                <small className="mono">600036</small>
              </td>
              <td>板块放量站稳观察位则保留仓位</td>
              <td>
                <span className="decision-chip">继续持有</span>
                <small>10:12 已响应</small>
              </td>
              <td>
                <strong>无成交</strong>
                <small>符合计划</small>
              </td>
              <td className="positive">无偏差</td>
              <td>
                <button
                  className="evidence-link"
                  aria-label="查看证据 E-POST-001"
                  onClick={() => onEvidence(evidence)}
                >
                  <ExternalLink size={12} />
                  E-POST-001
                </button>
              </td>
            </tr>
            <tr>
              <td>
                <strong>科大讯飞</strong>
                <small className="mono">002230</small>
              </td>
              <td>跌破 43.50 后进入观察，不追跌操作</td>
              <td>
                <span className="decision-chip muted-chip">NO_RESPONSE</span>
                <small>触点未回复</small>
              </td>
              <td>
                <strong>买入草稿待确认</strong>
                <small>来源：企业微信</small>
              </td>
              <td className="negative">需人工复核</td>
              <td>
                <span className="muted">成交证据待确认</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
