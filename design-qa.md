# 空数据状态与风控摘要设计验收

## 验收范围

本轮针对用户提供的三个生产界面截图，统一修复仅用一行大号文字表达空数据、状态层级不清以及摘要信息拥挤的问题。

| 页面 | 源截图 | 源像素 | 实现截图 | 实现像素 |
|---|---|---:|---|---:|
| 研报审计 | `C:\Users\Jelly\AppData\Local\Temp\codex-clipboard-c33c1a6b-c06f-4576-90e7-2ad30d862fff.png` | 1803 × 496 | `C:\Users\Jelly\AppData\Local\Temp\wealthsail-reports-empty-after.png` | 1188 × 693 |
| 风控政策 | `C:\Users\Jelly\AppData\Local\Temp\codex-clipboard-33d25c10-4f2e-4574-bcd6-3014abe113be.png` | 1780 × 1342 | `C:\Users\Jelly\AppData\Local\Temp\wealthsail-risk-empty-after.png` | 1200 × 700 |
| 天天基金导入 | `C:\Users\Jelly\AppData\Local\Temp\codex-clipboard-e72b440e-c849-4539-99c7-71d06b562542.png` | 1756 × 943 | `C:\Users\Jelly\AppData\Local\Temp\wealthsail-onboarding-empty-after.png` | 1200 × 700 |

- 浏览器 CSS 视口：桌面 1200 × 700；窄屏 680 × 800；设备像素比 1。
- 源截图具有不同的浏览器缩放比例，因此视觉判断以卡片或工作区焦点区域为单位，等比缩放后比较相对间距、层级和密度，不对字体做失真的逐像素比较。
- 页面状态：研报列表为空、租户没有已确认投资政策及风险预算、天天基金没有已确认服务器导入批次。

## 同屏比较证据

- 研报审计：`C:\Users\Jelly\AppData\Local\Temp\wealthsail-reports-empty-comparison.png`
- 风控政策：`C:\Users\Jelly\AppData\Local\Temp\wealthsail-risk-empty-comparison.png`
- 天天基金导入：`C:\Users\Jelly\AppData\Local\Temp\wealthsail-onboarding-empty-comparison.png`

三个比较文件均为左侧源截图、右侧修复后浏览器截图，已经在同一图像输入中检查。窄屏补充证据：

- `C:\Users\Jelly\AppData\Local\Temp\wealthsail-reports-empty-narrow.png`
- `C:\Users\Jelly\AppData\Local\Temp\wealthsail-risk-empty-narrow-aside.png`
- `C:\Users\Jelly\AppData\Local\Temp\wealthsail-onboarding-empty-narrow.png`

## 比较历史与修复

### P1：未配置政策与“CONFIRMED v3”互相矛盾

- 早期证据：源截图右侧显示“未配置”，左侧表单顶部却固定显示 `CONFIRMED · v3`，会让用户无法判断政策是否真的生效。
- 修复：风险预算表单接收真实当前政策摘要；有政策时显示真实版本和生效日期，无政策时显示中性“未配置 / 尚未配置生效策略”。
- 修复后证据：桌面及窄屏截图中，表单和右侧卡片都一致表达未配置状态。

### P2：空数据退化成偏大的普通文字

- 早期证据：“暂无研报”“当前租户尚未配置……”“暂无服务器导入批次”没有图标、标题与补充说明，视觉重心靠边且字体显得突兀。
- 修复：复用项目 `EmptyState`，为三个业务域提供紧凑图标、标题和下一步说明；研报时间线与阅读区分别说明列表和详情的空状态。
- 修复后证据：同屏比较中，空状态均位于可用空间的视觉中心，并与现有青绿色设计令牌、卡片圆角和字体层级一致。

### P2：风险预算占位符拥挤且难以理解

- 早期证据：空预算仍显示 `—%`，四组标签和值挤在同一行并发生不规则换行。
- 修复：没有已确认预算或预算字段不完整时只显示“尚无风险预算”，避免把空值拼成误导性的百分比；仅当三项核心预算完整时，才使用逐行标签和值展示，并把 L1 范围说明放入独立说明块。

### P2：研报窄屏产生页面级横向溢出

- 早期证据：首轮 680px 浏览器验收中，文档 `scrollWidth` 为 773px、`clientWidth` 为 673px，空时间线仍继承四列报告网格。
- 修复：空列表添加 `is-empty` 状态；窄屏工作区使用 `minmax(0, 1fr)`，空列表切换为单列并关闭无意义的横向滚动。
- 修复后证据：680px 视口中页面及报告列表的 `scrollWidth` 均等于 `clientWidth`。

## 五个视觉面检查

- 字体：所有标题、说明和状态文本沿用项目字体栈；空状态标题为 10–13px，说明为 9–10px，没有突兀的大号正文。
- 间距与布局：空状态居中并保留卡片呼吸空间；风险摘要按卡片层级排列；桌面和窄屏均无裁切或页面级横向溢出。
- 颜色：沿用 `--teal`、`--slate-*`、现有状态 pill 和浅青色图标底色，不引入新的视觉体系。
- 图像与资产：页面没有位图资产；图标来自项目现有 Lucide 图标库，没有手绘 SVG、字符图标或占位图片。
- 文案：分别解释报告生成后的去向、策略确认后的展示内容和导入确认后的历史记录，避免只有“暂无”而没有下一步。

## 浏览器与交互验收

| 检查项 | 结果 |
|---|---|
| 页面身份与有效 DOM | 通过；三个状态均有可访问的 `role=status` 和明确名称。 |
| 空白页或框架错误覆盖层 | 通过；页面均渲染真实组件内容。 |
| 控制台错误和警告 | 通过；最终验收标签页无应用错误或警告。 |
| 桌面视觉 | 通过；1200 × 700 下层级和卡片密度协调。 |
| 窄屏响应式 | 通过；680 × 800 下自然换行，无页面级横向溢出。 |
| 交互证明 | 通过；把“单一标的最大仓位”从 30 改为 25 后，表单切换到 `DRAFT`，并启用“预览策略影响”按钮。 |

## 既有回归范围

- 租户名称输入框与保存按钮的同轴对齐、700px 内容容器断点和角色隔离测试保持有效。
- 账号安全页已有宽屏及窄内容区回归结论未被本轮样式改动撤销。

## 结论

final result: passed
