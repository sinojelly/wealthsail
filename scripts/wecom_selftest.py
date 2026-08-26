#!/usr/bin/env python3
"""WeCom /callback URL 验证端到端自测工具。

作用：模拟企业微信的 URL 验证请求，打本机（或指定）的 /callback 路由，
判断「运行中的进程」配置是否正确 —— 生成真实加密 echostr → 官方签名 →
GET /api/v1/adapters/wecom/callback → 期望 200 且 body 为明文回显。

凭据来源（按优先级）：
  1. 环境变量 WECOM_CALLBACK_TOKEN / WECOM_CALLBACK_AES_KEY / WECOM_CORP_ID
  2. 当前目录下的 .env / api.env / worker.env

用法：
  python3 wecom_selftest.py [URL]
    URL 默认 http://127.0.0.1:8000/api/v1/adapters/wecom/callback

结果解读：
  RESULT: PASS —— 运行进程的回调配置和时钟正确；
            此时 403 只可能来自企微侧（URL 配置 / 请求内容），与服务器无关。
  RESULT: FAIL 403 —— Token/AESKey/CorpID、时间窗或代理参数不一致。
  连接失败 —— 端口映射/防火墙问题，改用 docker exec 进容器内执行。
"""
import base64
import hashlib
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8000/api/v1/adapters/wecom/callback"
ENV_FILES = (".env", "api.env", "worker.env")


def _load_env_files() -> dict:
    values: dict = {}
    for name in ENV_FILES:
        path = Path(name)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def _aes_key_bytes(aes_key: str) -> bytes:
    padded = aes_key + "=" * ((4 - len(aes_key) % 4) % 4)
    return base64.b64decode(padded)


def wecom_encrypt(plaintext: str, aes_key: str, receive_id: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = _aes_key_bytes(aes_key)
    iv = key[:16]
    raw = (
        b"\x00" * 16
        + struct.pack(">I", len(plaintext.encode("utf-8")))
        + plaintext.encode("utf-8")
        + receive_id.encode("utf-8")
    )
    pad_len = 32 - (len(raw) % 32)
    raw += bytes([pad_len]) * pad_len
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(raw) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def sign(token: str, ts: str, nonce: str, echostr: str) -> str:
    return hashlib.sha1("".join(sorted([token, ts, nonce, echostr])).encode()).hexdigest()


def main() -> int:
    env = os.environ
    file_env = _load_env_files()
    token = env.get("WECOM_CALLBACK_TOKEN") or file_env.get("WECOM_CALLBACK_TOKEN", "")
    aes_key = env.get("WECOM_CALLBACK_AES_KEY") or file_env.get("WECOM_CALLBACK_AES_KEY", "")
    corp_id = env.get("WECOM_CORP_ID") or file_env.get("WECOM_CORP_ID", "")
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL

    if not (token and aes_key and corp_id):
        print("ERROR: 缺少 WECOM_CALLBACK_TOKEN / WECOM_CALLBACK_AES_KEY / WECOM_CORP_ID（环境变量或 .env/api.env）")
        return 2
    if len(aes_key) != 43:
        print(f"ERROR: AESKey 长度应为 43，实际 {len(aes_key)}（可能含空白/换行，建议用 cat -A 检查）")
        return 2

    print(f"URL      : {url}")
    print("配置     : Token/AESKey/CorpID 均已提供（不输出值或指纹）")

    plain = str(int(time.time() * 1000) % 10**19)
    echostr = wecom_encrypt(plain, aes_key, corp_id)
    ts = str(int(time.time()))
    nonce = "wealthsail-selftest"
    sig = sign(token, ts, nonce, echostr)
    qs = urllib.parse.urlencode(
        {"msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": echostr}
    )
    print(f"请求     : GET {url}（签名参数已隐藏）")

    req = urllib.request.Request(url + "?" + qs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ok = resp.status == 200 and body == plain
            print(f"HTTP {resp.status}")
            print("RESULT: PASS —— 运行进程回调验证配置正确"
                  if ok else "RESULT: FAIL —— 状态 200 但返回内容与明文不一致")
            return 0 if ok else 1
    except urllib.error.HTTPError as exc:
        exc.read()
        print(f"HTTP {exc.code}")
        if exc.code == 403:
            print("RESULT: FAIL —— 签名、时间窗、AESKey 或 CorpID 校验未通过")
        else:
            print(f"RESULT: FAIL —— HTTP {exc.code}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        print("提示: 连不上说明端口映射/防火墙/容器网络问题；docker 部署时用 docker exec -i 进容器内跑")
        return 2


if __name__ == "__main__":
    sys.exit(main())
