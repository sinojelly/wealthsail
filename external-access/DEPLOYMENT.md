# NAS 首次部署详细步骤

日期：2026-10-23。适用于你的现状：**NAS PostgreSQL 已部署；WealthSail 使用新空库；StudyOS 尚未部署；两个站点仅向指定成员开放。**

本指南按执行顺序展开，命令都在 **SSH 登录 NAS 后的 Bash 终端**执行，不是在 Windows PowerShell 执行。每个代码块逐段执行，当前步骤成功后再继续；构建或初始化报错时先处理报错。

## 一、先回答：顺序、文件名和启动命令

推荐顺序如下，StudyOS 可以另一天再部署：

| 顺序 | 在哪里做 | 做什么 | 完成后的状态 |
|---|---|---|---|
| 1 | NAS | 上传源码、确认已有 PostgreSQL、准备路径和配置 | PG 保持原样 |
| 2 | Cloudflare 控制台 | 创建两个 Access application、创建一个 Named Tunnel 并取 token | 域名访问策略已准备，Tunnel 尚未连接 |
| 3 | NAS | 拉取 cloudflared，执行 `create` | 两个 edge 网络已创建，连接器暂未运行 |
| 4 | NAS | 构建 WealthSail、建库/管理员/租户、启动应用 | 应用在 Docker 内可用 |
| 5 | NAS + Cloudflare | 启动 cloudflared，发布 WealthSail 的受保护路由 | WealthSail 域名可验收 |
| 6 | NAS + Cloudflare | 部署 StudyOS、创建家长、发布第二条路由 | 两个站点均可验收 |

**所有 Compose 文件都不需要重命名，也不要把多个文件内容复制到同一个文件。** `.yaml`、`.yml` 两种后缀均可，以下命令用 `-f` 指定现有文件名。

| 部署对象 | 一次命令需要的文件，按 `-f` 顺序 | 是否单独运行 |
|---|---|---|
| cloudflared | `WealthSail/external-access/compose.yaml` | 独立项目 |
| WealthSail | `WealthSail/docker-compose.yml` → `WealthSail/docker-compose.nas.yml` → `WealthSail/external-access/compose.wealthsail.yaml` | 三文件合并为一个应用项目 |
| StudyOS | `studyos/docker-compose.yml` → `WealthSail/external-access/compose.studyos.yaml` | 两文件合并为一个应用项目 |
| 已有 PostgreSQL | 你原有的 `postgres/docker-compose.yaml` | 已运行，不重新部署 |
| 可选 PG 管理端口收紧 | 原 PG Compose → `external-access/compose.postgres-local-only.yaml` | 独立维护操作，见文末 |

`compose.wealthsail.yaml`、`compose.studyos.yaml` 是补充配置，不能单独 `up`。**本方案不要直接执行不带 `-f` 的 `docker compose up -d`**：那可能漏掉数据库网络或外网安全配置。下面提供完整命令，不依赖 README 中的 `cf/ws/so` 函数。

## 二、准备 NAS 目录和变量

### 2.1 上传源码

将修改后的两个完整项目放在 NAS 本地磁盘。例如：

```text
/volume1/docker/
├── WealthSail/
│   ├── docker-compose.yml
│   ├── docker-compose.nas.yml
│   ├── backend/、frontend/、scripts/、migrations/、deployment/ 等
│   └── external-access/
│       ├── compose.yaml
│       ├── compose.wealthsail.yaml
│       ├── compose.studyos.yaml
│       ├── .env.example
│       └── nginx/wealthsail-security.conf.template
├── studyos/
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── Dockerfile
│   └── 其余源码
└── postgres/                   # 你已有的 PG 项目
    └── docker-compose.yaml
```

不能只上传 Compose 文件：两个应用需要源码构建镜像。上传时包含 `.env.example`、`.dockerignore` 等隐藏文件；不要把 Windows 开发用的 `.env`、`.venv`、`node_modules` 或私人资料当成 NAS 生产配置上传。

StudyOS 的 `volume`、`codex-runtime` 和 WealthSail 数据卷应保存在 NAS 本机磁盘；StudyOS 不使用跨主机 NFS/SMB 目录承载运行中的数据库。

### 2.2 只在这里改成自己的实际值

以下是 **NAS 路径**，不是 `D:\...` 路径。按实际挂载点修改前两行和 Codex 目录；管理员邮箱替换为真实邮箱：

```bash
WEALTH_ROOT='/volume1/docker/WealthSail'
STUDYOS_DIR='/volume1/docker/studyos'
STUDYOS_CODEX_SOURCE='/volume1/docker-private/studyos-codex'
ADMIN_EMAIL='你的真实登录邮箱@example.com'
ACCESS_DIR="$WEALTH_ROOT/external-access"

ls "$WEALTH_ROOT/docker-compose.yml" \
   "$WEALTH_ROOT/docker-compose.nas.yml" \
   "$ACCESS_DIR/compose.yaml"
docker version
docker compose version
```

`STUDYOS_CODEX_SOURCE` 放在源码目录之外，避免认证文件进入镜像构建上下文。如果 StudyOS 还没上传，可以先完成 WealthSail 部署，到第七节再准备它。

Docker Compose 必须至少 **2.24.4**，否则不能识别安全配置中的 `!override`；先升级 Compose，不要删掉该标记。当前 NAS 用户须已有 Docker 操作权限。以下保存 token 的命令使用 `sudo`；若你以 root 操作，去掉命令中的 `sudo` 即可。

这些变量只在当前 SSH shell 有效。**断开 SSH 后重新执行本节变量赋值**，不需要重新建库、生成密钥或创建账号。

### 2.3 确认 PostgreSQL 已运行

```bash
docker ps --filter name=postgres
docker exec postgres pg_isready -U admin -d postgres
docker network inspect global-postgres-network >/dev/null
```

预期 `pg_isready` 显示 `accepting connections`，网络检查无错误。这里不修改原 PG Compose、卷或其他应用的 `prismscore_db`。后面为 WealthSail 新建 `wealthsail` 库，管理员密码仅在初始化时隐藏输入。

### 2.4 准备外网参数文件

```bash
test -e "$ACCESS_DIR/.env" || cp "$ACCESS_DIR/.env.example" "$ACCESS_DIR/.env"
docker network ls -q | xargs -r docker network inspect \
  --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'
```

检查现有 Docker、LAN、VPN 网段，确认以下两组均不冲突；无冲突时 **`.env` 可以保持模板原值**：

| 用途 | 网络 | 子网 | 动态地址池 | cloudflared 固定 IP |
|---|---|---|---|---|
| WealthSail | `wealthsail-tunnel-edge` | `172.30.91.0/29` | `172.30.91.4/30` | `172.30.91.2` |
| StudyOS | `studyos-tunnel-edge` | `172.30.92.0/29` | `172.30.92.4/30` | `172.30.92.2` |

若冲突，用 NAS 编辑器修改 `external-access/.env` 中对应 subnet、IP_RANGE、连接器 IP；动态池须在子网内，连接器固定 IP 须在动态池外。不要只改 Compose 某一份配置。后面所有应用读取同一份外网 `.env`。

## 三、在 Cloudflare 准备 Access 和 Tunnel

### 3.1 创建两个 Access application

在 Cloudflare One / Zero Trust 找到 **Access → Applications → Add application → Self-hosted**，分别创建：

| 项目 | WealthSail | StudyOS |
|---|---|---|
| 应用名称 | WealthSail | StudyOS |
| Public hostname | `wealth.sinojelly.kdns.fr` | `studyos.sinojelly.kdns.fr` |
| Path | 留空，保护所有路径 | 留空，保护所有路径 |
| Allow policy | 指定成员邮箱/身份组，包含你的邮箱 | 指定成员邮箱/身份组，包含你的邮箱 |
| 登录方式 | 邮件一次性验证码或已有 IdP | 邮件一次性验证码或已有 IdP |
| 建议会话时长 | 8 小时 | 8 小时 |

未匹配成员默认拒绝；不要设 Everyone 或整站 Bypass。启用 Cloudflare 账户 MFA；若用 IdP，可在 IdP 强制 MFA。

记下你的 **team name**，以及每个 application 各自的 **AUD**，后面发布路由要用。Access 授权不自动创建 WealthSail/StudyOS 账号。

### 3.2 创建一个 Named Tunnel

在 Cloudflare One 的 **Networking / Networks → Tunnels / Connectors** 创建 cloudflared Tunnel，例如 `nas-apps`，选择 Docker 安装方式。

从控制台安装示例中复制 **token 的值**，不复制整条命令到 NAS 执行。采用本项目文件 secret，避免 token 进入命令参数和历史。

此时先保留安装连接器页面，**Tunnel 未连接/Inactive 是正常状态**。有些控制台向导要连接器运行后才能 Continue，先做下面 NAS 操作，到第六节连接成功后再继续向导。

先确认 Cloudflare 的 `sinojelly.kdns.fr` zone 已 Active。两个域名要有有效边缘证书；证书覆盖和 DNS 前提见 [设计说明](./DESIGN.md#5-域名和-tls-前置条件)。

### 3.3 把 token 隐藏写入 NAS 文件

```bash
cd "$ACCESS_DIR"
mkdir -p secrets
chmod 700 secrets
sudo -v
read -rsp '粘贴 Tunnel token，然后回车: ' cf_token
printf '\n'
printf '%s' "$cf_token" | sudo tee secrets/cloudflared-token >/dev/null
unset cf_token
sudo chown 65532:65532 secrets/cloudflared-token
sudo chmod 0400 secrets/cloudflared-token
```

输入 token 时屏幕不显示字符，这是预期。此段是首装步骤，日常重启不重复写入。父目录由当前 NAS 管理员拥有且为 `0700`，文件为容器 UID/GID `65532:65532`、`0400`；NAS ACL 也必须允许 Docker 正确挂载。不要改成 root 运行连接器来绕过权限错误。

## 四、先创建 cloudflared 和网络，暂不启动

### 4.1 校验和拉取

```bash
docker compose --env-file "$ACCESS_DIR/.env" \
  -f "$ACCESS_DIR/compose.yaml" config --quiet

docker compose --env-file "$ACCESS_DIR/.env" \
  -f "$ACCESS_DIR/compose.yaml" pull
```

`config --quiet` 成功时没有输出。镜像固定版本在 `compose.yaml` 中，一般不需要修改；拉取失败先解决 NAS 网络或镜像平台问题。

### 4.2 创建，不启动

```bash
docker compose --env-file "$ACCESS_DIR/.env" \
  -f "$ACCESS_DIR/compose.yaml" create cloudflared

docker network inspect wealthsail-tunnel-edge studyos-tunnel-edge \
  --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'
```

第二条使用默认网络名；若你改了 `.env` 的网络名，同步替换检查命令中的两个名称。此时项目 `wealthsail-external-access` 和两个网络存在，cloudflared 状态为 Created/未运行即可。

**这样已经满足 WealthSail/StudyOS 的 external 网络前提，不需要先让 Tunnel 在线。**

## 五、部署 WealthSail：生成配置 → 建库 → 启动

### 5.1 首次生成配置

以下是空库首装。检查这三个文件是否已经存在：`deployment/nas.env`、`deployment/env/api.env`、`deployment/env/worker.env`。已经生成过就保留它们，不删除重建；若已初始化过应用，使用文末“日常更新”，不要重复首次部署。

NAS 已安装 Python 3 时执行：

```bash
cd "$WEALTH_ROOT"
python3 scripts/configure_nas.py \
  --web-base-url https://wealth.sinojelly.kdns.fr
```

**NAS 没有 Python 时，改为执行下面这一个替代命令；两种方式二选一：**

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$WEALTH_ROOT:/workspace" -w /workspace python:3.12-slim \
  python scripts/configure_nas.py \
  --web-base-url https://wealth.sinojelly.kdns.fr
```

生成结果：

| 文件 | 谁使用 | 你是否需要手工填密码 |
|---|---|---|
| `deployment/nas.env` | Compose：PG 网络/管理员名等 | 不需要 |
| `deployment/env/api.env` | API：运行 DSN、登录/会话密钥、域名 | 不需要，自动生成 |
| `deployment/env/worker.env` | Worker：独立 DSN、任务参数 | 不需要，自动生成 |

默认已匹配现有 `postgres:5432`、`admin` 和 `global-postgres-network`。Web 域名也已写好。**不用复制 api/worker 的 `.env.example` 覆盖生成文件，不用手工填写 NAS PG 的 admin 密码。**

### 5.2 校验三份合并配置并构建镜像

```bash
docker compose --env-file "$WEALTH_ROOT/deployment/nas.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$WEALTH_ROOT/docker-compose.yml" \
  -f "$WEALTH_ROOT/docker-compose.nas.yml" \
  -f "$ACCESS_DIR/compose.wealthsail.yaml" config --quiet

docker compose --env-file "$WEALTH_ROOT/deployment/nas.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$WEALTH_ROOT/docker-compose.yml" \
  -f "$WEALTH_ROOT/docker-compose.nas.yml" \
  -f "$ACCESS_DIR/compose.wealthsail.yaml" build api frontend
```

这是完整源码构建，可能较慢。Worker 和 db-init 复用 API 的后端镜像，不单独建 PostgreSQL 镜像。

### 5.3 创建空库、管理员和首个租户

确认第二节的 `ADMIN_EMAIL` 已改为你的真实邮箱：

```bash
docker compose --env-file "$WEALTH_ROOT/deployment/nas.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$WEALTH_ROOT/docker-compose.yml" \
  -f "$WEALTH_ROOT/docker-compose.nas.yml" \
  -f "$ACCESS_DIR/compose.wealthsail.yaml" \
  run --rm --no-deps db-init \
  --confirm-target postgres:5432/wealthsail \
  --bootstrap-email "$ADMIN_EMAIL" \
  --nickname 管理员 \
  --tenant-name 我的投研工作台
```

按提示依次隐藏输入：NAS PostgreSQL `admin` 密码、新 Web 管理员密码、再次确认 Web 密码。Web 密码至少 12 字符、最多 72 UTF-8 字节。昵称和租户名称可以换成你喜欢的名称；含空格时用引号包住。

成功时应包含 `READBACK OK`、`Ready: postgres:5432/wealthsail`、`Web administrator created.` 等信息。

这一步执行完整正式 migrations，自动处理首装近似穿透数据，不执行 `seeds/dev`；只创建你指定的管理员和首个租户，不创建测试账号、演示账户或持仓。公开基金/股票等基础目录保留。**不需要手工挑 migration、导入 SQL 或连接 Supabase。**

迁移/权限 readback 失败时停在这一步，不要继续启动，也不要删账本、跳 migration 或给运行角色授予超级用户。

### 5.4 启动三个常驻服务

```bash
docker compose --env-file "$WEALTH_ROOT/deployment/nas.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$WEALTH_ROOT/docker-compose.yml" \
  -f "$WEALTH_ROOT/docker-compose.nas.yml" \
  -f "$ACCESS_DIR/compose.wealthsail.yaml" \
  up -d --wait --wait-timeout 180 api worker frontend
```

`db-init` 是一次性工具，上一条命令结束后自动删除，不是需要常驻启动的第四个服务。若 NAS 启动较慢，先查日志，再视情况增加等待时间。

### 5.5 在 Docker 内检查

```bash
docker compose --env-file "$WEALTH_ROOT/deployment/nas.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$WEALTH_ROOT/docker-compose.yml" \
  -f "$WEALTH_ROOT/docker-compose.nas.yml" \
  -f "$ACCESS_DIR/compose.wealthsail.yaml" ps

docker compose --env-file "$WEALTH_ROOT/deployment/nas.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$WEALTH_ROOT/docker-compose.yml" \
  -f "$WEALTH_ROOT/docker-compose.nas.yml" \
  -f "$ACCESS_DIR/compose.wealthsail.yaml" exec -T frontend nginx -t
```

预期 API healthy，Worker/Frontend running，Nginx 显示配置测试成功。端口列显示内部 `80/tcp`、`8000/tcp` 正常，**不能出现宿主端口 `IP:端口->容器端口`**。

当前不通过 `http://NAS-IP:8080` 访问：应用不发布宿主端口。接下来用域名验收。

## 六、启动 Tunnel，让 WealthSail 先上线

### 6.1 启动连接器

```bash
docker compose --env-file "$ACCESS_DIR/.env" \
  -f "$ACCESS_DIR/compose.yaml" up -d cloudflared

docker compose --env-file "$ACCESS_DIR/.env" \
  -f "$ACCESS_DIR/compose.yaml" logs --tail=100 cloudflared
```

回到 Cloudflare，确认 Connector 已连接、Tunnel Healthy。安装向导若还在等待，现在点 Continue。

### 6.2 只发布 WealthSail 路由

在该 Tunnel 的 **Published application routes / Public Hostnames** 中新增：

| 字段 | 值 |
|---|---|
| Hostname | `wealth.sinojelly.kdns.fr` |
| Service type | HTTP |
| URL | `wealthsail-origin:80` |
| HTTP Host Header | `wealth.sinojelly.kdns.fr` |
| Protect with Access | 开启 |
| Team name | 第三节记下的团队名称 |
| AUD | WealthSail 的 Access application AUD |

如果 UI 将 hostname 分开填写，Subdomain 填 `wealth`，Domain 选 `sinojelly.kdns.fr`；Path 留空。若 Service 只有一个字段，则填写 `http://wealthsail-origin:80`。

这个目标是 Docker 网络里的入口，不能改成 `localhost`、NAS IP、API 的 8000 端口或数据库地址。StudyOS 未部署时先不加它的路由。

DNS 应由 Tunnel 流程创建/关联。已有同名 A/AAAA/CNAME 时先确认旧用途再替换，不保留指向 NAS 公网 IP 的直连记录。Cloudflare 控制台之外，不需要改本地 Compose 的域名路由，也不需要创建本地 ingress `config.yml`。

### 6.3 配置 HTTPS 与缓存后验收

- 确认该 hostname 证书有效，启用 HTTPS 重定向、最低 TLS 1.2。
- Cloudflare Cache Rule 对两个 hostname 设置 **Bypass cache**。表达式可用 `(http.host in {"wealth.sinojelly.kdns.fr" "studyos.sinojelly.kdns.fr"})`，确认其他规则不会重新强制缓存它们。
- 不在路由器上开入站 80/443/8000/8080/5432，也不额外部署 NAS 公网反向代理。

浏览器无痕打开 `https://wealth.sinojelly.kdns.fr`：先通过指定成员 Access 验证，再用第五节建立的邮箱/密码登录 WealthSail。刷新应能恢复会话，首个租户和管理员能力可见，初始账户/资产为空。

再用未授权邮箱确认被 Access 拒绝。Wi-Fi 和手机蜂窝网络各试一次。WealthSail 到这里可以先使用，**不用等待 StudyOS**。

## 七、最后部署 StudyOS，并加入同一个 Tunnel

### 7.1 准备原项目配置和 Codex 目录

```bash
ls "$STUDYOS_DIR/docker-compose.yml" "$STUDYOS_DIR/.env.example"
test -e "$STUDYOS_DIR/.env" || cp "$STUDYOS_DIR/.env.example" "$STUDYOS_DIR/.env"
mkdir -p "$STUDYOS_CODEX_SOURCE"
chmod 700 "$STUDYOS_CODEX_SOURCE"
```

用 NAS 编辑器打开 **StudyOS 自己的 `.env`**，将 `CODEX_HOME_DIR` 改为第二节的实际 Codex 目录，例如：

```dotenv
CODEX_HOME_DIR=/volume1/docker-private/studyos-codex
```

不能保留模板的 Windows 路径。这个目录必须已经存在，可以使用你在 NAS 上已有的 Codex 认证目录，也可以先是空目录，容器启动后再完成设备登录。

其他参数保留原值即可：精确 Host 和代理信任由外网 overlay 覆盖，不用修改 StudyOS 原 Compose，也不要改成信任所有代理。不要把 StudyOS `.env` 和 `external-access/.env` 合并，它们用途不同。

### 7.2 校验、构建、启动

```bash
docker compose --env-file "$STUDYOS_DIR/.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$STUDYOS_DIR/docker-compose.yml" \
  -f "$ACCESS_DIR/compose.studyos.yaml" config --quiet

docker compose --env-file "$STUDYOS_DIR/.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$STUDYOS_DIR/docker-compose.yml" \
  -f "$ACCESS_DIR/compose.studyos.yaml" build studyos

docker compose --env-file "$STUDYOS_DIR/.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$STUDYOS_DIR/docker-compose.yml" \
  -f "$ACCESS_DIR/compose.studyos.yaml" \
  up -d --wait --wait-timeout 180 studyos
```

`--wait` 等到原应用健康检查通过再继续，避免首次迁移尚未完成就创建家长。第一份 `-f` 始终是 StudyOS 原文件，保证 `./volume` 和构建路径指向 StudyOS，不能交换顺序。

### 7.3 创建首位家长

```bash
docker compose --env-file "$STUDYOS_DIR/.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$STUDYOS_DIR/docker-compose.yml" \
  -f "$ACCESS_DIR/compose.studyos.yaml" \
  exec -it studyos python /app/scripts/bootstrap_container.py
```

交互输入家长用户名、显示名、家庭名称和密码。已有家庭会保留，不重置账号。这个步骤在可信容器终端执行，不在公网开放首次家长创建接口。

### 7.4 确认 AI 认证

家庭登录账号与 Codex 认证不同。若 NAS 尚无可用 Codex 认证，可按 StudyOS 项目支持的设备方式登录：

```bash
docker compose --env-file "$STUDYOS_DIR/.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$STUDYOS_DIR/docker-compose.yml" \
  -f "$ACCESS_DIR/compose.studyos.yaml" \
  exec -it studyos codex login --device-auth

docker compose --env-file "$STUDYOS_DIR/.env" \
  --env-file "$ACCESS_DIR/.env" \
  -f "$STUDYOS_DIR/docker-compose.yml" \
  -f "$ACCESS_DIR/compose.studyos.yaml" \
  exec -T studyos codex login status
```

按终端给出的官方页面和设备码在浏览器完成登录，不把设备码或认证文件发给别人。认证保存在独立 `codex-runtime` 卷，重启保留。原项目认证配置和 AI 功能依赖以 StudyOS README/部署指南为准；Web 可用不代表 AI 认证已完成。不要为了重新登录执行 `down -v`。

### 7.5 在同一 Tunnel 增加 StudyOS 路由

| 字段 | 值 |
|---|---|
| Hostname | `studyos.sinojelly.kdns.fr` |
| Service type | HTTP |
| URL | `studyos-origin:8000` |
| HTTP Host Header | `studyos.sinojelly.kdns.fr` |
| Protect with Access | 开启 |
| Team name | 同一个团队名称 |
| AUD | **StudyOS 自己的** Access application AUD |

Path 留空；单 Service URL 字段填写 `http://studyos-origin:8000`。远程管理 Tunnel 会自动同步新路由，**不需要再建一个 cloudflared，不需要因此重启 WealthSail 或连接器**。

浏览器打开 `https://studyos.sinojelly.kdns.fr/v4/`，通过 Access 后使用家长账号登录。验证刷新、学生账号、资料上传、写操作和 AI 识别/批改/讨论；大上传受实际 Cloudflare 请求限额约束，按需拆分。

## 八、到底需要改哪些文件

| 文件/位置 | 首装动作 |
|---|---|
| 本文第二节 | 替换 NAS 绝对路径和管理员邮箱；只是当前 shell 变量 |
| `external-access/.env` | 由 `.env.example` 复制；无网段冲突则保持模板值 |
| `external-access/secrets/cloudflared-token` | 第三节隐藏写入真实 Tunnel token，设置权限 |
| `deployment/nas.env`、`deployment/env/api.env`、`deployment/env/worker.env` | 第五节生成一次，保留密钥，通常不手改 |
| `studyos/.env` | 从其自身模板复制，填 NAS 的 `CODEX_HOME_DIR` |
| Cloudflare 控制台 | 两个 Access app、一个 Tunnel、两条精确路由、各自 AUD、HTTPS 和缓存规则 |
| 所有 `.yml`/`.yaml` | 默认不改名、不移动、不合并；按本文完整 `-f` 顺序使用 |

两个应用的配置都使用“自身 env 在前，外网 env 在后”。不要把外网配置复制进 StudyOS 目录替代原项目，也不要通过 shell 中的同名 export 意外覆盖 env 值。

如果之前已经用自定义 `docker compose -p 项目名` 部署过应用，本指南对应的 **所有**应用 Compose 命令都要保留相同的 `-p`，避免创建另一套命名卷。首次按本文默认名称部署后保持路径/项目名稳定。

## 九、后续查看、重启和更新

规则：**保留对应应用的全部 `--env-file` 和 `-f` 前缀，只替换命令末尾的动作。**

| 目的 | 对应完整前缀后的动作 |
|---|---|
| 查看状态 | `ps` |
| 查看日志 | `logs --tail=100 服务名` |
| 停止指定服务 | `stop 服务名` |
| 原配置停止后恢复 | `up -d 服务名` |
| 修改 env/Compose 后应用 | `up -d --force-recreate 服务名` |

例如，临时停止所有外网入口：

```bash
docker compose --env-file "$ACCESS_DIR/.env" \
  -f "$ACCESS_DIR/compose.yaml" stop cloudflared
```

恢复仍使用第六节 `up -d cloudflared`。只停连接器不会删除数据库、家庭资料或应用账号；WAN/LAN 域名入口会同时暂停。

WealthSail 更新需先备份，再以第五节的三文件完整前缀执行：

1. `build api frontend`
2. `run --rm --no-deps db-init --confirm-target postgres:5432/wealthsail`（升级不再传 `--bootstrap-email`）
3. `up -d --force-recreate api worker frontend`

保留生成 env，不重新初始化身份密钥。完整数据库、租户验收和备份说明见 [NAS 数据库操作手册](../docs/31.NAS独立PostgreSQL部署与验证操作手册.md)。

StudyOS 更新先备份 `volume`、`codex-runtime` 及配置，再以其双文件前缀 `build studyos`、`up -d --wait studyos`。后续版本的迁移和备份要求同时遵循 StudyOS 自身文档。

## 十、上线前检查和失败时看哪里

- 两站新无痕窗口先进入 Access；未授权成员不能访问，通过 Access 后仍需应用登录。
- WealthSail 登录/刷新/登出、成员邀请、租户隔离正常；StudyOS 家长/学生、写操作和 AI 功能按实际认证验证。
- 两应用无宿主发布端口；NAS 的公网 IPv4/IPv6、LAN 直连均不能绕过入口。NAS 仍需正常主机防火墙和受限管理权限。
- 完成 [SECURITY.md 安全验收清单](./SECURITY.md#nas-验收清单)，包括 HTTPS、缓存、会话、SSE、上传、日志、重启和撤销成员。
- PG/pgAdmin 不经 Tunnel 发布。原 PG 端口收紧是独立可选维护项，参见 [README 第 8 节](./README.md#8-可选收紧现有-postgresqlpgadmin-管理端口)。`compose.postgres-local-only.yaml` 只能与原 PG Compose 合并，不能加到 WealthSail/StudyOS 命令中；旧 Engine 回环旁路及共享服务影响须先核实。

| 错误/现象 | 处理方向 |
|---|---|
| `unknown tag !override` | 升级 Compose 至 2.24.4+，不删除该标记 |
| edge network not found | 第四节 `create` 是否成功；是否使用同一外网 `.env` |
| 网络地址冲突 | 子网、动态池、固定 IP 成组修改；核实 LAN/VPN/Docker 网络 |
| `configuration exists; preserve its secrets` | 已有生成配置；保留它们，从校验/构建继续，不删除密钥 |
| db-init 失败 | 看具体迁移/角色/连接报错；不跳迁移、不修改其他业务库 |
| up 等待超时 | 保持同一完整前缀执行 `ps` 和 `logs --tail=100 服务名`，确认健康检查和启动错误 |
| token permission denied | 检查宿主 UID/GID、0400、父目录权限和 NAS ACL |
| Tunnel 一直 Inactive | cloudflared 是否已 `up`；NAS 出站 DNS/HTTPS/7844、token 和容器日志 |
| 域名 502 | 应用是否运行；内部 alias/端口和 edge 网络是否一致，不填 localhost |
| Access 403/不断重定向 | 成员策略、team、该站 AUD、证书及 Host；不关闭 JWT 验证 |
| StudyOS CSRF 403/会话丢失 | 精确代理 IP、外部 HTTPS/Host；不改成信任全部代理 |

**本指南验证范围：**本机没有 Docker。文档命令可做 Bash 语法、参数/文件引用检查，不能代替 NAS 的镜像构建、`docker compose config --quiet`、容器启动、`nginx -t` 或真实域名验收。这里没有自动操作你的 NAS 或 Cloudflare 账户。

官方参考：[Cloudflare Named Tunnel 创建流程](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)、[连接器 Access 校验](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/#access)、[Compose 多文件合并](https://docs.docker.com/reference/compose-file/merge/)。
