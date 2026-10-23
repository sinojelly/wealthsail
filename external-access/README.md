# NAS 外网接入操作手册

**首次从零部署请先看 [NAS 首次部署详细步骤](./DEPLOYMENT.md)**：按顺序列出需要修改的值、每套 Compose 文件和完整命令，不依赖命令简写。可以先上线 WealthSail，再接入 StudyOS。本文件保留参数、安全和运维说明供查阅。

日期：2026-10-23。使用 Cloudflare Named Tunnel + Access，仅指定成员可访问。NAS **新增一个常驻 `cloudflared` 容器**，不需要公网 IP、路由器端口转发或额外反向代理容器。LAN/WAN 均通过下面的 HTTPS 域名访问，先通过 Access，再登录应用。

| 域名 | Tunnel 内部目标 | 应用 |
|---|---|---|
| `wealth.sinojelly.kdns.fr` | `http://wealthsail-origin:80` | WealthSail frontend |
| `studyos.sinojelly.kdns.fr` | `http://studyos-origin:8000` | StudyOS |

设计见 [DESIGN.md](./DESIGN.md)，上线安全要求和验收清单见 [SECURITY.md](./SECURITY.md)。以下 NAS 命令使用 **Bash**；将路径替换为实际 NAS 路径。这里只创建配置，尚未操作 Cloudflare 账户或 NAS。

## 1. 前置检查

- NAS 已运行 Docker，`docker compose version` 至少 **2.24.4**。旧版 NAS Compose 不支持本方案的 `!override`，先更新 Compose，不能简单删掉该标记。
- 同时执行 `docker version` 查看 Engine 版本，保持受支持版本。应用默认不发布宿主端口；第 8 节可选数据库回环绑定在旧 Engine 上另有旁路限制。
- Cloudflare 中 `sinojelly.kdns.fr` zone 为 Active，两个 hostname 的边缘证书有效。若实际管理的是上级 `kdns.fr` zone，应先核实更深层域名的证书覆盖，参见 [TLS 设计](./DESIGN.md#5-域名和-tls-前置条件)。
- NAS 能出站访问 Cloudflare；受限网络须允许 Tunnel 所需的 TCP/UDP 7844 和 DNS/HTTPS。无需允许公网入站 80/443/8000/8080/5432。
- 已准备获准成员的邮箱。Access 的允许名单与应用内账号是两套管理，邀请成员时两边都要授权。

在 WealthSail 根目录准备路径和非秘密参数：

```bash
cd /你的NAS应用目录/WealthSail
WEALTH_ROOT="$PWD"
ACCESS_DIR="$WEALTH_ROOT/external-access"
test -e "$ACCESS_DIR/.env" || cp "$ACCESS_DIR/.env.example" "$ACCESS_DIR/.env"

docker network ls -q | xargs -r docker network inspect \
  --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'
```

同时检查 NAS/LAN/VPN 地址段。默认 `172.30.91.0/29`、`172.30.92.0/29` 必须与已有网络互不重叠。动态池分别为 `172.30.91.4/30`、`172.30.92.4/30`，专门排除连接器的固定 `.2` 地址。若冲突，在 `external-access/.env` 同时调整相应子网、动态池、连接器 IP；动态池须在子网内，固定 IP 须在动态池外，且不是网关/网络/广播地址。两个应用和 Tunnel 始终读取同一份该文件。

新开 SSH 会话后，需要重新设置这些变量和下面的函数。不要在 shell 中 export 同名参数覆盖 `.env`；尤其不要 export `STUDYOS_ALLOWED_HOSTS=*`。

## 2. Cloudflare：先建立 Access 保护

在 Cloudflare One / Zero Trust 的 **Access → Applications** 各创建一个 Self-hosted application（界面名称可能调整）：

| application | Public hostname | Allow 策略 |
|---|---|---|
| WealthSail | `wealth.sinojelly.kdns.fr`，所有路径 | 仅指定成员邮箱/身份组 |
| StudyOS | `studyos.sinojelly.kdns.fr`，所有路径 | 仅指定成员邮箱/身份组 |

选择邮件一次性验证码或已有身份提供方。不要使用 Everyone 放行、整站 Bypass 或跳过验证；未匹配默认拒绝。建议会话 8 小时；需要 MFA 时在身份提供方强制开启，邮件验证码本身不代表已强制 MFA。

记录两个 application **各自的 AUD** 和团队名称（`你的团队.cloudflareaccess.com` 中的团队部分），后面连接器会用它们验证 JWT。先建立 Access，再发布应用路由，避免短暂裸露入口。[官方 Access 流程](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)

## 3. 创建 Named Tunnel，安全保存连接 token

在 Cloudflare One 的 **Networks / Networking → Connectors → Cloudflare Tunnels** 新建 cloudflared Tunnel，例如 `nas-apps`，使用后台管理配置。选择 Docker，复制其连接 **token 值**。不直接执行控制台给出的明文 `--token ...` 示例，不使用临时 Quick Tunnel。

在 NAS Bash 中执行以下命令；token 隐藏输入，不会作为命令参数进入 shell 历史：

```bash
cd "$ACCESS_DIR"
mkdir -p secrets
chmod 700 secrets
sudo -v
read -rsp 'Tunnel token: ' cf_token
printf '\n'
printf '%s' "$cf_token" | sudo tee secrets/cloudflared-token >/dev/null
unset cf_token
sudo chown 65532:65532 secrets/cloudflared-token
sudo chmod 0400 secrets/cloudflared-token
```

父目录 `secrets` 由当前 NAS 管理员拥有，保持 `0700`；文件由容器 UID/GID `65532:65532` 拥有，保持 `0400`。若当前已是 root，可去掉 `sudo`。NAS 文件系统/ACL 必须实际支持这些权限，不能用让连接器以 root 运行来绕过权限错误。token 不放进 `.env`、Git、截图、聊天或公开备份。

Compose file secret 通过文件挂载，**不是加密密钥库**；Docker 的 file secret `uid/gid/mode` 不能代替宿主文件权限。[Docker secrets](https://docs.docker.com/compose/how-tos/use-secrets/)

定义 Tunnel 命令，拉取镜像并只创建容器/网络，暂不启动：

```bash
cf() {
  docker compose --env-file "$ACCESS_DIR/.env" \
    -f "$ACCESS_DIR/compose.yaml" "$@"
}
cf config --quiet
cf pull
cf create cloudflared
```

这会创建项目 `wealthsail-external-access` 及两个 edge 网络，供后续应用接入。镜像初始固定为 `cloudflare/cloudflared:2026.10.3`；NAS 首次拉取时核实平台支持并记录 digest：

```bash
docker image inspect cloudflare/cloudflared:2026.10.3 \
  --format '{{index .RepoDigests 0}}'
```

## 4. 接入 WealthSail

必须以 WealthSail 根 `docker-compose.yml` 为第一份文件，保持原项目名和卷名。若旧部署额外用了 `-p`，下面函数也须使用相同项目名，防止创建另一套空卷。

```bash
ws() {
  docker compose --env-file "$WEALTH_ROOT/deployment/nas.env" \
    --env-file "$ACCESS_DIR/.env" \
    -f "$WEALTH_ROOT/docker-compose.yml" \
    -f "$WEALTH_ROOT/docker-compose.nas.yml" \
    -f "$ACCESS_DIR/compose.wealthsail.yaml" "$@"
}
```

### 空库首次部署

先在 WealthSail 根目录生成一次配置。NAS 有 Python 时：

```bash
cd "$WEALTH_ROOT"
python3 scripts/configure_nas.py --web-base-url https://wealth.sinojelly.kdns.fr
```

NAS 无 Python 时可改用：

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$WEALTH_ROOT:/workspace" -w /workspace python:3.12-slim \
  python scripts/configure_nas.py --web-base-url https://wealth.sinojelly.kdns.fr
```

然后创建独立数据库和首位管理员：

```bash
docker network inspect global-postgres-network >/dev/null
ws config --quiet
ws build api frontend
ws run --rm --no-deps db-init \
  --confirm-target postgres:5432/wealthsail \
  --bootstrap-email 你的登录邮箱@example.com \
  --nickname 管理员 \
  --tenant-name 我的投研工作台
ws run --rm --no-deps codex-auth login --device-auth
ws run --rm --no-deps codex-auth login status
ws run --rm --no-deps --entrypoint python codex-auth -m scripts.codex_admin check
ws up -d codex-runner api worker frontend
```

安装器交互输入 NAS PostgreSQL `admin` 密码和新 Web 管理员密码。不会导入开发测试账号或演示租户。迁移、基础目录与首装数据清理规则详见 [NAS 数据库操作手册](../docs/31.NAS独立PostgreSQL部署与验证操作手册.md)。数据库管理员密码不写入此目录。

全部模型请求统一走 Codex；镜像已安装 CLI，设备登录用自己的电脑/手机浏览器完成。`codex_auth` 命名卷只由 Runner 和登录工具使用，与 StudyOS 分开。生成器现在生成四份配置；旧三文件先执行 `python3 scripts/configure_codex.py` 补齐，保留原密钥。认证文件备用导入、模型配置及每日研究开关见 [Codex 指南](../docs/32.Codex统一模型与NAS认证操作指南.md)，完整无简写命令见 [部署步骤 5.4](./DEPLOYMENT.md#54-为-wealthsail-完成-codex-登录)。

### 已有自建 PostgreSQL 部署

保留已有 env 和身份密钥，**不要重新生成配置或再次 bootstrap**。只核对 `deployment/env/api.env` 的下列两项：

```dotenv
WEB_BASE_URL=https://wealth.sinojelly.kdns.fr
CORS_ORIGINS=["https://wealth.sinojelly.kdns.fr"]
```

旧三文件先执行上文 Codex 配置补齐和设备登录，再执行 `ws config --quiet`、`ws build api frontend`、`ws up -d --force-recreate codex-runner api worker frontend`。frontend overlay 清空宿主端口映射，附加可信 IP/限速模板，API/Worker 继续使用原 PostgreSQL 网络。

### 检查入口

```bash
ws exec -T frontend nginx -t
ws ps
ws exec -T frontend wget -q -O /dev/null http://127.0.0.1/
```

以上检查在容器内完成；NAS 本机和 LAN 均无额外 Web 端口。Nginx 模板只替换连接器 IP，保留 `$uri` 等 Nginx 变量。普通查询、refresh 和 SSE 不套用登录接口的 10 次/分钟限流。

## 5. 接入 StudyOS，先建立首位家长

将 StudyOS 源码单独放到 NAS。**不要将它的基础 Compose 复制到此目录运行**，否则 `./volume`、build context 等相对路径会变。

```bash
cd /你的NAS应用目录/studyos
STUDYOS_DIR="$PWD"
test -e "$STUDYOS_DIR/.env" || cp "$STUDYOS_DIR/.env.example" "$STUDYOS_DIR/.env"
```

按 StudyOS 自己的文档准备 Codex 登录/配置，将其 `.env` 的 `CODEX_HOME_DIR` 改成 NAS 上真实存在的 Codex 目录，如 `/home/你的用户/.codex`，不能保留 Windows 示例路径。原只读宿主认证挂载、工作数据卷、Codex 运行卷都保留。

```bash
so() {
  docker compose --env-file "$STUDYOS_DIR/.env" \
    --env-file "$ACCESS_DIR/.env" \
    -f "$STUDYOS_DIR/docker-compose.yml" \
    -f "$ACCESS_DIR/compose.studyos.yaml" "$@"
}
so config --quiet
so build studyos
so up -d --wait studyos
so exec -it studyos python /app/scripts/bootstrap_container.py
so ps
```

初始化脚本隐藏输入家长密码；已有家庭不会被覆盖。完成后使用 `/v4/` 登录。外网不放开首次家长创建接口，也不伪造本机来源规避原校验。

后面的 `.env` 覆盖 StudyOS 示例中的 `STUDYOS_ALLOWED_HOSTS=*`，限制为 `studyos.sinojelly.kdns.fr`；overlay 只信任该站 cloudflared 固定 IP 的转发头，确保 HTTPS Origin/CSRF 和 Secure cookie 正常。不要设 `FORWARDED_ALLOW_IPS=*`。容器内部仍监听 8000，宿主不发布 8000 端口。

本次不改变 StudyOS 的 Agent sandbox 设置。其原容器模式可能使用 `none`；上线前阅读 [应用权限边界](./SECURITY.md#应用)，仅授权可信成员，不额外挂载 NAS 私人文件。

## 6. 发布精确路由，启动连接器

在同一 Named Tunnel 的 **Published application routes / Public Hostnames** 添加：

| 设置 | WealthSail | StudyOS |
|---|---|---|
| Hostname | `wealth.sinojelly.kdns.fr` | `studyos.sinojelly.kdns.fr` |
| Service type | HTTP | HTTP |
| URL | `wealthsail-origin:80` | `studyos-origin:8000` |
| HTTP Host Header | `wealth.sinojelly.kdns.fr` | `studyos.sinojelly.kdns.fr` |
| Protect with Access | 启用 | 启用 |
| Team name / AUD | 你的团队 / WealthSail app AUD | 你的团队 / StudyOS app AUD |

控制台若只显示一个 Service URL 字段，填写带 `http://` 的完整目标。目标不能填 `127.0.0.1`，那会指向 cloudflared 容器自己。**StudyOS 尚未启动/初始化时先不发布其路由**。不发布通配 hostname、NAS 管理面或整段内网，不创建公开数据库路由。

Protect with Access 是连接器侧的 JWT 验证；两个应用 AUD 不可混用。[对应官方设置](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/#access)。路由和 Access 由控制台统一管理，不在本地再放 `config.yml` ingress。

DNS 记录由 Tunnel 发布流程创建/关联；若已有同名 A/AAAA/CNAME 冲突，先核对旧服务用途再替换，不能让它继续指向 NAS 公网地址。

```bash
cf up -d cloudflared
cf logs --tail=100 cloudflared
```

预期 Tunnel 为 Healthy，日志出现成功注册连接；容器没有宿主端口映射。metrics 仅监听其容器内部 `127.0.0.1:2000`，不向外发布。

## 7. HTTPS、缓存和实际验收

1. Cloudflare 账户启用 MFA；两个 hostname 开启 HTTPS 重定向、最低 TLS 1.2，确认有效证书。Tunnel 到同一 NAS 应用最后一跳是 Docker bridge HTTP，无需关闭任何 TLS 证书校验。
2. 创建 Cache Rule：表达式 `(http.host in {"wealth.sinojelly.kdns.fr" "studyos.sinojelly.kdns.fr"})`，设置 **Bypass cache**。检查已有规则不会重新强制缓存这些站点的接口或资料。
3. 未登录 Access 的无痕窗口访问两个 HTTPS 域名，必须先验证成员；未授权邮箱必须拒绝。通过 Access 后仍需各应用自己的账号登录。
4. WealthSail 按 [数据库手册第 6 节](../docs/31.NAS独立PostgreSQL部署与验证操作手册.md#6-web-用户和租户验收) 验收管理员、邀请、用户和租户隔离。确认刷新后会话恢复、头像/导入/流式对话正常。
5. StudyOS 验收家长/学生登录、刷新、创建/修改操作（CSRF）、识别/批改和逐事件流式回复。会话 cookie 应为 Secure/HttpOnly；CSRF cookie 供前端读取，不要求 HttpOnly。大上传按实际 Cloudflare 请求限额拆分，不能假设原项目 1GiB 单批上传均能通过公网。
6. 分别用家庭 Wi-Fi 和手机蜂窝网络访问；两种网络都经过 Access。移除某成员的允许权限并撤销其 Access 会话，确认无法继续访问；应用内权限也按需撤销。
7. 查看 `ws ps`、`so ps`，不得出现 `宿主IP:端口->容器端口` 的发布（只显示内部 `80/tcp` 或 `8000/tcp` 正常）；检查路由器无相关映射，公网 IPv6 也不能绕过入口。完成 [完整安全验收清单](./SECURITY.md#nas-验收清单)。

未认证时 `curl -I https://wealth.sinojelly.kdns.fr/` 返回 Access 重定向/拒绝是预期，不应为得到 200 而关闭 Access。机器调用以后另行配置专用服务身份。

## 8. 可选：收紧现有 PostgreSQL/pgAdmin 管理端口

你已有的 PostgreSQL Compose 发布了 SQL 和 pgAdmin 宿主端口。建议只给 NAS 本机/SSH 管理，避免 LAN/连接器经 NAS IP 绕到管理面。此 overlay **仅改变端口绑定**；不修改数据库卷或共享网络，也不新建 PostgreSQL。

先确认其他应用使用 Docker 网络的 `postgres:5432`，没有依赖 `NAS-IP:5432`；确认现有项目名、卷名，在维护窗口重建这两个服务。切换会短暂中断共享数据库连接，不能对未知项目直接执行。

**先检查 Engine**：Docker Engine 28.0.0 之前，同一二层网络主机可能访问绑定到 localhost 的发布端口。旧 NAS 必须先升级，或配置主机防火墙阻断 LAN 到这些管理端口并从同网段验证；不能仅凭 `127.0.0.1` 绑定判定安全。两个应用默认完全不发布宿主端口，不依赖这一回环保护。[Docker 官方限制](https://docs.docker.com/engine/network/port-publishing/#publishing-ports)

```bash
cd /你的NAS应用目录/postgres
docker compose -f docker-compose.yaml \
  -f "$ACCESS_DIR/compose.postgres-local-only.yaml" config --quiet
docker compose -f docker-compose.yaml \
  -f "$ACCESS_DIR/compose.postgres-local-only.yaml" up -d postgres pgadmin
```

若原部署用了自定义 `-p`/`--env-file`，必须保留。改后 SQL 仅 `127.0.0.1:5432`、pgAdmin 仅 `127.0.0.1:5500`，应用间 Docker DNS 连接继续正常。可用 SSH 本地端口转发管理 pgAdmin；不要为管理页另设无保护公网路由。

## 9. 日常更新、停用和排错

后续 WealthSail 的 build、迁移、up 等命令均继续使用 **ws 的完整三文件前缀**，StudyOS 使用 so 前缀。省略接入 overlay 可能恢复原端口/丢失入口防护。保留 env、secret、数据卷和原项目名。

更新 cloudflared 时核对官方发布说明，将 `compose.yaml` 的版本改为审阅后的固定版本，再执行 `cf pull`、`cf up -d --force-recreate cloudflared`；记录 digest 并复验两站。无需自动跟随 `latest`。

暂停外网仅执行 `cf stop cloudflared`，应用和数据库继续存在。恢复用 `cf up -d cloudflared`。不要用 `down -v` 清理应用数据，也不要在应用仍连着时删除 edge 网络。发生 token 泄露按 [应急步骤](./SECURITY.md#紧急断开与-token-泄露) 轮换 token 并断开既有连接。

| 现象 | 检查 |
|---|---|
| `unknown tag !override` | Compose 未达到 2.24.4；更新插件 |
| network pool overlaps | 对比 Docker/LAN/VPN 子网，成组调整 subnet 和可信 IP；重建网络前先停依赖服务 |
| token permission denied | 宿主文件 owner 65532、mode 0400、父目录 NAS 管理员 0700，检查 NAS ACL |
| Tunnel 1033 / 无连接 | token、NAS 出站 DNS/7844、容器日志、后台 connector 状态 |
| 502 / origin 不可达 | 应用是否启动；edge 网络和 alias；目标必须是内部 DNS/端口 |
| Access 403 / 不断验证 | 成员策略、应用 AUD、team name、hostname 是否匹配；不要关闭 JWT 验证 |
| 证书错误 | zone Active、DNS 指向 Tunnel、证书包含该 hostname |
| StudyOS 写操作 403 / cookie 丢失 | Host、精确信任的连接器 IP、HTTPS 转发 scheme；不要改成信任 `*` |
| 413 / 大上传中断 | Cloudflare 套餐请求大小/时长及应用限制；WealthSail Nginx 为 21MiB |
| 流式结果最后一起显示 | MIME 应为 `text/event-stream`、禁止额外代理缓冲/缓存；确认 Access 会话未到期 |

## 10. 本次验证范围

本机无 Docker，已做 7 份 YAML（含 `!override`）静态解析、网络/IP/文件引用/secret 隔离检查及两个项目真实基础 Compose 的兼容性审查；13 个 Bash 代码块通过 `bash -n`。没有运行 Docker Compose 合并、拉取该镜像、`nginx -t` 或实际 Cloudflare 登录。NAS 上第 3–7 节命令和验收清单是上线前必做检查；控制台配置不会由这些本地文件自动创建。
