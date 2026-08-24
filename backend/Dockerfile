# WealthSail API / Worker 共用后端镜像（python:3.12-slim）
# 构建：  docker build -f backend/Dockerfile -t wealthsail-backend .
# 运行：  API    → uvicorn backend.main:app --host 0.0.0.0 --port 8000
#         Worker → python -m backend.worker   （docker-compose 以 command 覆盖）
#
# 安全基线：非 root 运行；.env 不进镜像（compose 注入）；密钥全部来自环境变量。

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 依赖层（利用构建缓存；akshare 依赖较多，单独一层）
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# 应用代码与迁移/种子脚本
COPY backend/ ./backend/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
