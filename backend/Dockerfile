# WealthSail API / Worker 共用后端镜像（python:3.12-slim）
# 构建：  docker build -f backend/Dockerfile -t wealthsail-backend .
# 运行：  API    → uvicorn backend.main:app --host 0.0.0.0 --port 8000
#         Worker → python -m backend.worker   （docker-compose 以 command 覆盖）
#
# 安全基线：非 root 运行；.env 不进镜像（compose 注入）；密钥全部来自环境变量。

# npm is used only to obtain the pinned, platform-specific native distribution.
# Node, npm and the JavaScript launcher do not enter the runtime image.
FROM node:24-bookworm-slim AS codex-tooling

ARG CODEX_CLI_VERSION=0.160.1
RUN npm install --global --include=optional "@openai/codex@${CODEX_CLI_VERSION}" \
    && npm cache clean --force
COPY deployment/codex/extract-native.mjs /build/extract-native.mjs
RUN node /build/extract-native.mjs /usr/local/lib/node_modules/@openai/codex /opt/codex \
    && test "$("/opt/codex/native/$(cat /opt/codex/native-executable)" --version)" \
        = "codex-cli ${CODEX_CLI_VERSION}"

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=codex-tooling /opt/codex/ /opt/codex/
RUN ln -s "/opt/codex/native/$(cat /opt/codex/native-executable)" /usr/local/bin/codex \
    && codex --version

# 依赖层（利用构建缓存；akshare 依赖较多，单独一层）
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# 应用代码与迁移/种子脚本
COPY backend/ ./backend/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

# 非 root 运行
RUN groupadd --gid 10001 appuser \
    && useradd --create-home --uid 10001 --gid 10001 appuser \
    && install -d -m 0700 -o 10001 -g 10001 /home/appuser/.codex \
    && chown -R appuser:appuser /app
COPY --chown=10001:10001 deployment/codex/config.toml /home/appuser/.codex/config.toml
RUN chmod 0600 /home/appuser/.codex/config.toml

ENV HOME=/home/appuser \
    PATH=/opt/codex/native/codex-path:/opt/codex/native/path:${PATH}
USER appuser
RUN codex --version

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
