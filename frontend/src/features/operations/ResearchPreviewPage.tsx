import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { getResearchPreview } from "../../api/research-preview";
import {
  canViewOperations,
  privateQueryKey,
  useAuthSession,
} from "../../app/auth-session";
import MacroContextStrip from "../fund-screener/components/MacroContextStrip";
import ValueDirections from "../fund-screener/components/ValueDirections";
import { timeLabel } from "../fund-screener/macro-display";
import PreviewFunds from "./components/PreviewFunds";
import { frozenNavHistory, previewMissingReason } from "./preview-data";
import PreviewTopics from "./components/PreviewTopics";
import "../fund-screener/opportunities.css";
import "./research-preview.css";

export default function ResearchPreviewPage() {
  const { runId = "" } = useParams();
  const session = useAuthSession();
  const preview = useQuery({
    queryKey: privateQueryKey(session, "research-preview", runId),
    queryFn: ({ signal }) => getResearchPreview(runId, signal),
    enabled: !!runId && canViewOperations(session),
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const value = preview.data;
  const valid =
    value?.runId === runId &&
    value.version === "research-preview-v1" &&
    value.mode === "HISTORICAL_SIMULATION" &&
    value.readOnly === true &&
    value.investmentEligible === false &&
    Number.isFinite(Date.parse(value.validatedAt));
  const daily = value?.opportunityContext.dailyResearch;
  const context = value
    ? {
        ...value.opportunityContext,
        portfolio: null,
        sellRiskAlerts: undefined,
        dailyResearch: daily ? { ...daily, report: value.report } : null,
      }
    : undefined;
  const navHistories = Object.fromEntries(
    (valid ? value.funds : []).flatMap((fund) =>
      fund.fundCode
        ? [[fund.fundCode, frozenNavHistory(fund, value!.validatedAt)]]
        : [],
    ),
  );
  return (
    <main className="opportunities research-preview-page">
      <aside className="research-preview-sticky" role="note">
        <strong>历史模拟 · 非实时投资建议</strong>
        {valid && <span>原验证时间：{timeLabel(value.validatedAt)}</span>}
      </aside>
      <header className="research-preview-banner">
        <h1>历史模拟 · 非实时投资建议</h1>
        <p>
          仅回放原研究时点的冻结证据。基金净值、买卖区间和风险结论均属于历史记录，当前行情与持仓未读取。
        </p>
        {valid && (
          <dl>
            <div>
              <dt>原验证时间</dt>
              <dd>{timeLabel(value.validatedAt)}（北京时间）</dd>
            </div>
            <div>
              <dt>规则版本</dt>
              <dd>{value.validatorVersion}</dd>
            </div>
            <div>
              <dt>原研究ID</dt>
              <dd>{value.runId}</dd>
            </div>
          </dl>
        )}
        <div className="research-preview-actions">
          {["手动启动研究", "跟踪研究", "设置提醒", "发布报告"].map((label) => (
            <button
              type="button"
              key={label}
              disabled
              title="历史模拟仅供核验，操作已禁用"
            >
              {label}
            </button>
          ))}
        </div>
        <small>
          操作已禁用：此预览不会调用 AI、保存跟踪、设置提醒或发布报告。
        </small>
      </header>
      {!canViewOperations(session) && (
        <p role="alert">需要运维查看权限才能读取历史研究预览。</p>
      )}
      {preview.isPending && <p role="status">正在读取冻结研究材料…</p>}
      {preview.isError && (
        <p role="alert">
          历史研究预览读取失败：
          {preview.error instanceof Error
            ? preview.error.message
            : "请稍后重试"}
        </p>
      )}
      {value && !valid && <p role="alert">历史研究身份不一致，已停止展示。</p>}
      {value && valid && (
        <>
          {!!value.missingReasons.length && (
            <aside aria-label="历史材料缺口">
              {value.missingReasons.map((reason) => (
                <p key={reason}>
                  {previewMissingReason(reason)}（{reason}）
                </p>
              ))}
            </aside>
          )}
          <MacroContextStrip
            data={context}
            error={false}
            renderMyFunds={() => (
              <>
                <p className="research-preview-muted">
                  本页展示原研究提及基金，冻结材料未保存个人持仓；不读取当前持仓或跟踪记录。
                </p>
                <PreviewFunds
                  funds={value.funds}
                  validatedAt={value.validatedAt}
                  directions={value.report?.valueDirections}
                  sources={value.report?.sources}
                />
              </>
            )}
            historicalPreview={{
              validatedAt: value.validatedAt,
              navHistories,
              recommendations: (
                <>
                  <ValueDirections
                    data={context?.dailyResearch}
                    frozenNavHistories={navHistories}
                  />
                  <p>
                    原始研究共提及 {value.funds.length}{" "}
                    只基金，包括正式计划被剥离的基金；可在“我的基金”查看完整历史价格与图表。
                  </p>
                </>
              ),
              topics: <PreviewTopics preview={value} />,
            }}
          />
        </>
      )}
    </main>
  );
}
