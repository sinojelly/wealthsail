# NAS 环境配置交接与暂停记录（2026-10-24）

本文件供下一次 AI 或维护人员继续部署使用。用户要求：写完交接文档后暂停，**本轮不再继续构建、初始化数据库、启动应用或配置公网入口**。

**当前整体状态：部署准备已完成一部分，实际镜像构建失败，系统尚未上线。** WealthSail、StudyOS、Codex 和 cloudflared 都不能按“部署完成”处理。两轮已知后台构建均已结束失败，不是仍在构建。本文依据本地操作记录和已返回的 NAS 结果整理；编写本文时未重新连接 NAS，也未重新探测 Cloudflare。

## 1. 最终要实现什么

- 使用 NAS 已有的 PostgreSQL，为 WealthSail 创建独立空库及应用角色，不再连接 Supabase；只创建首位管理员和初始租户，不导入开发测试账号、演示租户或业务测试数据。
- 保持 Web 登录、刷新后会话恢复、用户管理、租户隔离及日常操作体验；需要在部署后实际验收。
- WealthSail 即时聊天、常规报告中的模型调用和每日研究统一使用 Codex，不再走 `MODEL_API_KEY` 通道。
- WealthSail 与 StudyOS 分开保存 Codex 认证和运行数据；不直接使用或复制本机桌面 Codex 的私人认证。
- 使用一个 Cloudflare Named Tunnel 和一个常驻 cloudflared 容器，为两个站点提供外网访问：

| 域名 | Tunnel 的 Docker 内部目标 | 访问方式 |
| --- | --- | --- |
| `wealth.sinojelly.kdns.fr` | `http://wealthsail-origin:80` | Cloudflare Access 指定成员验证，再登录 WealthSail |
| `studyos.sinojelly.kdns.fr` | `http://studyos-origin:8000` | Cloudflare Access 指定成员验证，再登录 StudyOS |

- LAN 和外网都通过 HTTPS 域名访问；不公开 PostgreSQL、Codex Runner、NAS 管理面或应用宿主端口。
- 按用户指定的四个持久化根目录挂载数据，源码目录与数据目录分离；不影响 NAS 其他容器和数据库。
- 保留真实命令、执行原因、结果和维护方法；每次完成代码修改后先验证再做本地 Git 提交，默认不推送远端。

## 2. 阅读顺序与资料位置

本文是**实际进度快照**，优先用它判断做到了哪一步。其他指南中的命令是操作说明，不代表已经执行。

| 文件 | 用途 |
| --- | --- |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | 首次部署的完整顺序和命令；需要叠加下面的存储配置，且实际 NAS 应使用私有新版 Compose |
| [NAS-STORAGE.md](./NAS-STORAGE.md) | 四个数据根目录、子目录权限、完整 Compose 文件组合及卷冲突边界 |
| [CLOUDFLARE-SETUP-NOTES.md](./CLOUDFLARE-SETUP-NOTES.md) | Cloudflare 已操作内容、待办及受限 API 备选方式 |
| [DESIGN.md](./DESIGN.md)、[SECURITY.md](./SECURITY.md) | Tunnel、网络、安全设计和上线验收 |
| [数据库设计](../docs/30.NAS独立PostgreSQL部署与身份租户设计.md)、[数据库操作手册](../docs/31.NAS独立PostgreSQL部署与验证操作手册.md) | 自建身份、租户、空库安装和迁移边界 |
| [Codex 指南](../docs/32.Codex统一模型与NAS认证操作指南.md) | Runner、设备认证、认证文件备用导入及模型检查 |
| [数据库任务清单](../ai-review/50.NAS独立PostgreSQL实施计划与任务清单.md)、[外网任务清单](../ai-review/51.NAS外网接入实施计划与任务清单.md)、[Codex 任务清单](../ai-review/52.Codex统一模型与NAS认证实施计划及任务清单.md) | 代码方案与验证计划 |

本机私密入口是 `external-access/nas.env`，已被 Git 忽略。它包含 NAS 登录信息、源码路径、持久化路径及应用初装参数。**不要把它的内容打印、粘进交接文档、加入 Git 或直接作为 Compose 的 env 文件。** 文件曾使用 GB18030 编码，临时读取工具兼容 UTF-8/GB18030；不要用 shell `source` 执行其内容。

需要保留原有参数名中的拼写 `NAS_VOLUME_*`，不要擅自改成 `VOLUME`。真实地址、账号、目录和密码均从私密参数读取，本文只记录变量名。

## 3. 已完成的代码与 Git 状态

自建 PostgreSQL、身份与租户、外网安全配置以及统一 Codex 的代码已在此前实现并提交。最近的相关本地提交为：

| 提交 | 内容 |
| --- | --- |
| `10d8a5d` | Cloudflare Tunnel 和受保护的两站接入配置 |
| `a07f464` | NAS 安装与安全外网访问说明 |
| `b197958` | 全部模型调用改走隔离的 Codex 运行通道 |
| `9aa9d11` | Codex Runner、认证工具、镜像和 NAS 配置生成 |
| `d248283` | Codex NAS 认证文档和验证后提交规则 |
| `418b424` | 按用户数据目录增加三份存储 overlay |

本次交接文档另做本地提交，提交号以 `git log -1` 为准。**没有执行远端推送。** NAS 的 WealthSail 最后确认停在 `418b424`，不会自动获得本机之后的文档提交；继续修改代码后需明确同步方式，不能假定 NAS 执行 `git pull` 就能拿到尚未推送的本地提交。

本机分支最后确认是 `master`。`external-access/DEPLOYMENT.md` 有用户原有的未提交路径/邮箱修改，已保留，**未混入助手提交**。后续提交前继续检查差异，不要把这些个人部署参数提交进去。本机 StudyOS 在相邻仓库，仅做了只读检查和 bundle 导出，没有修改其本机代码。

前序代码验证记录：后端 3360 通过、142 跳过，前端 355 通过，相关 Ruff 和文档门禁通过。这是此前提交的本地验证结果，不能替代本轮 NAS 构建、Codex 实际认证和浏览器验收。本次仅新增/修订文档，不重跑整套应用测试。

## 4. NAS 已确认环境与已执行操作

### 4.1 登录和现有服务

- **正确 SSH 端口见私密参数 `NAS_SSH_PORT`**，已成功密码登录。此前默认端口及用户最初提供的端口均不正确；不要继续尝试这些错误端口。
- NAS 为 x86_64 Linux，内存约 15 GiB；Docker 数据盘最后检查约有 269 GiB 可用。
- Docker Engine 为 `29.4.0`，系统 Compose 为 `2.21.0`，旧版不能支持本项目使用的 `!override`。
- 现有 `postgres` 容器使用 `postgres:18-bookworm`，管理角色 `admin`，数据库服务可用，已有外部网络 `global-postgres-network`。
- NAS 有其他在用容器和其他业务数据库。本轮没有重建、停止、删除或清理它们，也没有修改全局 Docker daemon 配置或代理服务。
- 最后一次只读检查确认目标数据库 `wealthsail` 不存在；数据库安装器尚未执行。
- 登录用户可 sudo，但不在 Docker 组。后续 Docker 操作用受控 sudo，不必为了本任务修改系统用户组。

Windows 原生 `ssh-keyscan` 曾报密钥交换算法不支持；临时 Paramiko 工具已经连接成功并固定正确私密端口的 ed25519 主机密钥。后续连接使用拒绝未知主机密钥策略。不要为消除连接错误关闭密钥校验；密钥变化时先核验。

### 4.2 已完成的准备工作

| 项目 | 实际结果 |
| --- | --- |
| 源码更新 | 检查干净工作区、固定原 HEAD 和祖先关系后，通过本地 Git bundle 做 `--ff-only` 更新；没有 reset/clean |
| WealthSail NAS 版本 | 已更新到 `418b424da8e72ab2655596f4c61bf207d79c4c82` |
| StudyOS NAS 版本 | 已从 `ea3c5a1…` 更新到 `3197df683301d28641df6807a1331ba9f0de4179` |
| 持久化目录 | 已在用户指定四个根目录下创建本轮所需目录；头像 UID/GID 10001，WealthSail Codex auth 10001/0700，StudyOS runtime root/0700 |
| 路径边界复核 | 最后一轮检查中，9 个持久化子目录的 realpath、非符号链接和目录断言通过 |
| 私有 Compose | 已安装官方 `v5.6.1`，下载及 NAS 安装时校验 SHA256；未替换系统 Compose |
| 私有 Buildx | 已安装官方 `v0.38.1`，校验 SHA256，NAS 版本检查通过；未替换系统插件 |
| WealthSail 配置 | 已生成 `deployment/nas.env`、`deployment/env/api.env`、`worker.env`、`codex.env` |
| 外网/存储参数 | 已生成 NAS `external-access/.env` 和 `external-access/storage.env`；没有 Tunnel token 和 Access 的真实参数 |
| StudyOS 配置 | 已生成 NAS StudyOS `.env`，使用独立 Codex source/runtime 目录，限制 Host 与可信转发 IP |
| 应用初装凭据 | 已生成缺失的独立应用密码并仅追加到本机被忽略的 `nas.env`；账号尚未在应用中创建 |
| Compose 合并 | WealthSail 四文件、StudyOS 三文件组合的真实 `config --quiet` 均通过 |
| 网络预检 | 两个 edge 子网与现有 Docker 网络/主机路由未发现冲突；这不代表 edge 网络已创建 |

私有工具都在 `${NAS_VOLUME_CLOUDFLARED}/tools` 下：

- `docker-compose-v5.6.1`：经校验的 Compose 二进制。
- `docker-client/cli-plugins/docker-buildx`：经校验的 Buildx。
- `docker-client/config.json`：本次专用 Docker 客户端配置。
- `deploy.json`：本次应用目录及工具位置记录。
- `nas-compose`：本次命令包装器，固定项目名、文件顺序、env 文件，并设置专用 `DOCKER_CONFIG`。

包装器的项目名分别为 `wealthsail`、`studyos`、`wealthsail-external-access`。后续不要切换项目名，否则可能得到另一套空卷。直接调用系统 `docker compose` 或默认客户端的 `docker buildx`，不会自动使用这套私有工具。

### 4.3 实际执行过的关键命令与原因

下表将真实操作中的个人目录替换为变量。准备脚本还经 SFTP 上传私密配置和 bundle，并通过 Python 安全创建目录、校验哈希、写新配置；不能把这张表当成可从头重复执行的脚本。

| 已执行命令/操作 | 为什么执行及结果 |
| --- | --- |
| `id`、`uname -srm`、`df -hP` | 核对身份、架构和空间 |
| `docker version`、`docker compose version`、`docker ps -a` | 判断版本兼容性，盘点已有服务，避免误操作 |
| `docker network ls`、`docker network inspect`、`ip -j route` | 检查两站 edge 地址段是否冲突 |
| `realpath`、`stat`、Python 路径断言 | 核对源码/数据根目录与子目录权限、真实位置 |
| `git status --porcelain`、`git bundle verify`、`git fetch`、`git merge --ff-only <固定提交>` | 安全更新两套已确认干净的源码，不覆盖 NAS 修改 |
| 官方 CLI SHA256 校验、私有工具安装和版本检查 | 解决旧 Compose/Buildx 兼容性，不修改系统插件 |
| `nas-compose ws config --quiet`、`nas-compose so config --quiet` | 校验真实多文件合并；避免打印展开后的秘密 |
| `docker exec postgres psql -U admin -d postgres -Atc "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='wealthsail')"` | 只读确认安装目标尚不存在，没有执行建库或迁移 |
| `nas-compose ws build api frontend`、`nas-compose so build studyos` | 两轮后台尝试构建，均失败；日志和退出码保留 |
| 受控读取 Docker info 中的镜像/代理字段，探测官方仓库、代理 TCP | 排查 metadata 失败；不打印 registry 登录密码或代理凭据 |

本机实际通过 `.tmp/nas_ops.py` 的 Paramiko Session 执行，NAS 登录/sudo 密码只经 SSH 认证或 sudo stdin 传入，不放命令行参数；应用初装参数经 SFTP 写入私密 JSON。命令、原因和返回结果保存在被忽略的 `.tmp/nas-operations-redacted.jsonl`。该文件虽做了脱敏，仍按私密运维资料保管，不整份提交 Git。脱敏器可能连普通用户名子串一起替换，日志中的命令文件名因此可能失真；实际 Python 模块应以仓库源码为准，例如 `scripts.codex_admin`。

## 5. 遇到的问题、解决情况与当前阻断

| 问题 | 已知结论/处理 | 当前状态 |
| --- | --- | --- |
| SSH refused / Authentication failed | 最初端口不正确；用户更正私密参数后已连接成功 | 已解决；不要重复试错密码 |
| Windows SSH 工具 KEX 报错 | 使用临时 Paramiko 4.0.0 并固定主机密钥 | 已解决当前连接方式 |
| NAS 系统 Compose 过旧 | 安装私有 Compose v5.6.1 | 已解决本任务 CLI 兼容性 |
| 准备脚本末尾 `IPAM.Config=None` 导致 TypeError | 源码更新、目录创建、配置及合并验证已在报错前完成；后续网络预检按空列表处理并通过 | 已绕开；不能因准备脚本非零退出而认定此前动作全部回滚 |
| 第一轮构建提示需要 Buildx >= 0.17 | 安装私有 Buildx v0.38.1 | 已解决此项；原失败日志保留 |
| 第二轮基础镜像 metadata `not found` | Python、Node、Nginx 多个基础标签均失败，尚未完成应用镜像构建 | **当前阻断**；不能直接认定这些官方标签都不存在 |
| NAS 已配置 Docker 代理，但探测不可达 | 最后远程探测明确返回代理 TCP 不可达/OSError；官方 registry/auth 的默认网络探测均返回 URLError，未确认具体原因 | **当前阻断**；并非简单的“完全未配代理” |
| 国内镜像地址 | 已查阅 DaoCloud 官方 public-image-mirror 说明，准备了探测脚本 | **尚未取得 NAS 成功探测/拉取证据，未修改 Dockerfile/Compose** |
| Cloudflare 免费开通页空白 | 用户已自行完成 Zero Trust Free 激活 | 用户确认完成；助手尚未复核组织/team name |
| 浏览器/工具基础设施超时 | Cloudflare 页面读取和自动权限审核多次超时；部分代理任务报 workspace routing discovery timeout | 属于操作工具故障，不是已确认的 Cloudflare/NAS 应用故障 |

### 国内镜像的后续要求

用户已明确要求考虑国内镜像源。下一位 AI 应优先解决镜像拉取，**本轮尚未落地这项修改**。

最后一次只读结果还显示：daemon 已有多个第三方 mirror，default builder 使用 docker driver、BuildKit v0.29.0。没有取得官方 token/manifest 的成功结果，不能把代理或加速链路返回的 `not found` 直接当成官方仓库结论。

1. 先检查现有 daemon 的镜像加速、代理和客户端/Builder 实际配置，输出时隐藏代理凭据；不要直接修改 NAS 全局 daemon 或重启共享 Docker。
2. 可候选验证 DaoCloud 的 `m.daocloud.io/docker.io/library/<镜像>:<版本>` 或 `docker.m.daocloud.io/library/<镜像>:<版本>`。这些只是待测地址，不能据文档断言 NAS 可用。[供应方说明](https://github.com/DaoCloud/public-image-mirror)
3. 在 NAS 对实际需要的 Node、Python、Nginx、cloudflared 逐个验证平台、manifest/拉取和 digest，再选择项目级可配置方案，保留官方源选项。
4. 镜像仓库与构建中的 npm/pnpm、pip、apt 网络是不同环节。解决基础镜像后还要观察依赖下载；不要宣称换镜像就能修复所有网络问题。
5. 不使用来历不明的重打包镜像，不关闭 TLS 校验。若需新增 Dockerfile ARG、overlay 或依赖源参数，先验证再分功能提交 Git，并将新提交安全同步到 NAS。

最后可确认的远程日志记录在 2026-10-24 22:56 左右（北京时间）。本机后来已有 `nas_china_registry_probe*.py`，**没有找到该国内探测已成功执行的结果**，不要只凭脚本存在把它记作完成。

## 6. Cloudflare 已完成与尚未完成

已完成：用户在应用内浏览器登录；助手确认目标域名位于当前账户、由 Cloudflare 托管；进入免费开通流程。用户随后明确回复“已激活 Zero Trust Free”。没有选择付费计划。

尚未完成：

- [ ] 复核 Zero Trust 组织和实际 team name。
- [ ] 添加/确认适合指定成员的身份方式；若使用邮箱验证码，需要明确配置 One-time PIN，不能假定新组织默认已启用。
- [ ] 两个精确 hostname 的 Self-hosted Access 应用和允许名单；初始拟使用私密参数中的管理员邮箱，其他成员名单待用户补充。
- [ ] 两站各自的 AUD；默认拒绝、无 Everyone/Bypass，会话建议 8 小时。
- [ ] Named Tunnel（建议名 `nas-apps`）、连接 token 安全落盘及权限设置。
- [ ] 两条路由的连接器侧 Access 验证，team name 和各自 AUD 正确对应。
- [ ] DNS 路由、证书覆盖、HTTPS 重定向、最低 TLS 1.2、两站缓存 Bypass。
- [ ] NAS 健康后启动 cloudflared，验收 LAN/WAN、授权和拒绝访问。

**尚未创建 Tunnel、Access 应用或公网路由；没有可用的 Tunnel token。** 不能填假 token 启动，也不能为了先看到页面而临时开放无 Access 的入口。浏览器会话在下一轮未必仍可复用，恢复时重新盘点页面状态，避免重复创建资源；如需用户完成交互，准备具体配置后再请求。

## 7. 下一轮建议执行顺序

用户明确恢复工作后，再执行以下步骤。优先恢复状态盘点，不从零重跑准备脚本。

1. **读取交接与私密参数，做只读复核。** 确认正确 SSH 端口、主机密钥、两套 Git HEAD/工作区、持久目录/卷、现有数据库、私有 CLI、构建退出结果。保留所有现有 env/密钥；如果目标库或账号已经出现，先调查，不再按空库流程覆盖。
2. **修复项目构建网络。** 完成国内 registry 实测或恢复有效代理；任何共享代理/daemon 修改先评估其他服务。只在源码需要时修改、验证并提交，再同步固定提交。
3. **构建镜像。** WealthSail backend/frontend、StudyOS，以及 cloudflared 拉取；保留分轮日志，确认成功退出和镜像 digest。继续检查 npm/pip/apt 阶段。
4. **准备 Cloudflare Access 和 Named Tunnel。** 可与本地应用准备并行，公网路由最后发布；将 team/AUD/token 写入私密运行文件，避免写入 Git、聊天、命令历史或日志。
5. **准备 edge 网络但不公开服务。** 原方案通过 cloudflared Compose 的 `create` 建立网络/容器而暂不启动，前提是真实 token 文件和权限已经就绪；当前不能跳过此前提。
6. **WealthSail 空库安装。** 使用项目安装器 `db-init`，确认精确目标 `postgres:5432/wealthsail`，创建独立权限角色、管理员及初始租户（拟“我的投研工作台”）。不要手工批量导入整个 migrations 目录或开发 seed。PG 管理员密码经受控 stdin/内存传给一次性安装器，不写常驻容器配置或日志。
7. **WealthSail Codex 认证。** 一次性 `codex-auth login --device-auth`，由用户本人在浏览器授权；仅向用户提供必要认证交互，设备码不进交接文档/Git。随后 `login status` 和 `scripts.codex_admin check`。不要拿每日研究 `--once` 测登录或消耗每日唯一名额。
8. **启动 WealthSail。** 启动 `codex-runner api worker frontend`，检查 health、Nginx 和持久卷权限；保持每日研究开关关闭，直到认证及网页验收完成。
9. **启动并初始化 StudyOS。** 检查独立数据目录，启动 `studyos` 后通过受信容器内脚本创建首位家长和初始家庭（拟“我的家庭”）；已有家庭则保留。完成该应用自己的 Codex 认证，不共用 WealthSail auth 目录。
10. **发布受保护的路由并验收。** 两站均须通过 Access 才到应用登录页，检查 HTTPS/cookie/刷新/SSE/上传/CSRF，以及 WealthSail 用户、租户隔离、StudyOS 家长/学生流程。没有成功验收的站点不先发布。
11. **交付维护记录并提交。** 更新实际命令/原因/结果、版本/digest、备份与更新操作；只提交相关已验证改动，不推送远端，除非用户另行要求。

## 8. 恢复时命令入口与文件组合

下面是**后续操作模板，本次写交接时没有执行**。先在 NAS Bash 中手动填写私密参数对应的实际路径；不要 `source external-access/nas.env`。`NAS_TOOLS` 对应用户指定 cloudflared 持久目录下的 `tools`。

```bash
NAS_TOOLS='/替换为实际cloudflared持久化目录/tools'

# 只做无秘密输出的配置与服务状态检查。
sudo "$NAS_TOOLS/nas-compose" ws config --quiet
sudo "$NAS_TOOLS/nas-compose" so config --quiet
sudo "$NAS_TOOLS/nas-compose" ws ps -a
sudo "$NAS_TOOLS/nas-compose" so ps -a

# cloudflared 的 config/create/start 必须先准备真实 token 文件。
# 构建网络修复后才重新 build；不要把失败任务误当作仍在运行。
```

现有包装器已经包含以下文件，后续维护不能遗漏 overlay：

| 项目 | Compose 顺序 | env 顺序 |
| --- | --- | --- |
| WealthSail / `ws` | 根 `docker-compose.yml` → `docker-compose.nas.yml` → `external-access/compose.wealthsail.yaml` → `compose.wealthsail.storage.yaml` | `deployment/nas.env` → `external-access/.env` → `external-access/storage.env` |
| StudyOS / `so` | StudyOS 根 `docker-compose.yml` → WealthSail 的 `external-access/compose.studyos.yaml` → `compose.studyos.storage.yaml` | StudyOS `.env` → WealthSail `external-access/.env` → `storage.env` |
| cloudflared / `cf` | `external-access/compose.yaml` → `compose.cloudflared.storage.yaml` | `external-access/.env` → `storage.env` |

`config --quiet` 不验证网络能否拉取镜像、文件 secret 是否可读或容器是否健康。当前仅 ws/so 的合并验证通过，不能将其等同于 cloudflared 可启动。

## 9. 临时记录与下一位 AI 的定位线索

以下文件都在本机被忽略的 `.tmp` 下，**不是仓库正式脚本，也没有提交**。下次如果该目录丢失，需要据正式源码和本交接重建工具，不得依赖它们永远存在。

| 文件 | 用途/注意事项 |
| --- | --- |
| `nas_ops.py`、`nas-tools/` | 受控 SSH helper 和临时 Paramiko 依赖；读取私密参数，不会自动把密码放 argv |
| `nas-discovered-hostkey.pub` | 正确私密端口对应的固定主机密钥；不自动覆盖 |
| `nas-live-state.json` | NAS 本轮随机私有阶段目录位置；含真实路径，仅私下读取 |
| `nas-operations-redacted.jsonl` | 实际远程命令、执行原因、退出码与脱敏结果；保留原始排障证据 |
| `nas_phase_prepare.py`、`nas_prepare_remote.py` | 已执行的准备流程；**不能无脑重跑**，现有 StudyOS env 会触发保护退出 |
| `nas_phase_build*.py`、`nas_start_build*_remote.py` | 两轮构建任务和启动逻辑；保留已存在的 result/pid/log，不直接覆盖或复用 PID 判断当前进程 |
| `nas_install_buildx_remote.py`、`nas-compose/` | 私有工具安装、官方附件和哈希文件 |
| `nas_registry_probe*.py`、`nas_official_proxy_probe*.py` | 已执行的 registry/代理/子路径排查 |
| `nas_china_registry_probe*.py` | 国内候选探测脚本；尚无可确认执行结果 |
| `wealthsail-nas.bundle`、`studyos-nas.bundle` | 已同步固定版本的增量 bundle，不代表包含此后本机新提交 |
| `cloudflare-allowed-email.txt` | 初始拟允许邮箱；私密，不输出到仓库 |

NAS 阶段目录在 `/tmp` 下随机创建，权限 `0700`，实际位置见本机 state 文件。里面的 `config.json` 含初装秘密，权限 `0600`；不要公开 `cat`、截图或提交它。已保留第一轮 `ws-build.log` / `so-build.log` 和结果 JSON，以及第二轮 `ws-build-2.log` / `so-build-2.log` 和结果 JSON。临时目录可能因 NAS 重启消失；持久配置和工具在各源码目录及指定数据根目录，不在 `/tmp`。

## 10. 继续操作时必须保持的边界

- 不执行 `down -v`、`volume prune`、`system prune`、`git reset --hard`、`git clean` 或递归删除存储目录来修复失败。
- 每次涉及目录、挂载或权限，核实绝对路径、realpath、符号链接和现有数据；不对共享根目录递归 chown。已有同名卷但 driver options 不同，先备份并单独设计迁移。
- 不重新生成已有 JWT/HMAC/Runner 密钥，不覆盖 env，不把 SSH 密码注入应用容器；认证卷只挂给各自应用需要的进程。
- PostgreSQL 已有其他业务，不能重装共享服务或批量导入测试 SQL；首次 bootstrap 前再次确认精确空库目标。
- 数据库/应用账号还未创建，私密文件有生成密码不代表可以登录。Codex 镜像内 CLI、持久化目录存在也不代表已经认证。
- 当前没有公网入口。后续先有 Access、应用健康和正确连接器验证，再发布精确路由，不用开放宿主端口绕过构建/认证问题。
- 所有已完成代码改动按根 [AGENTS.md](../AGENTS.md) 验证并本地提交；保留用户原有修改，认证和个人部署参数不得进入 Git。

**暂停点：交接文档提交后停止工作，等待用户明确恢复。** 下一次首要任务是只读复核现状，然后解决镜像构建网络；不能从“数据库已经装好”或“Cloudflare 已配置完成”继续。
