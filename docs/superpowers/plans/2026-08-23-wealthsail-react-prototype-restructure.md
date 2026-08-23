# WealthSail React Prototype Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the broken/mojibake Figma React prototype into a tested L1 MVP demonstration for three asset types, four report sessions, three isolated demo users, and WeCom-aware interaction.

**Architecture:** Keep the existing Vite/React/Tailwind visual shell, but split domain types, pure report/tenant selectors, mock fixtures, layout, report cards, and portfolio views. The browser holds only mock tenant snapshots; production identity, ledger, source, and WeCom boundaries remain documented backend contracts.

**Tech Stack:** React 19, TypeScript 5.7, Vite 8, Tailwind CSS 4, Vitest, jsdom, React Testing Library, user-event, oxfmt.

**Spec:** `docs/superpowers/specs/2026-08-23-wealthsail-docs-and-prototype-restructure-design.md`

## Global Constraints

- Preserve the current dark navy/cyan/emerald visual language and responsive desktop/mobile layout.
- Render correct UTF-8 Chinese; no mojibake, broken literals, emoji-only controls without accessible labels, or hidden tenant context.
- Demo assets include at least one A-share stock, one exchange-traded ETF/LOF, and one off-exchange public fund.
- Report sessions are `PRE_MARKET`, `INTRADAY`, `FUND_1430`, and `POST_MARKET`.
- Three demo tenants have disjoint account, report, decision, and watchlist data.
- Every market/fund observation shows source, `asOf`, and status; estimated fund values are explicitly labeled.
- The prototype never stores or renders a WeCom secret and never claims to perform a real network call or trade.
- New behavior follows test-first red–green–refactor; all tests, TypeScript checks, format checks, and production build must pass before completion.

---

### Task 1: Establish the React test and quality harness

**Files:**

- Modify: `figma/v1-mobile/package.json`
- Modify: `figma/v1-mobile/pnpm-lock.yaml` through `pnpm add`
- Create: `figma/v1-mobile/vitest.config.ts`
- Create: `figma/v1-mobile/src/test/setup.ts`
- Create: `figma/v1-mobile/src/domain/reporting.test.ts`

**Interfaces:**

- Consumes: existing Vite React configuration.
- Produces: `pnpm test`, `pnpm test:run`, `pnpm typecheck`, and `pnpm format:check` commands for all later tasks.

- [ ] **Step 1: Add test scripts and dependencies**

Run from `figma/v1-mobile`:

```powershell
pnpm add -D vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

Add scripts:

```json
{
  "test": "vitest",
  "test:run": "vitest run",
  "typecheck": "tsc --noEmit",
  "format:check": "oxfmt --check ."
}
```

- [ ] **Step 2: Configure jsdom and test setup**

Create `vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
  },
});
```

Create `src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 3: Write the first failing domain test**

Create `src/domain/reporting.test.ts` importing the not-yet-created `getReportForSession`:

```ts
import { describe, expect, it } from "vitest";
import { getReportForSession } from "./reporting";

describe("getReportForSession", () => {
  it("returns only the requested tenant and session report", () => {
    expect(getReportForSession([], "tenant-jelly", "PRE_MARKET")).toBeNull();
  });
});
```

- [ ] **Step 4: Run the test and verify RED**

Run:

```powershell
pnpm test:run -- src/domain/reporting.test.ts
```

Expected: FAIL because `./reporting` does not exist.

- [ ] **Step 5: Commit the harness and red test**

```powershell
git add -- package.json pnpm-lock.yaml vitest.config.ts src/test/setup.ts src/domain/reporting.test.ts
git commit -m "test(ui): establish the React MVP test harness"
```

### Task 2: Define stable prototype domain types and selectors

**Files:**

- Create: `figma/v1-mobile/src/domain/types.ts`
- Create: `figma/v1-mobile/src/domain/reporting.ts`
- Modify: `figma/v1-mobile/src/domain/reporting.test.ts`

**Interfaces:**

- Consumes: no UI components.
- Produces: `TenantSnapshot`, `Instrument`, `Position`, `Report`, `Evidence`, `getTenantSnapshot()`, `getReportForSession()`, and `formatDataStatus()`.

- [ ] **Step 1: Extend the failing tests for tenant isolation and status wording**

Use fixtures inline and assert:

```ts
expect(getTenantSnapshot(snapshots, "tenant-chen")?.tenant.id).toBe("tenant-chen");
expect(getTenantSnapshot(snapshots, "missing")).toBeNull();
expect(getReportForSession(reports, "tenant-jelly", "FUND_1430")?.tenantId).toBe("tenant-jelly");
expect(formatDataStatus("ESTIMATED")).toBe("估算");
expect(formatDataStatus("UNAVAILABLE")).toBe("不可用");
```

- [ ] **Step 2: Verify the expanded test remains RED**

Run `pnpm test:run -- src/domain/reporting.test.ts`.

Expected: FAIL for missing exports.

- [ ] **Step 3: Implement the domain types**

Define exact unions:

```ts
export type AssetType = "CN_STOCK" | "EXCHANGE_FUND" | "OFF_EXCHANGE_FUND";
export type ReportSession = "PRE_MARKET" | "INTRADAY" | "FUND_1430" | "POST_MARKET";
export type DataStatus = "FRESH" | "ESTIMATED" | "STALE" | "CONFLICT" | "UNAVAILABLE";
export type DecisionStatus = "DRAFT" | "CONFIRMED" | "CORRECTED" | "NOT_EXECUTED";
```

Add typed interfaces with stable IDs and comments explaining that UI IDs mirror production contracts but the values are mock-only. `Report` includes `tenantId`, `session`, `title`, `summary`, `asOf`, `dataStatus`, `evidence`, `items`, and optional `decisionStatus`. `TenantSnapshot` owns tenant, accounts, instruments, positions, reports, watchlist, and WeCom binding state.

- [ ] **Step 4: Implement pure selectors**

Use strict tenant/session matches and return `null` instead of falling back to the first tenant or another session. `formatDataStatus` maps all five statuses to Chinese labels via an exhaustive `Record<DataStatus, string>`.

- [ ] **Step 5: Run domain tests and verify GREEN**

Run `pnpm test:run -- src/domain/reporting.test.ts`.

Expected: PASS.

- [ ] **Step 6: Commit domain contracts**

```powershell
git add -- src/domain/types.ts src/domain/reporting.ts src/domain/reporting.test.ts
git commit -m "feat(ui): add tenant-safe report domain contracts"
```

### Task 3: Replace corrupted mock data with three isolated tenant snapshots

**Files:**

- Replace: `figma/v1-mobile/src/data/mockData.ts`
- Create: `figma/v1-mobile/src/data/mockData.test.ts`

**Interfaces:**

- Consumes: domain types from Task 2.
- Produces: `tenantSnapshots`, `defaultTenantId`, and helper-free UTF-8 fixtures used by the app.

- [ ] **Step 1: Write failing fixture integrity tests**

Assert:

```ts
expect(tenantSnapshots).toHaveLength(3);
expect(new Set(tenantSnapshots.map(({ tenant }) => tenant.id)).size).toBe(3);
expect(new Set(tenantSnapshots.flatMap(({ accounts }) => accounts.map(({ id }) => id))).size)
  .toBe(tenantSnapshots.flatMap(({ accounts }) => accounts).length);
expect(new Set(tenantSnapshots[0].positions.map(({ instrumentId }) => instrumentId)))
  .not.toEqual(new Set(tenantSnapshots[1].positions.map(({ instrumentId }) => instrumentId)));
expect(new Set(tenantSnapshots[0].positions.map(({ assetType }) => assetType)))
  .toEqual(new Set(["CN_STOCK", "EXCHANGE_FUND", "OFF_EXCHANGE_FUND"]));
expect(tenantSnapshots.every(({ reports }) => reports.length === 4)).toBe(true);
expect(tenantSnapshots.flatMap(({ reports }) => reports)
  .filter(({ session }) => session === "FUND_1430")
  .every(({ dataStatus }) => dataStatus === "ESTIMATED")).toBe(true);
```

- [ ] **Step 2: Verify RED against the legacy data file**

Run `pnpm test:run -- src/data/mockData.test.ts`.

Expected: FAIL because `tenantSnapshots` and the new asset model do not exist.

- [ ] **Step 3: Implement three disjoint demo tenants**

Create three named demo users with non-sensitive fictional identifiers. For every tenant include three asset types, four reports, a WeCom binding state, evidence with source/as-of/status, and at least one stale or unavailable item to demonstrate fail-closed messaging. Use realistic but clearly labeled mock values; include a visible `isMock: true` marker in tenant metadata.

- [ ] **Step 4: Add authoritative-media examples as cited opinions**

Add mock evidence entries for “候选来源：兔主席”和“候选来源：苏涵” only as source-governance demonstrations. Mark them `sourceKind: "COMMENTARY"`, use example URLs rather than copied content, distinguish source opinion from verified fact, and never turn the opinion into an order instruction.

- [ ] **Step 5: Run fixture tests and verify GREEN**

Run `pnpm test:run -- src/data/mockData.test.ts`.

Expected: PASS.

- [ ] **Step 6: Commit fixtures**

```powershell
git add -- src/data/mockData.ts src/data/mockData.test.ts
git commit -m "feat(ui): model three isolated MVP demo tenants"
```

### Task 4: Build the accessible application shell and tenant switcher

**Files:**

- Create: `figma/v1-mobile/src/components/layout/AppHeader.tsx`
- Replace: `figma/v1-mobile/src/components/Sidebar.tsx`
- Replace: `figma/v1-mobile/src/App.tsx`
- Create: `figma/v1-mobile/src/App.test.tsx`

**Interfaces:**

- Consumes: `TenantSnapshot`, `ReportSession`, `tenantSnapshots`, and `defaultTenantId`.
- Produces: top-level tenant/session/view state passed to report and portfolio components.

- [ ] **Step 1: Write failing App shell tests**

Render `<App />` and assert:

```ts
expect(screen.getByRole("heading", { name: "盘前简报" })).toBeInTheDocument();
expect(screen.getByRole("combobox", { name: "当前用户" })).toHaveValue("tenant-jelly");
await user.selectOptions(screen.getByRole("combobox", { name: "当前用户" }), "tenant-chen");
expect(screen.getByText("陈女士的组合")).toBeInTheDocument();
expect(screen.queryByText("Jelly 的组合")).not.toBeInTheDocument();
expect(screen.getByText("模拟数据 · 不构成投资建议")).toBeInTheDocument();
```

- [ ] **Step 2: Verify RED**

Run `pnpm test:run -- src/App.test.tsx`.

Expected: FAIL because the tenant switcher and new headings do not exist or the legacy code cannot compile.

- [ ] **Step 3: Implement the shell**

`App.tsx` owns only `tenantId`, `activeSession`, and `activeView`. Derive the snapshot through `getTenantSnapshot`; when tenant changes, reset session to `PRE_MARKET`, view to reports, and focused instrument to `null`. Remove unused `mobileView` and empty callbacks.

- [ ] **Step 4: Implement accessible layout navigation**

Sidebar buttons use text labels and `aria-current`; mobile navigation uses the same labels. `AppHeader` contains tenant selector, “模拟数据” disclosure, WeCom binding badge, and last-sync time. Icons are decorative with `aria-hidden`, not the only accessible label.

- [ ] **Step 5: Run App tests and verify GREEN**

Run `pnpm test:run -- src/App.test.tsx`.

Expected: PASS.

- [ ] **Step 6: Commit the shell**

```powershell
git add -- src/App.tsx src/App.test.tsx src/components/Sidebar.tsx src/components/layout/AppHeader.tsx
git commit -m "feat(ui): add tenant-aware MVP navigation"
```

### Task 5: Implement the four-session report workspace

**Files:**

- Create: `figma/v1-mobile/src/components/reports/ReportWorkspace.tsx`
- Create: `figma/v1-mobile/src/components/reports/ReportHeader.tsx`
- Create: `figma/v1-mobile/src/components/reports/EvidenceList.tsx`
- Create: `figma/v1-mobile/src/components/reports/PostMarketDecision.tsx`
- Create: `figma/v1-mobile/src/components/reports/ReportWorkspace.test.tsx`
- Remove after replacement: `figma/v1-mobile/src/components/ChatArea.tsx`

**Interfaces:**

- Consumes: one tenant's reports, active session, `onSessionChange`, and `onDecisionChange`.
- Produces: report presentation and local post-market decision feedback; no network side effect.

- [ ] **Step 1: Write failing session and evidence tests**

Assert the four tab names, 14:30 estimate disclosure, evidence source/as-of, unavailable-data abstention, and session switch:

```ts
expect(screen.getAllByRole("tab")).toHaveLength(4);
await user.click(screen.getByRole("tab", { name: "14:30 基金" }));
expect(screen.getByText(/盘中估值为区间估算/)).toBeInTheDocument();
expect(screen.getByText(/数据截至/)).toBeInTheDocument();
expect(screen.getByRole("status")).toHaveTextContent(/估算|陈旧|不可用/);
```

- [ ] **Step 2: Write failing post-market interaction tests**

Switch to post-market and assert buttons named `确认记录`, `需要纠正`, and `未执行`; clicking each displays the matching draft state. Assert the copy says no formal record is written until backend confirmation.

- [ ] **Step 3: Verify RED**

Run `pnpm test:run -- src/components/reports/ReportWorkspace.test.tsx`.

Expected: FAIL because report components do not exist.

- [ ] **Step 4: Implement session report cards**

Use one shared report shell rather than four duplicated components. Vary session-specific callouts through typed configuration. Display report title, summary, as-of, data-status badge, evidence, affected instruments, risk/action checklist, and WeCom delivery status. The 14:30 copy must say “估值区间，不是实时净值”.

- [ ] **Step 5: Implement post-market decision feedback**

Maintain only component-local demo state. Use buttons with visible selected state and an explanatory note: “原型仅演示状态；生产环境由鉴权 API 写入不可变决策日志。”

- [ ] **Step 6: Run report tests and verify GREEN**

Run `pnpm test:run -- src/components/reports/ReportWorkspace.test.tsx`.

Expected: PASS.

- [ ] **Step 7: Remove legacy ChatArea and commit**

```powershell
git add -- src/components/reports src/components/ChatArea.tsx
git commit -m "feat(ui): implement the four-session report workspace"
```

### Task 6: Rebuild portfolio, watchlist, and context views for three asset types

**Files:**

- Replace: `figma/v1-mobile/src/components/PortfolioView.tsx`
- Replace: `figma/v1-mobile/src/components/WatchlistView.tsx`
- Replace: `figma/v1-mobile/src/components/ContextPanel.tsx`
- Modify if required: `figma/v1-mobile/src/components/Sparkline.tsx`
- Create: `figma/v1-mobile/src/components/PortfolioView.test.tsx`

**Interfaces:**

- Consumes: current `TenantSnapshot` and selected `Instrument`.
- Produces: tenant-scoped portfolio totals, asset labels, fund-estimate disclosures, and evidence-aware context details.

- [ ] **Step 1: Write failing portfolio tests**

Assert labels `A股股票`, `场内基金`, `场外基金`, current tenant's three instrument names, base currency CNY, and that off-exchange fund valuation includes `估算` and an as-of timestamp. Assert no other tenant instrument appears.

- [ ] **Step 2: Verify RED**

Run `pnpm test:run -- src/components/PortfolioView.test.tsx`.

Expected: FAIL because the legacy portfolio accepts no tenant snapshot and models only stocks.

- [ ] **Step 3: Implement portfolio calculations without mutating fixtures**

Derive market/estimated value and P&L during render from current snapshot. Use asset-aware quantity units (`股`, `份`) and price labels (`行情`, `盘中估算`, `最新净值`). Do not use `useEffect` for derived totals.

- [ ] **Step 4: Implement watchlist and context evidence**

Watchlist receives tenant-scoped instruments; context panel displays source, as-of, status, thesis summary, invalidation condition, and a visible abstention warning for unavailable data. Remove buy/sell buttons that imply execution; use `记录观察` and `更新论点` prototype actions.

- [ ] **Step 5: Run portfolio tests and verify GREEN**

Run `pnpm test:run -- src/components/PortfolioView.test.tsx`.

Expected: PASS.

- [ ] **Step 6: Commit portfolio views**

```powershell
git add -- src/components/PortfolioView.tsx src/components/PortfolioView.test.tsx src/components/WatchlistView.tsx src/components/ContextPanel.tsx src/components/Sparkline.tsx
git commit -m "feat(ui): show stocks and funds with data provenance"
```

### Task 7: Polish responsive styling and remove obsolete code

**Files:**

- Modify: `figma/v1-mobile/src/index.css`
- Modify: `figma/v1-mobile/src/App.tsx`
- Remove obsolete exports/files discovered by `rg`.

**Interfaces:**

- Consumes: all implemented components.
- Produces: responsive and formatter-clean final prototype.

- [ ] **Step 1: Add a failing accessibility regression assertion**

In `App.test.tsx`, verify every session and mobile navigation control is reachable by role/name, status badges expose text, and the tenant selector has a label. Run the test and confirm it fails for any missing label before adjusting JSX.

- [ ] **Step 2: Fix only the failing accessibility/styling issues**

Keep CSS imports first; preserve existing theme tokens; add focus-visible rings, reduced-motion handling, compact mobile padding, and safe bottom navigation spacing. Do not add a new font, visual brand, or generated asset.

- [ ] **Step 3: Find and remove obsolete mojibake and imports**

Run:

```powershell
rg -n "[锟斤拷鈥馃]|AlphaGhost|trade-confirm|service_role" src
pnpm typecheck
```

Expected: no mojibake/legacy agent labels/trade-confirm flow and no TypeScript error.

- [ ] **Step 4: Run all UI checks**

```powershell
pnpm test:run
pnpm typecheck
pnpm format:check
pnpm build
```

Expected: every command exits 0 with no test failure, TypeScript error, format difference, or build error.

- [ ] **Step 5: Commit polish**

```powershell
git add -- src package.json pnpm-lock.yaml vite.config.ts
git commit -m "fix(ui): complete responsive and accessible MVP polish"
```

### Task 8: Integrate documentation and prototype verification

**Files:**

- Modify if validation exposes gaps: `docs/08.WealthSail需求设计测试追溯矩阵.md`
- Modify if validation exposes gaps: `figma/v1-mobile/src/imports/04.WealthSail-MVP-UI____.md`

**Interfaces:**

- Consumes: completed documentation and UI plans.
- Produces: final evidence that code, docs, and tests describe the same MVP.

- [ ] **Step 1: Map prototype behaviors to requirement IDs**

Add UI prototype test names and file paths to the traceability rows for four sessions, three assets, tenant switching, WeCom state, data status, evidence, and post-market decision state. Keep status `PASS` only for the React prototype tests actually executed; backend/system cases remain `NOT_RUN`.

- [ ] **Step 2: Update the imported UI brief**

Replace legacy AlphaGhost/stock-only copy with WealthSail, four sessions, three asset types, tenant switcher, evidence/as-of, and prototype-only disclaimers. This file is descriptive input, not a separate product authority.

- [ ] **Step 3: Run fresh full verification**

From repository root:

```powershell
pwsh -NoProfile -File scripts/validate_docs.ps1
Push-Location figma/v1-mobile
pnpm test:run
pnpm typecheck
pnpm format:check
pnpm build
Pop-Location
git diff --check
git status --short
```

Expected: document validator and all frontend checks exit 0; Git shows only intended source/doc/plan changes and no `dist`, credentials, or dependency cache.

- [ ] **Step 4: Scan for secrets and false completion claims**

```powershell
rg -n "(sk-[A-Za-z0-9_-]{20,}|service_role\s*=|WECOM_SECRET\s*=\S+|\|\s*PASS\s*\|)" docs scripts figma/v1-mobile/src
```

Expected: no key-like value; `service_role` only in warnings; any `PASS` rows are backed by the current React test run and explicitly scoped to prototype tests.

- [ ] **Step 5: Commit integration evidence**

```powershell
git add -- docs figma/v1-mobile/src figma/v1-mobile/package.json figma/v1-mobile/pnpm-lock.yaml figma/v1-mobile/vitest.config.ts scripts ai-review
git commit -m "chore: align WealthSail docs prototype and verification"
```
