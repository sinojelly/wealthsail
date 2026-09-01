import { useState } from "react";
import { BellRing } from "lucide-react";
import { useUpdateReportSubscriptions } from "../../../api/hooks";
import type {
  ReportSession,
  ReportSubscriptions,
} from "../../../api/types";

const SUBSCRIPTIONS: Array<{
  session: ReportSession;
  label: string;
  description: string;
}> = [
  {
    session: "PRE_MARKET",
    label: "盘前简报",
    description: "交易日 08:30，隔夜市场、今日事件与操作提醒",
  },
  {
    session: "INTRADAY",
    label: "盘中跟踪",
    description: "交易时段内的持仓变化与风险信号",
  },
  {
    session: "FUND_1430",
    label: "基金 2:30 报告",
    description: "交易日 14:30，基金估值、申赎状态与规则建议",
  },
  {
    session: "POST_MARKET",
    label: "盘后复盘",
    description: "交易日 15:30，组合表现、持仓健康度与行动建议",
  },
];

export default function ReportSubscriptionCard({
  subscriptions,
}: {
  subscriptions: ReportSubscriptions;
}) {
  const mutation = useUpdateReportSubscriptions();
  const [values, setValues] = useState(subscriptions);
  const [error, setError] = useState<string | null>(null);

  async function change(session: ReportSession, checked: boolean) {
    const previous = values;
    setError(null);
    setValues({ ...values, [session]: checked });
    try {
      const saved = await mutation.mutateAsync({ [session]: checked });
      setValues(saved);
    } catch (caught) {
      setValues(previous);
      setError(caught instanceof Error ? caught.message : "订阅保存失败");
    }
  }

  return (
    <section className="panel report-subscription-card">
      <div className="panel-header">
        <div>
          <h2>研报推送订阅</h2>
          <p>关闭后仍会生成报告并保留在研报审计中，只停止企微推送</p>
        </div>
        <BellRing size={17} />
      </div>
      <div className="report-subscription-list">
        {SUBSCRIPTIONS.map(({ session, label, description }) => (
          <label className="report-subscription-row" key={session}>
            <span>
              <strong>{label}</strong>
              <small>{description}</small>
            </span>
            <input
              type="checkbox"
              role="switch"
              aria-label={label}
              checked={values[session]}
              disabled={mutation.isPending}
              onChange={(event) =>
                void change(session, event.currentTarget.checked)
              }
            />
          </label>
        ))}
      </div>
      {error ? (
        <p className="report-subscription-error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
