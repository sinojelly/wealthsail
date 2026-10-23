# 安全配置与验收

本方案提供分层防护，不能保证服务绝对安全。Cloudflare 账户、NAS 主机、应用依赖、应用账号和备份均属于信任边界。仅完成 Tunnel 连通不等于完成安全部署。

## 上线前必须完成

### Cloudflare

- 账户启用多因素验证，优先 Passkey/硬件安全密钥；只授予必要管理员权限。
- 两个精确 hostname 各建一个 Access application，仅 Allow 指定邮箱/身份组。不要配置 `Everyone` 放行或广泛 Bypass。
- 建议会话时长 8 小时。使用身份提供方时，在 IdP 要求 MFA；邮件一次性验证码不等同于已经强制 MFA。
- 两条 Tunnel route 都启用 **Protect with Access**，正确区分两个 AUD，不能只依赖边缘登录页。
- 两个 hostname 均有效 HTTPS，启用 HTTP → HTTPS 重定向、最低 TLS 1.2。HSTS 可先仅当前主机、不加 preload/全域 includeSubDomains，确认所有相关服务后再扩大。
- 对两个 hostname 设置缓存 Bypass；不要让已有的 Cache Everything 规则覆盖金融和学习数据。
- 按套餐可用性启用 WAF/速率规则，重点保护两应用的登录、密码重置和注册；不要对整个 API 套浏览器挑战而破坏 SSE/机器调用。

### NAS 与 Docker

- 不配置路由器端口转发，不通过 Tunnel 发布 NAS 管理面板、Docker API、PostgreSQL、pgAdmin、SSH 或整个网段。
- cloudflared 不使用 host networking、特权模式、Docker socket、应用数据卷或数据库凭证；运行 rootfs 只读并移除 Linux capabilities。
- 两个应用默认 `ports: !override []`，不发布宿主 Web 端口；管理员使用容器终端维护。确认未保留旧的 `0.0.0.0` 或其他宿主映射。
- 收紧 PostgreSQL/pgAdmin 的宿主端口。可选 overlay 仅改变绑定地址，不改变数据库卷和应用间 Docker 网络；应用若仍通过 LAN IP 连接 SQL，先调整连接后再应用。
- 可选数据库回环发布要求 Docker Engine 28+，旧 Engine 须另用主机防火墙阻断同网段旁路并实测；localhost 发布在旧版不保证只有本机可达。[Docker 官方限制](https://docs.docker.com/engine/network/port-publishing/#publishing-ports)
- 固定 cloudflared 地址必须与 Nginx/StudyOS 信任地址一致，并位于动态 IP 分配池之外，防止重启时被应用抢占。不要泛信 `X-Forwarded-For`、`CF-Connecting-IP` 或使用 `FORWARDED_ALLOW_IPS=*`。
- NAS 防火墙限制 LAN 管理入口，公网 IPv6 同样不得绕过 Cloudflare 直达应用。Bridge 网络本身不能防止连接器访问所有宿主机已发布端口。
- Token 文件仅挂载给 cloudflared：宿主文件 owner `65532:65532`、mode `0400`；父目录仅 NAS 管理员可进入。不把 token 写进 `.env`、Compose command 或 Git。
- Compose 的 file secret 不提供加密存储，并且 file 来源的 uid/gid/mode 设置不能替代宿主权限；磁盘/备份仍须保护。[Docker secrets 说明](https://docs.docker.com/compose/how-tos/use-secrets/)

### 应用

- WealthSail 使用原自建 PostgreSQL、受限运行账号、RLS、强口令与邀请制；Access 通过不代表拥有某租户权限。
- StudyOS 首位家长由容器终端初始化；家庭管理员控制学生账号、恢复凭证和敏感操作。不能为了外网首装取消“仅本机初始化”校验。
- StudyOS 现有 Docker 模式可能使用 `STUDYOS_AGENT_SANDBOX=none` 兼容 NAS，映射为 Codex 的宽执行模式。该应用容器仍可写自己的工作区和 Codex 运行卷，不能承诺模型无法修改文件或使用认证。仅允许可信家庭成员，避免挂载 NAS 其他资料。
- 可在目标 NAS 验证 `read-only` Agent sandbox，确认识别、批改、流式对话均工作后采用；不要为嵌套沙箱加入 `privileged`、`SYS_ADMIN` 或 `seccomp=unconfined`。
- 保留正常口令轮换、依赖/镜像更新、数据备份和隔离恢复演练。Tunnel token 与用户资料备份均不得进入构建上下文。

## 局域网与旁路

默认 LAN/WAN 都经 Cloudflare Access，局域网不另配直连 DNS，应用不发布宿主端口。若管理员以后另开 NAS 反向代理、分流 DNS 或 LAN IP 入口，这些路径不会自动经过 Access，必须另行限制来源并保持应用自己的登录/HTTPS，不能宣称它们受 Access 保护。

企微回调目前保持关闭/受全站 Access 阻断。如将来必须发布，仅审查精确 `/api/v1/adapters/wecom/callback`，同时调整精确 Access app 和精确 Tunnel route 的 JWT 必需项；保留签名、时间窗、防重及小请求体限制。禁止给整个 `/api` 放行。[Cloudflare 路径例外](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/common-policies/#bypass-a-public-endpoint)

## NAS 验收清单

- [ ] Cloudflare zone Active，两个 hostname 的 edge certificate 有效。
- [ ] 未登录 Access 的新浏览器，两个域名都进入 Access 验证/被拒绝，不能直接看到应用。
- [ ] 未授权邮箱不能进入；授权成员通过 Access 后，仍需应用内登录。
- [ ] 清除/过期 Access 会话后不能直接调用 API；故意移除边缘 app 的测试应只在隔离 hostname，确认 connector JWT 验证能拒绝缺 token 请求。
- [ ] NAS 本机 `docker compose config --quiet`、WealthSail `nginx -t` 成功，两个应用的健康检查成功。
- [ ] 两应用无宿主发布端口；外部扫描 NAS 公网 IPv4/IPv6 不能直达这些服务及数据库/管理端口；管理端口按 Engine 版本验证同网段也无旁路。
- [ ] 用户登录、刷新页面、跨租户拒绝、StudyOS 登录/CSRF 写操作正常，会话 cookie 带 Secure/HttpOnly；CSRF cookie 仍允许前端读取。
- [ ] WealthSail 登录请求突发超过限额返回 429，普通查询/refresh/SSE 无误伤；不同访客可区分真实 IP。
- [ ] 两站同源 SSE 实际逐事件显示，Access 到期后的重连能正确重新验证。
- [ ] 头像、20MiB 内基金导入正常；StudyOS 按真实公网限额拆分大上传。
- [ ] Cloudflare 不缓存带用户数据的接口；容器日志不包含口令、token、Cookie 或完整私密请求头。
- [ ] 分别在 Wi-Fi 和手机蜂窝网络访问两个域名；重启 NAS 后服务恢复，撤销某成员后不可重新访问。

## 紧急断开与 token 泄露

1. 立即停止 NAS 的 cloudflared 容器，或在 Cloudflare 禁用/删除受影响 published route / Access application 的允许策略。
2. 在 Cloudflare 轮换该 Tunnel token。**若怀疑泄露，同时断开所有既有 connector 连接**；仅换 token 不会自动踢掉已建立的恶意连接。
3. 更新宿主 token 文件，重新设置 `65532:65532` 与 `0400`，强制重建可信 cloudflared 容器。
4. 核对连接器清单、Access 事件、应用登录与操作日志，必要时撤销应用会话和受影响凭证。

依据：[Cloudflare 远程 Tunnel token 权限和轮换](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/remote-tunnel-permissions/)。删除 Tunnel 容器不会删除 PostgreSQL、StudyOS 工作区或应用账号。
