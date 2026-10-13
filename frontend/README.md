# WealthSail Web Frontend

本目录是可运行的 WealthSail Web 前端。视觉与交互以 `figma/v2-code` 为主，`figma/v2-ui-images` 仅用于视觉对照；工程结构遵循 `docs/09` 第 6.2 节。废弃的 `figma/v1-mobile` 未参与实现。

## 本地运行

```bash
pnpm install
pnpm dev
```

默认开发地址为 `http://localhost:5173`。可通过 `VITE_API_BASE_URL` 指定后端 API 根路径；未配置时使用同源 `/api`。

Windows 一键编译、启动数据库/后端及测试账号见 [本地运行文档](../docs/26.Windows-local-development.md)。在仓库根目录执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1` 即可使用便携 PostgreSQL，无需 Docker 或安装数据库服务。已安装依赖时可加 `-SkipInstall`；停止服务使用 `-Action Stop`。只启动前端不能完成真实登录。

## 质量命令

```bash
pnpm test       # Vitest + Testing Library 用户流程测试
pnpm typecheck  # TypeScript strict 检查
pnpm lint       # ESLint（零 warning）
pnpm build      # 类型检查 + Vite 生产构建
```

## 目录职责

- `src/app`：路由、内存会话和 TanStack Query 客户端。
- `src/api`：Fetch 封装、按业务域拆分的 API，以及 OpenAPI 生成类型的预留目录。
- `src/domain`：金额精度、数据质量和图表数值边界。
- `src/features`：按业务能力拆分的页面与组件；页面通过路由懒加载。
- `src/components`：跨业务复用的布局、数据状态和可访问性组件。
- `src/test`：测试环境和带租户会话的统一渲染入口。

## 已实现工作区

- 资产看板：股票、场内 ETF、场外基金、现金、收益曲线、资产配置、持仓过滤与成交草稿校对。
- 研报审计：盘前简报、盘中跟踪、基金 2:30 报告、盘后交互复盘及证据链抽屉。
- 风控政策：草稿、服务端语义预览、重新认证确认、版本和能力边界。
- 账号安全：企业微信一次性绑定凭证、软撤销、多用户租户角色和会话保护说明。
- 运维监控：调度作业、降级状态、高风险审计事件。
- 初始化导入：股票/ETF/基金批次校验、逐行预览和只提交有效记录。

## 金融数据边界

金额、份额、价格和比例在业务边界均使用十进制字符串。精确运算通过 `decimal.js` 完成；只有 `chart-number-adapter.ts` 可以把字符串转成 JavaScript `number`，且结果只能用于图表坐标，禁止回写事实账本。

## API 与测试数据

运行中的页面使用真实后端 API；`src/test/fixtures.ts` 的确定性数据只用于自动化测试。访问令牌保存在内存、刷新令牌使用 HttpOnly Cookie，结算和策略预览由服务端执行，写操作由后端再次校验 `tenant_id` 与角色权限。开发和 preview 服务通过 `/api` 代理到 `http://127.0.0.1:8000`，可用 `WS_LOCAL_API_TARGET` 调整代理目标。
