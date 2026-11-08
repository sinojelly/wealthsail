import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { privateQueryKey, useAuthSession } from "../../../app/auth-session";
import { getPersonalFundsReference } from "../../../api/personal-funds";
import FundReferenceDetails from "../../fund-screener/components/FundReferenceDetails";
import { fundDisplayName } from "../../fund-screener/plan-display";
import { timeLabel } from "../../fund-screener/macro-display";

export default function CurrentPersonalFundsReference() {
  const session = useAuthSession();
  const [enabled, setEnabled] = useState(false);
  const query = useQuery({
    queryKey: privateQueryKey(
      session,
      "personal-funds-reference",
      "historical-readonly",
    ),
    queryFn: ({ signal }) => getPersonalFundsReference(false, signal),
    enabled,
    retry: false,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const data = query.data;
  const valid =
    data?.source === "LOCAL_DATABASE_ONLY" &&
    data.readOnly &&
    data.version === "personal-funds-reference-v1";
  return (
    <section
      className="research-preview-current-reference"
      aria-label="当前持仓与跟踪实时参考"
    >
      <h2>当前持仓与跟踪实时参考</h2>
      <label>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => setEnabled(event.target.checked)}
        />
        显示当前持仓、跟踪与自选的本地只读参考
      </label>
      <p>
        与原研究历史材料分别展示。开启后仅读取当前本地数据库已有资料；不会采集行情、调用
        AI、保存跟踪或设置提醒。
      </p>
      {enabled && (
        <>
          {query.isPending && <p role="status">正在读取当前本地基金资料…</p>}
          {query.isError && (
            <p role="alert">
              当前基金参考读取失败：
              {query.error instanceof Error ? query.error.message : "请重试"}
            </p>
          )}
          {valid && (
            <>
              <strong>
                当前参考读取时间：{timeLabel(data.asOf)}；与原研究时点分别展示。
              </strong>
              {data.funds.map((fund) => (
                <article
                  className="op-value-fund"
                  key={fund.key}
                  aria-label={`当前基金 ${fund.fundCode ?? fund.label}`}
                >
                  <h3>{fundDisplayName(fund.label, fund.fundCode)}</h3>
                  <FundReferenceDetails fund={fund} historical />
                </article>
              ))}
              {!data.funds.length && (
                <p>当前账户没有可读取的持仓、活动跟踪或基金自选。</p>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
