import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CalendarClock,
  ExternalLink,
  Info,
  MessageSquareText,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import QualityBadge from "../../../components/data-state/QualityBadge";
import DecisionAuditTable from "./DecisionAuditTable";
import type { EvidenceRecord, ReportRecord } from "../report-data";
import { evidenceRecords } from "../report-data";

function EvidenceButton({
  id,
  onEvidence,
}: {
  id: string;
  onEvidence: (evidence: EvidenceRecord) => void;
}) {
  return (
    <button
      className="evidence-link"
      aria-label={`查看证据 ${id}`}
      onClick={() => onEvidence(evidenceRecords[id])}
    >
      <ExternalLink size={11} />
      {id}
    </button>
  );
}

function PremarketReport({
  onEvidence,
}: {
  onEvidence: (evidence: EvidenceRecord) => void;
}) {
  return (
    <>
      <section className="report-section">
        <div className="section-title">
          <div>
            <span className="section-number">01</span>
            <div>
              <h2>隔夜市场与今日环境</h2>
              <p>先列事实，再给解释与待验证推断。</p>
            </div>
          </div>
        </div>
        <div className="insight-grid">
          <article className="insight-card">
            <div>
              <span className="logic-tag fact">事实</span>
              <QualityBadge quality="FRESH" />
            </div>
            <h3>海外风险偏好温和回升</h3>
            <p>标普500 +0.42%，纳斯达克 +0.61%，半导体板块相对活跃。</p>
            <EvidenceButton id="E-PRE-001" onEvidence={onEvidence} />
          </article>
          <article className="insight-card">
            <div>
              <span className="logic-tag explain">解释</span>
            </div>
            <h3>成长风格或有情绪映射</h3>
            <p>
              隔夜科技资产回暖可能改善 A
              股成长方向开盘情绪，但不能直接外推为全天趋势。
            </p>
            <span className="confidence">置信度：中</span>
          </article>
          <article className="insight-card">
            <div>
              <span className="logic-tag infer">推断</span>
            </div>
            <h3>关注高波动持仓开盘缺口</h3>
            <p>
              科大讯飞需要验证量能与 43.50
              元观察线，不因单一海外信号追涨或补仓。
            </p>
            <span className="confidence">待 09:45 验证</span>
          </article>
        </div>
      </section>
      <section className="report-section">
        <div className="section-title">
          <div>
            <span className="section-number">02</span>
            <div>
              <h2>今日关键事件</h2>
              <p>只展示可能影响持仓与风险预算的事件。</p>
            </div>
          </div>
        </div>
        <div className="event-list">
          <div>
            <CalendarClock size={16} />
            <span className="mono">09:30</span>
            <p>重点行业数据披露，关注消费与金融板块反馈。</p>
            <EvidenceButton id="E-PRE-002" onEvidence={onEvidence} />
          </div>
          <div>
            <CalendarClock size={16} />
            <span className="mono">20:30</span>
            <p>海外通胀分项披露，影响次日风险偏好评估。</p>
            <EvidenceButton id="E-PRE-002" onEvidence={onEvidence} />
          </div>
        </div>
      </section>
      <section className="report-section">
        <div className="section-title">
          <div>
            <span className="section-number">03</span>
            <div>
              <h2>持仓观察计划</h2>
              <p>计划是条件与动作约束，不是收益承诺。</p>
            </div>
          </div>
        </div>
        <div className="watch-grid">
          <div>
            <strong>
              招商银行 <small>600036</small>
            </strong>
            <p>观察银行板块是否放量站稳；未满足条件时不主动增仓。</p>
            <span className="status-pill safe">风险预算内</span>
          </div>
          <div>
            <strong>
              科大讯飞 <small>002230</small>
            </strong>
            <p>43.50 为风险观察线；跌破后只触发复核，不自动交易。</p>
            <span className="status-pill warning">接近观察线</span>
          </div>
        </div>
      </section>
    </>
  );
}

function IntradayReport({
  onEvidence,
}: {
  onEvidence: (evidence: EvidenceRecord) => void;
}) {
  return (
    <>
      <div className="alert alert-danger">
        <ShieldAlert size={16} />
        <div>
          <strong>盘中触点不是交易指令</strong>
          <p>系统只做异常解释、风险预算核对与交互留痕，不连接券商自动下单。</p>
        </div>
      </div>
      <section className="intraday-hero">
        <div className="pulse-icon">
          <ArrowDown size={22} />
        </div>
        <div>
          <span>11:25 风险触点</span>
          <h2>科大讯飞跌破 43.50 元观察线</h2>
          <p>当前 43.28 元，日内 -3.21%，成交量较 20 日同期均值放大 1.7 倍。</p>
          <EvidenceButton id="E-INTRA-001" onEvidence={onEvidence} />
        </div>
        <div className="intraday-price">
          <strong className="mono negative">43.28</strong>
          <span className="mono negative">-3.21%</span>
        </div>
      </section>
      <section className="report-section">
        <div className="section-title">
          <div>
            <span className="section-number">01</span>
            <div>
              <h2>风险解释</h2>
              <p>事实、规则命中与行动边界分开呈现。</p>
            </div>
          </div>
        </div>
        <div className="reasoning-flow">
          <div>
            <span className="logic-tag fact">事实</span>
            <p>价格已跌破观察线且量能放大。</p>
          </div>
          <ArrowDown size={15} />
          <div>
            <span className="logic-tag explain">规则命中</span>
            <p>单标的浮亏和波动上升，但当前仓位仍低于 30% 上限。</p>
          </div>
          <ArrowDown size={15} />
          <div>
            <span className="logic-tag infer">建议</span>
            <p>等待用户回复“继续观察 / 创建减仓草稿 / 忽略本次触点”。</p>
          </div>
        </div>
      </section>
      <section className="interaction-box">
        <MessageSquareText size={18} />
        <div>
          <strong>企业微信交互状态</strong>
          <p>
            消息已送达，当前为 NO_RESPONSE；不会把“未回复”解释成“无操作决策”。
          </p>
        </div>
        <span className="status-pill pending">等待回复</span>
      </section>
    </>
  );
}

function FundReport({
  onEvidence,
}: {
  onEvidence: (evidence: EvidenceRecord) => void;
}) {
  return (
    <>
      <div className="alert alert-warning">
        <AlertTriangle size={16} />
        <div>
          <strong>估值不等于最终净值</strong>
          <p>
            结果用于盘中风险观察。披露滞后、持仓变化和停牌资产都可能造成偏差。
          </p>
        </div>
      </div>
      <section className="fund-summary-grid">
        <div>
          <span>覆盖基金</span>
          <strong className="mono">2 / 2</strong>
          <small>本账户场外基金</small>
        </div>
        <div>
          <span>加权持仓覆盖率</span>
          <strong className="mono">68.9%</strong>
          <small>按最近披露持仓</small>
        </div>
        <div>
          <span>预估当日贡献</span>
          <strong className="mono positive">+¥1,284</strong>
          <small>不可作为最终收益</small>
        </div>
        <div>
          <span>截止规则</span>
          <strong className="mono">15:00</strong>
          <small>已按产品规则确认</small>
        </div>
      </section>
      <section className="report-section">
        <div className="section-title">
          <div>
            <span className="section-number">01</span>
            <div>
              <h2>基金估值明细</h2>
              <p>披露日期、覆盖率和估值质量必须同时可见。</p>
            </div>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>基金</th>
                <th>最近披露</th>
                <th>持仓覆盖</th>
                <th className="numeric">估算净值</th>
                <th className="numeric">估算涨跌</th>
                <th>质量</th>
                <th>证据</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <strong>易方达消费行业</strong>
                  <small className="mono">110022</small>
                </td>
                <td className="mono">2026-07-21</td>
                <td className="mono">72.4%</td>
                <td className="mono numeric">1.7326</td>
                <td className="mono numeric positive">+0.48%</td>
                <td>
                  <QualityBadge quality="ESTIMATED" />
                </td>
                <td>
                  <EvidenceButton id="E-FUND-001" onEvidence={onEvidence} />
                </td>
              </tr>
              <tr>
                <td>
                  <strong>招商中证白酒</strong>
                  <small className="mono">161725</small>
                </td>
                <td className="mono">2026-07-19</td>
                <td className="mono">64.1%</td>
                <td className="mono numeric">1.3024</td>
                <td className="mono numeric negative">-0.31%</td>
                <td>
                  <QualityBadge quality="ESTIMATED" />
                </td>
                <td>
                  <span className="muted">E-FUND-002</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section className="method-note">
        <Info size={15} />
        <div>
          <strong>方法说明</strong>
          <p>
            以最近公开披露持仓为基础，叠加可获取的成分行情估算；未覆盖部分按基金风格基准近似。最终净值披露后将产生新版本并记录估值误差。
          </p>
        </div>
      </section>
    </>
  );
}

function PostmarketReport({
  onEvidence,
}: {
  onEvidence: (evidence: EvidenceRecord) => void;
}) {
  return (
    <>
      <section className="post-summary">
        <div className="post-score">
          <span>今日计划一致性</span>
          <strong className="mono">82</strong>
          <small>/ 100</small>
        </div>
        <div className="post-stat">
          <ArrowUp size={16} />
          <div>
            <strong className="positive">+¥12,384</strong>
            <span>当日收益</span>
          </div>
        </div>
        <div className="post-stat">
          <Sparkles size={16} />
          <div>
            <strong>1 项待复核</strong>
            <span>成交草稿与盘前计划有偏差</span>
          </div>
        </div>
      </section>
      <section className="report-section">
        <div className="section-title">
          <div>
            <span className="section-number">01</span>
            <div>
              <h2>市场与组合回顾</h2>
              <p>以收盘事实解释组合表现，不把收益简单归因于单一观点。</p>
            </div>
          </div>
        </div>
        <div className="narrative">
          <p>
            <span className="logic-tag fact">事实</span> 沪深300收涨
            0.39%，组合收涨
            0.65%。招商银行贡献最大；两只场外基金仍为盘中估值口径。
          </p>
          <p>
            <span className="logic-tag explain">解释</span>{" "}
            金融和消费持仓形成正贡献，科大讯飞的下跌抵消了部分收益。
          </p>
          <p>
            <span className="logic-tag infer">推断</span>{" "}
            次日需优先核对科大讯飞成交草稿与风险预算，暂不根据单日超额收益提高仓位。
          </p>
        </div>
      </section>
      <DecisionAuditTable
        onEvidence={(record) => onEvidence(evidenceRecords[record.id])}
      />
      <section className="report-section">
        <div className="section-title">
          <div>
            <span className="section-number">04</span>
            <div>
              <h2>明日待办</h2>
              <p>把复盘结论变成可检查的下一步。</p>
            </div>
          </div>
        </div>
        <ul className="todo-list">
          <li>
            <span>1</span>
            <div>
              <strong>确认科大讯飞买入草稿是否为真实成交</strong>
              <p>若不是，作废草稿；若是，确认后复核单标的风险预算。</p>
            </div>
          </li>
          <li>
            <span>2</span>
            <div>
              <strong>最终净值披露后核对基金估值误差</strong>
              <p>误差超过阈值时标记估值方法待调整。</p>
            </div>
          </li>
        </ul>
      </section>
    </>
  );
}

export default function ReportViewer({
  report,
  onEvidence,
}: {
  report: ReportRecord;
  onEvidence: (evidence: EvidenceRecord) => void;
}) {
  return (
    <article className="report-viewer">
      <header className="report-viewer-header">
        <div>
          <span className="report-kicker">
            2026-08-23 · {report.generatedAt} 生成
          </span>
          <h1>{report.title}</h1>
          <p>{report.subtitle}</p>
        </div>
        <div className="report-version">
          <span className="status-pill published">已发布</span>
          <small className="mono">v1 · {report.id}</small>
        </div>
      </header>
      <div className="report-document">
        {report.session === "PRE_MARKET" ? (
          <PremarketReport onEvidence={onEvidence} />
        ) : null}
        {report.session === "INTRADAY" ? (
          <IntradayReport onEvidence={onEvidence} />
        ) : null}
        {report.session === "FUND_1430" ? (
          <FundReport onEvidence={onEvidence} />
        ) : null}
        {report.session === "POST_MARKET" ? (
          <PostmarketReport onEvidence={onEvidence} />
        ) : null}
      </div>
      <footer className="report-disclaimer">
        本报告为个人投研辅助材料，不构成投资建议或自动交易指令。请结合风险政策与真实成交事实独立决策。
      </footer>
    </article>
  );
}
