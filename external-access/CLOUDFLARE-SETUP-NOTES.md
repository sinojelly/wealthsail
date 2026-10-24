# Cloudflare 控制台配置操作记录

本文件与 NAS 终端命令记录配套，记录 Cloudflare 控制台的实际操作及其用途。秘密参数、账号邮箱、账户 ID、应用 AUD 和真实 Tunnel token 不写入仓库；运行配置使用私密文件保存。

## 当前进度

2026-10-24：用户已在应用内浏览器自行完成 Cloudflare 登录。已核对 `sinojelly.kdns.fr` 位于当前账户，域名正在由 Cloudflare 提供 DNS/代理服务。助手进入免费计划 checkout 后遇到空白页面和浏览器自动权限审核超时，用户随后自行开通并明确确认“已激活 Zero Trust Free”。助手尚未复核激活后的组织/team name，尚未创建 Tunnel、Access 应用或公开路由。用户要求写交接文档后暂停，当前整体状态与恢复入口见 [部署交接记录](./DEPLOYMENT-HANDOFF-2026-10-24.md)。

| 实际步骤 | 为什么执行 | 结果 |
| --- | --- | --- |
| 检查可用浏览器和现有登录会话 | 优先复用已登录会话，避免接触账号密码/MFA | 检查时只有应用内浏览器，尚未登录 |
| 打开 `https://dash.cloudflare.com/` | 由账户所有者在官方页面完成登录 | 用户确认已完成登录，未由助手输入密码或 MFA |
| 打开目标域名的 Access 入口 | 确认操作的是指定域名，并查看 Zero Trust 启用状态 | 找到正确域名，Zero Trust 尚未启用 |
| 进入 Zero Trust onboarding 并选择免费计划 | 为两个站点准备成员验证，限定使用免费方案 | 已到达 free checkout；尚未确认组织开通成功 |
| 将免费组织开通步骤交给账户所有者 | checkout 页面读取为空白，且条款/账单需要本人核对 | 用户已确认 Zero Trust Free 激活；未选择付费计划 |

## 后续配置与验收要求

以下是待执行步骤，不代表已完成：

1. 确认目标 zone 为 `sinojelly.kdns.fr`，账户、DNS 托管及证书覆盖正确。
2. 复核用户已开通的 Zero Trust Free 组织和实际 team name，不重复开通或切换到付费方案。
3. 分别创建 WealthSail 和 StudyOS 的 self-hosted Access 应用，仅放行明确配置的成员邮箱，默认拒绝其他成员，不使用 Everyone 或 Bypass。
4. 创建一个后台管理的 Named Tunnel，连接 token 通过私密文件交给 NAS，不把控制台的明文 token 命令写进 shell 历史或 Git。
5. NAS 应用健康并通过本地验收后，才添加两个精确 hostname 路由，分别指向 `http://wealthsail-origin:80`、`http://studyos-origin:8000`。
6. 两条路由均开启连接器侧 Access 验证，分别使用对应应用 AUD；检查 HTTPS、缓存 Bypass、授权/未授权浏览器访问和 LAN/WAN 一致性。

## 备用：受限 API 管理方式

本次尚未执行下面的写 API。只有无法完成控制台操作、且用户选择 API 方式时再采用。

Tunnel 的连接 token 只让 `cloudflared` 连接该 Tunnel，不能创建 Access 应用或管理 DNS。API 管理需要另外的短期受限管理 token：指定账户的 Cloudflare Tunnel 写权限、Access Apps and Policies 写权限，以及指定 zone 的 DNS 读写权限。避免使用 Global API key；完成配置后撤销临时管理 token。

- 创建后台管理 Tunnel：`POST /accounts/$ACCOUNT_ID/cfd_tunnel`，指定 `config_src=cloudflare`。[官方 Tunnel API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/subresources/cloudflared/methods/create/)
- 创建 Access 应用：`POST /accounts/$ACCOUNT_ID/access/apps`，使用 self-hosted 类型和精确 hostname。[官方 Access API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/create/)
- 策略使用最小成员列表；应用级策略 API 也可创建策略，官方推荐可复用策略再由应用引用。[官方策略 API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/subresources/policies/methods/create/)

完整部署命令及维护操作见 [DEPLOYMENT.md](./DEPLOYMENT.md)，入口与应急说明见 [SECURITY.md](./SECURITY.md)。
