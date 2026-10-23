# NAS 外网接入设计

日期：2026-10-23。用户已确认：仅指定成员访问，启用 Cloudflare Access；StudyOS 尚未部署，使用容器内部端口 8000。

## 1. 选型与访问路径

NAS 常驻增加一个 **Cloudflare 官方 `cloudflared` 容器**，采用后台管理的 Named Tunnel。它主动建立出站连接，不要求家庭公网 IPv4、DDNS、路由器入站端口映射或额外 VPS。[官方 Tunnel 介绍](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)

浏览器 HTTPS → Cloudflare Access 成员验证 → 加密 Tunnel → NAS cloudflared → 应用 HTTP 入口 → 应用自己的账号认证。

| 公网域名 | Tunnel Service URL | 应用自身身份 |
|---|---|---|
| `wealth.sinojelly.kdns.fr` | `http://wealthsail-origin:80` | WealthSail 邮箱/密码、用户/租户/RLS |
| `studyos.sinojelly.kdns.fr` | `http://studyos-origin:8000` | StudyOS 家长/学生、会话与 CSRF |

两个域名分别创建 Access application，可以维护不同的允许成员。Access 不替代应用内的登录，也不自动创建应用用户。

局域网和外网默认使用相同 HTTPS 域名、相同 Access 策略，都经过 Cloudflare。此方案需要 NAS 与客户端能访问互联网；没有配置分流 DNS 或离线局域网 HTTPS。两个应用均不发布宿主端口，管理员通过可信 NAS 终端执行 `docker compose exec` 维护。

## 2. 文件职责

| 文件 | 用途 |
|---|---|
| `compose.yaml` | 独立 Tunnel 项目、两个 edge 网络、只读 token secret |
| `compose.wealthsail.yaml` | 仅扩展 WealthSail frontend，附加 Nginx 防护配置 |
| `compose.studyos.yaml` | 扩展 StudyOS 单容器入口、精确信任代理、收紧域名并移除宿主发布 |
| `compose.postgres-local-only.yaml` | 可选收紧已有 PostgreSQL/pgAdmin 的宿主机管理端口 |
| `.env.example` | 非秘密的网络和地址参数；镜像版本固定在 `compose.yaml` |
| `nginx/wealthsail-security.conf.template` | 可信访客 IP、身份接口限速、请求体大小 |
| `README.md` | 从 Cloudflare 控制台到 NAS 部署的操作步骤 |
| `SECURITY.md` | 安全边界、验收清单、撤销及应急 |

Tunnel 的 hostname、Access team/AUD 由 Cloudflare 控制台配置，**不在本地放第二份 ingress 配置**。本地没有 Cloudflare API key 或账户级 `cert.pem`，只保存该 Tunnel 的连接 token。[远程管理权限](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/remote-tunnel-permissions/)

## 3. 网络与可信代理

Tunnel Compose 创建两个独立 bridge 网络。默认参数可调整，但三个 Compose overlay 必须读取同一份 `.env`：

| 网络 | 默认子网 | 动态分配池 | cloudflared 固定地址 | 加入的应用 |
|---|---|---|---|---|
| `wealthsail-tunnel-edge` | `172.30.91.0/29` | `172.30.91.4/30` | `172.30.91.2` | 仅 WealthSail frontend |
| `studyos-tunnel-edge` | `172.30.92.0/29` | `172.30.92.4/30` | `172.30.92.2` | 仅 StudyOS 服务 |

固定地址用于精确信任代理，并非 NAS 的局域网 IP。部署前检查与现有 Docker、LAN、VPN 子网不重叠。前端保留原应用网络；API/Worker 保留原 PostgreSQL 网络；cloudflared 不加入 `wealthsail` 或 `global-postgres-network`。

动态池是对应子网的子集，固定连接器地址位于动态池之外，避免连接器停止或 NAS 重启时先启动的应用占用受信 IP。修改网络须一起修改 subnet、ip_range 和固定地址。[Docker 静态地址与动态分配](https://docs.docker.com/reference/cli/docker/network/connect/#network-implications-of-stopping-pausing-or-restarting-containers)

Docker bridge 隔离限制直接访问另一网络的容器，但不等于完整出站防火墙。连接器仍能主动访问可路由的 NAS/LAN 地址；应同时收紧宿主机发布的数据库、pgAdmin 和其他管理端口。两个站共用一个连接器，它是两站共同的信任边界。

WealthSail 只信其 cloudflared 地址传入的 `CF-Connecting-IP`，按真实访客地址限流；未受信来源不能通过伪造此 header 改变限流身份。Nginx 全局允许 21MiB 请求体以容纳现有 20MiB ZIP 和 multipart 开销，业务层继续执行原上传限制。身份操作有独立速率键；普通查询、refresh、SSE 不受该身份接口限额影响。

StudyOS 只把该站的 cloudflared 地址设为 `FORWARDED_ALLOW_IPS`，保留外部 Host 与 HTTPS scheme，使 Origin 检查及 Secure cookie 正确。禁止用 `*` 代理信任规避 403。现有 StudyOS 的本机首家长初始化限制不变，使用可信容器终端脚本初始化。

## 4. Cloudflare 层边界

必须先创建两个精确域名的 Access application，Allow 仅指定邮箱/身份组，未匹配默认拒绝。再在各 Tunnel route 启用 **Protect with Access**，填写团队名称和对应 app AUD，让 cloudflared 在连接器侧验证 Access JWT。[官方接入流程](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)、[Protect with Access 参数](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/#access)

应用内密码、角色、租户隔离、会话和 CSRF 保持。Cloudflare 全域名缓存规则对这两个 hostname 设置 Bypass，不缓存登录、财务和学习资料；按账号可用功能开启 WAF、防爆破规则及访问日志。

本次不发布企微等无浏览器回调例外。启用回调前需单独设计精确路径的 Access/Tunnel 规则，并保留应用验签、时间窗和防重；不能把整个 `/api` 设为 Bypass。[官方精确路径例外说明](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/common-policies/#bypass-a-public-endpoint)

## 5. 域名和 TLS 前置条件

实际 Cloudflare zone 应为用户控制且 Active 的 `sinojelly.kdns.fr`；此时两个目标都是该 zone 的首层 hostname。若账户实际管理的是 `kdns.fr` zone，两个目标是更深层 hostname，应先确认额外证书覆盖。不能只凭“DNS 托管成功”推断证书已可用。[Universal SSL 覆盖范围](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/#hostname-coverage)

浏览器到 Cloudflare 使用有效 HTTPS；Cloudflare 到 cloudflared 是加密 Tunnel；同一 NAS 的 Docker bridge 最后一跳使用 HTTP。Cloudflare 是可信 TLS 中间层，能处理 HTTP 明文内容；这里不声称浏览器到应用进程全程无第三方可见。

## 6. 兼容性与范围

- 采用官方 `2026.10.3` 版本作为初始固定版本；`--token-file` 要求至少 2025.4.0。拉取和 digest 记录在 NAS 执行，后续有计划更新。[运行参数](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/#token-file)、[版本发布](https://github.com/cloudflare/cloudflared/releases/tag/2026.10.3)
- 要求 Docker Compose **2.24.4+**，用 `ports: !override []` 清空原 ports，防止合并时留下可绕过 Access 的宿主入口。[Docker 合并语义](https://docs.docker.com/reference/compose-file/merge/#replace-value)
- 保留现有 SSE。使用 Named Tunnel，响应维持 `text/event-stream` 和禁缓冲，不用临时 Quick Tunnel。[流式响应说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/troubleshoot-tunnels/common-errors/)
- StudyOS 原项目允许很大的单批上传；实际外网请求受 Cloudflare 套餐大小/时长限制。大批量应拆分，或走受信管理通道；不能宣称 1GiB 单请求可原样通过公网。
- 不修改 StudyOS 仓库源码或其既有数据卷、Codex 认证配置。它的 Agent sandbox 设置继承原项目；网络入口保护不能代替 Agent 文件/执行权限边界。
- 本机没有 Docker，本次验证配置解析、文件引用及隔离约束；容器启动、镜像、Cloudflare 控制台和双网访问需在 NAS 实测。

## 7. WealthSail 的 Codex 内部服务

聊天、普通报告的模型分析及每日公共研究统一通过 `codex-runner`；固定计算继续由原业务逻辑完成。Runner 只加入独立 `codex` 网络，API/Worker 通过专用令牌调用，不发布 8090，不加入两个 edge 网络或 PostgreSQL 网络。Cloudflare 不新增 Codex 路由，也不参与 Codex 的出站账号登录。

仅 Runner 和一次性 `codex-auth` 登录工具挂载可写 `codex_auth` 卷，以支持凭据自动刷新；API/Worker、frontend、cloudflared 没有 Codex 认证文件。Runner 无数据库、Web 会话、邮件或企微密钥。私人聊天/报告使用独立临时上下文、禁止工具与搜索；公共每日研究保留联网与每日唯一名额。认证卷与 StudyOS 分离，操作见 [Codex 认证与部署指南](../docs/32.Codex统一模型与NAS认证操作指南.md)。
