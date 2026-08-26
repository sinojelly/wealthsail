#!/usr/bin/env bash
# =============================================================================
# WealthSail 本地开发环境一键启动 / 停止 / 状态
#
#   ./start.sh            # 启动 API + Worker + 前端（等价于 ./start.sh start）
#   ./start.sh stop       # 停止全部
#   ./start.sh restart    # 重启全部
#   ./start.sh status     # 查看各进程状态
#
# 依赖：.venv（Python 虚拟环境）、frontend/node_modules（前端依赖）、
#       .env（DATABASE_URL / WORKER_DATABASE_URL 等，已配置 Supabase 直连）
# 日志：logs/api.log · logs/worker.log · logs/frontend.log
# PID ：.run/*.pid（stop/status 依据）
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

PY="$PWD/.venv/bin/python"
API_PORT="${WS_API_PORT:-8000}"
FRONT_PORT="${WS_FRONT_PORT:-5173}"
LOG_DIR="$PWD/logs"
PID_DIR="$PWD/.run"
mkdir -p "$LOG_DIR" "$PID_DIR"

is_running() { # $1 = pid 文件
  [ -f "$1" ] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null
}

port_open() { # $1 = 端口
  python3 - "$1" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    print("open")
except OSError:
    print("closed")
finally:
    s.close()
PY
}

cmd_start() {
  echo "▶ 启动 WealthSail（API + Worker + 前端）"

  if is_running "$PID_DIR/api.pid"; then
    echo "  · API 已在运行 (pid $(cat "$PID_DIR/api.pid"))"
  elif [ "$(port_open "$API_PORT")" = "open" ]; then
    echo "  ⚠ API 端口 $API_PORT 已被其他进程占用，跳过启动（可先 ./start.sh stop 或关掉占用进程）"
  else
    nohup "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port "$API_PORT" \
      > "$LOG_DIR/api.log" 2>&1 &
    echo $! > "$PID_DIR/api.pid"
    echo "  · API 启动 → http://localhost:$API_PORT （logs/api.log）"
  fi

  if is_running "$PID_DIR/worker.pid"; then
    echo "  · Worker 已在运行 (pid $(cat "$PID_DIR/worker.pid"))"
  else
    nohup "$PY" -m backend.worker > "$LOG_DIR/worker.log" 2>&1 &
    echo $! > "$PID_DIR/worker.pid"
    echo "  · Worker 启动（调度 + outbox 投递，logs/worker.log）"
  fi

  if [ -d frontend/node_modules ]; then
    if is_running "$PID_DIR/front.pid"; then
      echo "  · 前端已在运行 (pid $(cat "$PID_DIR/front.pid"))"
    elif [ "$(port_open "$FRONT_PORT")" = "open" ]; then
      echo "  ⚠ 前端端口 $FRONT_PORT 已被其他进程占用，跳过启动"
    else
      nohup sh -c 'cd frontend && exec ./node_modules/.bin/vite --host 0.0.0.0' \
        > "$LOG_DIR/frontend.log" 2>&1 &
      echo $! > "$PID_DIR/front.pid"
      echo "  · 前端启动 → http://localhost:$FRONT_PORT （logs/frontend.log）"
    fi
  else
    echo "  ⚠ 跳过前端：frontend/node_modules 不存在（先 npm install）"
  fi

  echo "▶ 健康检查…"
  for _ in $(seq 1 15); do
    [ "$(port_open "$API_PORT")" = "open" ] && break
    sleep 1
  done
  if [ "$(port_open "$API_PORT")" = "open" ]; then
    echo "  ✔ API 端口 $API_PORT 就绪"
  else
    echo "  ✘ API 端口 $API_PORT 未就绪，详见 logs/api.log"
    exit 1
  fi
  [ "$(port_open "$FRONT_PORT")" = "open" ] \
    && echo "  ✔ 前端端口 $FRONT_PORT 就绪" \
    || echo "  ⚠ 前端端口 $FRONT_PORT 未就绪（详见 logs/frontend.log）"
  echo "  完成。访问 http://localhost:$FRONT_PORT 或直接 http://localhost:$API_PORT"
}

cmd_stop() {
  echo "■ 停止 WealthSail"
  for name in api worker front; do
    pid_file="$PID_DIR/$name.pid"
    if is_running "$pid_file"; then
      kill "$(cat "$pid_file")" 2>/dev/null || true
      rm -f "$pid_file"
      echo "  · $name 已停止"
    else
      rm -f "$pid_file"
    fi
  done
}

cmd_status() {
  echo "■ WealthSail 状态"
  for name in api worker front; do
    pid_file="$PID_DIR/$name.pid"
    if is_running "$pid_file"; then
      echo "  · $name: 运行中 (pid $(cat "$pid_file"))"
    else
      echo "  · $name: 未运行"
    fi
  done
}

case "${1:-start}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  *) echo "用法: $0 [start|stop|restart|status]" >&2; exit 2 ;;
esac
