# Docker 网页加载耗时排查

本次代码提供四层计时：浏览器、Nginx、API、数据库。先升级前端和 API 镜像，再在 NAS 上复现一次。以下命令在仓库目录执行；使用独立 PostgreSQL 的 NAS 部署保留两个 Compose 文件，普通部署去掉 `-f docker-compose.nas.yml`。

## 1. 抓取一次慢加载

打开终端执行，然后在浏览器打开 `/fund-screener?perf=1`，等待总纲、基金表格和买卖计划出现，最后按 Ctrl+C。

```bash
mkdir -p artifacts/web-performance
docker compose -f docker-compose.yml -f docker-compose.nas.yml logs \
  --since 5m --timestamps -f api frontend \
  | tee artifacts/web-performance/load.log
```

只看本次性能记录：

```bash
grep -E 'web_request|http_request|perf_stage|slow_sql|fund_quotes' \
  artifacts/web-performance/load.log
```

同一个请求，浏览器 Network 的 `X-Request-Id`、Nginx 的 `request_id` 和 API 的 `request_id` 一致。复制一个请求 ID 后筛选：

```bash
grep '替换为请求ID' artifacts/web-performance/load.log
```

日志不记录请求参数、SQL 原文、查询参数值、Cookie、Authorization 或基金持仓数据。请勿额外执行 `docker compose config`、打印环境变量或复制认证文件来排查耗时。

## 2. 同时看浏览器和容器资源

浏览器开发者工具：Network 勾选 Preserve log，Console 筛选 `frontend_performance`。`?perf=1` 仅开启当前页面的诊断，不改变认证或缓存策略。

- `javascript_ready`：从导航开始到入口脚本运行。
- `auth_ready`：会话恢复耗时；`route_ready`：路由组件提交时间。
- `asset`：静态文件下载耗时、传输大小与解压后的大小。
- `api`：`headersMs` 为请求到收到响应头，`bodyMs` 为响应体读取，`parseMs` 为 JSON 解析，`serverMs` 为 API 报告的服务端处理时间。
- Network → Timing / Response Headers：`Server-Timing` 显示 `app`、`db`、`pool`，单位均为毫秒。

NAS 另开终端观察 CPU、内存和网络：

```bash
docker compose -f docker-compose.yml -f docker-compose.nas.yml ps
docker stats --no-stream
```

不要把未脱敏的完整 HAR 发给他人：HAR 可能包含认证头和个人账户响应。提供指定请求的耗时、响应头以及本页性能日志即可。

## 3. 检查静态资源和 JSON 是否压缩

将站点地址替换成实际地址。先获取 HTML，再自动选取当前入口 JS：

```bash
SITE='https://替换为实际域名'
curl --compressed -sS -o artifacts/web-performance/index.html \
  -w 'html status=%{http_code} dns=%{time_namelookup}s connect=%{time_connect}s tls=%{time_appconnect}s ttfb=%{time_starttransfer}s total=%{time_total}s bytes=%{size_download}\n' \
  "$SITE/"
ASSET=$(sed -n 's/.*src="\([^"]*\/assets\/[^" ]*\.js\)".*/\1/p' artifacts/web-performance/index.html | head -n 1)
test -n "$ASSET" && curl --compressed -sS -D artifacts/web-performance/asset.headers \
  -o /dev/null \
  -w 'js status=%{http_code} ttfb=%{time_starttransfer}s total=%{time_total}s bytes=%{size_download}\n' \
  "$SITE$ASSET"
cat artifacts/web-performance/asset.headers
```

入口 JS 应是 `200`，响应应有 `Content-Encoding: gzip`（上游自行改为其他压缩算法也可以），哈希资源允许长期缓存。HTML 必须重新验证缓存，不能复用过期入口。`ASSET` 为空时，在 Network 复制真实 `/assets/...js` 路径再执行 curl。

登录后，在浏览器 Network 检查 `/api/v1/investment/opportunity-context` 的响应头，也应有 `Content-Encoding: gzip`；Size 中传输大小应明显小于资源大小。聊天 SSE 保持即时传输，不启用此项压缩。

## 4. 如何判断慢在哪里

| 现象 | 重点检查 |
| --- | --- |
| HTML TTFB 很高，API 没有收到请求 | 域名、TLS、NAS 外层反向代理、网络链路 |
| `javascript_ready` 高、静态文件下载慢 | Nginx gzip、带宽、缓存命中、镜像资源是否过大 |
| `auth_ready` 高 | `/api/v1/auth/refresh`、`/api/v1/auth/me` 对应请求 |
| Nginx `request_time` 很高、`upstream_time` 很低 | 浏览器/反向代理传输、慢客户端下载响应 |
| API `pool_wait_ms` 很高 | 数据库连接池拥塞；先查长请求，不要直接放大连接数 |
| `db_ms` 很高，出现 `slow_sql` | 数据库网络、数据库负载、对应 `query_id` 和业务阶段 |
| 某个 `perf_stage` 高但 `db_ms` 低 | Python 计算或 JSON 处理；看 `context.daily_research`、`context.nav_change_projection` 等阶段 |
| `fund_quotes.*.provider` 慢 | 外部行情源；基金最新值独立加载，基础报告和表格不等待外部行情 |

Nginx 的 `request_time`、`upstream_time` 是秒；API 和浏览器日志是毫秒。API 总耗时截至生成响应头，浏览器 `bodyMs` 和 Nginx 总耗时还能发现响应体传输问题。SSE 是长连接，其总耗时本来就长，不按普通页面接口判定。

## 已验证的代码问题与范围

- 原入口 HTML 的 root 为空，脚本到达前没有可见内容；现在提供初始加载提示。
- 移除远程字体 CSS 依赖；静态 JS/CSS 和普通 JSON API 开启 gzip。公开总览响应本地实测 2,516,521 字节压缩至 174,816 字节，减少约 93%；压缩约 9.74 ms。部署后的压缩是否生效，以浏览器响应头为准。
- 每次总览去掉一份约 3.85 MB 的重复公开数据读取和一份约 1.36 MB 的重复日报读取；本地数据库对比返回结果一致。
- 报告证据按报告处理一次，避免逐基金重复分类；本地同报告评分约 680–994 ms 降至 42–75 ms。NAS 上的实际改善需用上述日志确认。
- 使用真实数据库连接池的公开总览对比中，服务端处理约 1,543 ms 降至 768 ms，序列化结果一致。这不是 NAS 网络环境的端到端加载时间。
- 确认净值从正式披露源独立核对；收盘后也可以获取最新估算净值，并保留估值日期和标识。原报告的研究日期、冻结买卖点不被实时行情改写。
