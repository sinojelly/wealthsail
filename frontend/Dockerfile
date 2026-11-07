# WealthSail 前端镜像：pnpm 构建 → nginx 静态托管 + /api 反向代理
# 构建：  docker build -f frontend/Dockerfile -t wealthsail-frontend .
# 运行：  80 端口对外；/api/* 代理到 compose 内 api:8000（见 nginx.conf）

FROM node:22-alpine AS build

WORKDIR /app

# corepack 固定 pnpm 版本（与 packageManager 对齐）
RUN corepack enable && corepack prepare pnpm@11.23.0 --activate

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# ---- 运行层：nginx ----
FROM nginx:1.27-alpine

ARG BUILD_COMMIT=""
LABEL org.opencontainers.image.revision="${BUILD_COMMIT}"

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
