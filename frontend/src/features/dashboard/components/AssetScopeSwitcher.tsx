import type { MeMember, PortfolioAssetScope } from "../../../api/types";

export default function AssetScopeSwitcher({
  scope,
  tenantName,
  members,
  onChange,
}: {
  scope: PortfolioAssetScope;
  tenantName: string;
  members: MeMember[];
  onChange: (next: PortfolioAssetScope) => void;
}) {
  return (
    <section className="asset-scope-bar panel" aria-label="资产查看范围">
      <div className="asset-scope-copy">
        <strong>查看范围</strong>
        <span>资产归属按投资账户计算，共同账户在租户总览只计一次。</span>
      </div>
      <div className="asset-scope-controls">
        <div className="asset-scope-buttons" role="group" aria-label="资产作用域">
          <button
            type="button"
            aria-pressed={scope.kind === "MINE"}
            onClick={() => onChange({ kind: "MINE" })}
          >
            我的资产
          </button>
          <button
            type="button"
            aria-label="租户总览"
            aria-pressed={scope.kind === "TENANT"}
            onClick={() => onChange({ kind: "TENANT" })}
          >
            租户总览
            {tenantName ? <small>{tenantName}</small> : null}
          </button>
        </div>
        <label className="asset-scope-member">
          <span>成员资产</span>
          <select
            aria-label="成员资产"
            value={scope.kind === "MEMBER" ? scope.memberId : ""}
            onChange={(event) => {
              const memberId = event.target.value;
              if (memberId) onChange({ kind: "MEMBER", memberId });
            }}
          >
            <option value="">选择成员</option>
            {members.map((member) => (
              <option key={member.userId} value={member.userId}>
                {member.nickname} · {member.role}
              </option>
            ))}
          </select>
        </label>
      </div>
    </section>
  );
}
