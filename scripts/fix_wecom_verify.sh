#!/usr/bin/env bash
# =============================================================================
# WealthSail 企微回调 URL 验证 403 一键诊断+修复脚本
#
# 用法（云服务器上）:
#   sudo bash fix_wecom_verify.sh
#
# 原理:
#   代码/算法本身正确（已本地实测），403 的根因是「运行进程实际持有的
#   WECOM_* 配置」与企微后台不一致 —— 环境变量(优先级高)覆盖 .env、
#   或改完 .env 未重启进程、或 api.env/worker.env 残留 dev 占位值。
#
# 步骤: ①进程环境变量诊断 ②systemd 单元检查 ③日志失败原因 ④时钟检查
#       ⑤备份+同步配置(.env/api.env/worker.env) ⑥重启服务 ⑦端到端自测
# 全程幂等，可重复执行；不改动与验证无关的配置（WECOM_CORP_SECRET 等）。
# =============================================================================
set -uo pipefail

# ---- 企微后台实际值（与 admin 后台「接收消息」配置一致）----
WECOM_TOKEN="lRYMyUWdtVcJt"
WECOM_AES_KEY="HVOXHkwz2HHeYNORKXhG4Eecvfr2BBifRAlOn7oUtgi"
WECOM_CORP_ID="wwa7129a0d7f520a61"

PROJECT_DIR="/opt/wealthsail"
SERVICE="wealthsail.service"
URL="http://127.0.0.1:8000/api/v1/adapters/wecom/verify"

say() { printf '\n===== %s =====\n' "$*"; }

# ---------------------------------------------------------------------------
say "[1/6] 诊断：运行进程实际持有的 WECOM_*（cat -A 可看到尾部隐藏空格/^M）"
# ---------------------------------------------------------------------------
PID=$(pgrep -f "uvicorn backend.main" | head -1)
if [ -n "${PID:-}" ]; then
  echo "进程 PID=$PID"
  sudo cat "/proc/$PID/environ" 2>/dev/null | tr '\0' '\n' \
    | grep '^WECOM_' | sort | cat -A || echo "(读取 /proc/$PID/environ 失败)"
else
  echo "警告：未找到 uvicorn backend.main 进程（服务未运行？）"
fi

# ---------------------------------------------------------------------------
say "[2/6] 诊断：systemd 单元中的环境变量注入"
# ---------------------------------------------------------------------------
if systemctl cat "$SERVICE" >/dev/null 2>&1; then
  systemctl cat "$SERVICE" | grep -E 'Environment|EnvironmentFile|ExecStart|WorkingDirectory' || true
  echo "---- 单元文件可能还引用了其它 env 文件，逐一检查 ----"
  for f in $(systemctl cat "$SERVICE" 2>/dev/null | grep -oP 'EnvironmentFile=-?\K\S+' ); do
    echo "== $f =="
    [ -f "$f" ] && grep '^WECOM_' "$f" | cat -A || echo "(文件不存在: $f)"
  done
else
  echo "未找到 systemd 单元 $SERVICE（可能是裸进程/其它方式部署）"
fi

# ---------------------------------------------------------------------------
say "[3/6] 诊断：日志中的失败原因（决定性证据）"
# ---------------------------------------------------------------------------
if systemctl is-active "$SERVICE" >/dev/null 2>&1; then
  sudo journalctl -u "$SERVICE" --no-pager -n 300 2>/dev/null | grep -i wecom | tail -15
else
  echo "(服务未激活，跳过日志查询；若有 nohup.out 请自行查看)"
fi
echo "日志判读:"
echo "  signature mismatch                 -> Token/AESKey 与后台不一致（本脚本将修复）"
echo "  timestamp out of replay window     -> 服务器时钟偏差 >300s（见 [4/6] 时钟检查）"
echo "  receiveid mismatch / verify failed -> CorpID 或 AESKey 错误（本脚本将修复）"

# ---------------------------------------------------------------------------
say "[4/6] 诊断：服务器时钟（企微要求 |服务器时间-请求时间戳| <= 300s）"
# ---------------------------------------------------------------------------
date
echo -n "参考时间(HTTP Date 头): "
curl -sI --max-time 8 https://www.baidu.com 2>/dev/null | grep -i '^date:' || echo "无法获取网络时间"

# ---------------------------------------------------------------------------
say "[5/6] 修复：备份 + 同步 WECOM_* 配置"
# ---------------------------------------------------------------------------
cd "$PROJECT_DIR" 2>/dev/null || { echo "错误：项目目录不存在 $PROJECT_DIR，请修改脚本顶部 PROJECT_DIR"; exit 1; }

TS=$(date +%Y%m%d_%H%M%S)
for f in .env api.env worker.env; do
  if [ -f "$f" ]; then
    cp "$f" "$f.bak.$TS" && echo "已备份 $f -> $f.bak.$TS"
  fi
done

# 更新或追加三个验证相关键（用 | 作分隔符避免转义问题）
for f in .env api.env worker.env; do
  [ -f "$f" ] || continue
  for entry in \
    "WECOM_CALLBACK_TOKEN|$WECOM_TOKEN" \
    "WECOM_CALLBACK_AES_KEY|$WECOM_AES_KEY" \
    "WECOM_CORP_ID|$WECOM_CORP_ID"; do
    key="${entry%%|*}"; val="${entry#*|}"
    if grep -q "^${key}=" "$f"; then
      sed -i "s|^${key}=.*|${key}=${val}|" "$f"
    else
      printf '%s=%s\n' "$key" "$val" >> "$f"
    fi
  done
  echo "已同步 $f"
  grep -E '^WECOM_CALLBACK_(TOKEN|AES_KEY)=|^WECOM_CORP_ID=' "$f" | cat -A
done

# ---------------------------------------------------------------------------
say "[6/6] 重启服务 + 端到端自测"
# ---------------------------------------------------------------------------
if systemctl list-unit-files "$SERVICE" >/dev/null 2>&1; then
  sudo systemctl restart "$SERVICE"
  sleep 4
  systemctl is-active "$SERVICE" || { echo "服务重启失败，请查看: sudo journalctl -u $SERVICE -n 50"; exit 1; }
  echo "服务已重启并处于 active 状态"
else
  echo "未使用 systemd，请手动重启你的 uvicorn 进程后继续"
fi

# 端到端自测：生成真实加密 echostr -> 官方签名 -> GET /verify -> 期望 200 明文
PY="$PROJECT_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
cat > /tmp/wecom_selftest.py <<'PYEOF'
import base64, hashlib, os, struct, sys, time, urllib.error, urllib.parse, urllib.request
def key_bytes(k): return base64.b64decode(k + "=" * ((4 - len(k) % 4) % 4))
def encrypt(plain, aes, rid):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    k = key_bytes(aes); iv = k[:16]
    raw = b"\x00"*16 + struct.pack(">I", len(plain.encode())) + plain.encode() + rid.encode()
    pad = 32 - len(raw) % 32; raw += bytes([pad]) * pad
    c = Cipher(algorithms.AES(k), modes.CBC(iv)); e = c.encryptor()
    return base64.b64encode(e.update(raw) + e.finalize()).decode()
def sign(tok, ts, nonce, echo):
    return hashlib.sha1("".join(sorted([tok, ts, nonce, echo])).encode()).hexdigest()
tok, aes, rid = os.environ["WECOM_TOKEN"], os.environ["WECOM_AES_KEY"], os.environ["WECOM_CORP_ID"]
plain = str(int(time.time() * 1000) % 10**19)
echo = encrypt(plain, aes, rid)
ts, nonce = str(int(time.time())), "fix-verify"
qs = urllib.parse.urlencode({"msg_signature": sign(tok, ts, nonce, echo),
                             "timestamp": ts, "nonce": nonce, "echostr": echo})
try:
    with urllib.request.urlopen(sys.argv[1] + "?" + qs, timeout=10) as r:
        body = r.read().decode()
        ok = r.status == 200 and body == plain
        print(f"HTTP {r.status}  body={body[:60]!r}")
        print("RESULT: PASS —— 进程配置正确，/verify 工作正常，请去企微后台重新保存回调配置"
              if ok else "RESULT: FAIL —— 状态 200 但内容不符")
        sys.exit(0 if ok else 1)
except urllib.error.HTTPError as e:
    body = e.read().decode()[:120]
    print(f"HTTP {e.code}  body={body!r}")
    if e.code == 403 and "signature mismatch" in body:
        print("RESULT: FAIL —— Token/AESKey 仍不一致（检查 .env 是否被环境变量覆盖、是否重启成功）")
    elif e.code == 403:
        print("RESULT: FAIL —— verify failed（时钟偏差或 AESKey 错误）")
    sys.exit(1)
except Exception as e:
    print(f"ERROR: {e}")
    print("提示: 连不上 8000 端口则检查 systemd ExecStart 的监听地址与端口映射")
    sys.exit(2)
PYEOF

WECOM_TOKEN="$WECOM_TOKEN" WECOM_AES_KEY="$WECOM_AES_KEY" WECOM_CORP_ID="$WECOM_CORP_ID" \
  "$PY" /tmp/wecom_selftest.py "$URL"
rc=$?

echo
if [ $rc -eq 0 ]; then
  echo "✅ 修复完成：进程配置已正确。最后一步：企业微信管理后台 -> 应用 -> 接收消息 -> 保存（重新触发 URL 验证）。"
else
  echo "❌ 自测未通过（exit=$rc），请把上方输出发回排查。"
fi
exit $rc
