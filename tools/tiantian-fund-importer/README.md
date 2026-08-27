# 天天基金本地交易采集工具

这个工具在用户电脑上打开一个独立、可见的浏览器。用户自行扫码登录天天基金后，工具自动查询普通基金成功交易、按年度拆分日期、获取全部分页、去重并输出 WealthSail 可继续处理的 CSV/JSON。

它不自动下单，不修改天天基金账户，不保存用户名或密码，也不会把 Cookie、访问令牌或浏览器资料提交到 WealthSail 服务端。

## 1. 当前采集范围

工具自动查询天天基金交易页第一行的普通基金业务：

- 买入；
- 卖出；
- 转换；
- 超级转换；
- 定投；
- 组合调仓；
- 分红；
- 其他。

查询状态固定为“成功”。充值、自动充值、普通取现、快速取现、活期宝转出、互转、结转收益、预约取现、信用卡还款和每日收益等活期宝业务不会作为基金交易导入。

即使天天基金返回了混入的活期宝记录，工具仍会在本地二次分类，放入 `excluded-cash-management.csv`，不会进入 `fund-transactions.csv`。

`活期宝转入`和`卖出回活期宝`是普通基金以活期宝作为付款或回款渠道的真实买卖，工具会保留；`活期宝充值`、`活期宝快速取现`和`活期宝普通取现`属于现金管理，即使页面返回了底层货币基金代码也会排除。`强行调增`、`活动发放`以及没有基金代码的组合调仓等特殊记录进入 `review-required.csv`，不会伪装为标准成交。

## 2. 首次安装

要求 Node.js 22 或更高版本。进入工具目录安装依赖：

```powershell
cd D:\develop\gitee\WealthSail\tools\tiantian-fund-importer
pnpm install
```

工具默认使用系统安装的 Microsoft Edge，不需要另行下载自动化浏览器。

## 3. 推荐运行命令

当前参考资料中最早可见交易在 2020 年，因此第一次建议从 2020-01-01 开始：

```powershell
pnpm capture -- --from 2020-01-01
```

如果实际开户更早，把日期提前。例如：

```powershell
pnpm capture -- --from 2015-01-01
```

运行后只需协助完成以下步骤：

1. 工具打开一个独立的 Edge 窗口；
2. 在该窗口中登录天天基金，推荐使用天天基金 App 扫码登录；
3. 确认浏览器已经显示“交易查询”页面；
4. 回到 PowerShell，按一次 `Enter`；
5. 不要关闭自动打开的浏览器，等待终端显示“采集完成”。

以后再次运行会复用本机独立浏览器资料，通常不必重复登录；天天基金要求重新验证时，再扫码确认即可。

## 4. 浏览器启动失败

如果电脑没有 Edge，可改用 Chrome：

```powershell
pnpm capture -- --from 2020-01-01 --browser chrome
```

如果 Edge 和 Chrome 都不可用，可以安装 Playwright Chromium：

```powershell
pnpm exec playwright install chromium
pnpm capture -- --from 2020-01-01 --browser chromium
```

## 5. 输出内容

结果默认写入：

```text
tools/tiantian-fund-importer/private-data/exports/run-时间戳/
├── manifest.json
├── all-transactions.json
├── fund-transactions.csv
├── excluded-cash-management.csv
├── review-required.csv
└── non-posting.csv
```

- `manifest.json`：时间范围、分类数量、去重数量、文件 SHA-256 和安全声明；
- `all-transactions.json`：标准化后的完整结果，银行卡只保留尾号；
- `fund-transactions.csv`：普通基金成功交易，是后续导入 WealthSail 的主要文件；
- `excluded-cash-management.csv`：被排除的活期宝记录；
- `review-required.csv`：无法安全判断的“其他”产品或特殊交易，必须人工确认；
- `non-posting.csv`：不应改变持仓的状态记录。当前默认只查询成功状态，通常为空。

CSV 会阻断以 `= + - @` 开头的单元格，避免使用 Excel 打开第三方数据时执行公式。

## 6. 常用选项

```text
--from YYYY-MM-DD      最早交易日期，默认 2010-01-01
--to YYYY-MM-DD        截止日期，默认今天
--browser NAME         msedge、chrome 或 chromium
--delay-ms NUMBER      请求间隔，默认 800 毫秒，允许 300~10000
--output-dir PATH      自定义导出目录
--profile-dir PATH     自定义独立浏览器资料目录
--help                 显示命令帮助
```

天天基金历史查询每次最多支持约一年，工具会自动拆分为不重叠区间，并把最近一年与历史交易分别查询。每个业务类型、每个日期区间都从第一页开始，根据官方返回的总记录数继续获取后续页面。

## 7. 隐私与安全

- `private-data/` 和 `reference/天天基金/` 已在仓库根 `.gitignore` 中忽略；
- 不要把 `browser-profile/`、完整交易文件、原始截图或 Cookie 发到群聊、工单或 Git；
- 如需协助排查，优先提供 `manifest.json` 和已人工检查过的 `review-required.csv`；
- 临时访问令牌只存在于采集进程内存中，结束后随进程释放；
- 工具使用天天基金交易查询页面当前调用的结构化查询接口。网站升级后如果字段或接口发生变化，工具会停止并报错，不会把失败响应当成空交易继续导出。

## 8. 验证工具本身

```powershell
pnpm test
```

测试覆盖活期宝排除、普通基金保留、失败状态隔离、银行卡脱敏、日期切片、分页、去重、CSV 安全、查询请求契约以及会话秘密不落盘。
