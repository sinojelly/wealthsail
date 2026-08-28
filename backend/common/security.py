"""Security primitives: CSPRNG tokens, keyed HMAC digests, envelope encryption.

Binding tokens are high-entropy one-time capabilities. Only keyed HMAC-SHA256
digests are persisted; reversible routing values use AES-256-GCM envelope
encryption. The API callback writer and Worker currently share that symmetric
key, while database column privileges prevent the API role from reading stored
ciphertext. Public-key/KMS-separated decrypt authority remains a target design.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_token(entropy_bytes: int = 16) -> str:
    """CSPRNG token with >=128-bit entropy, base32-encoded (no padding).

    Format: WS-XXXX-XXXX-... (grouped, uppercase, URL-safe).
    """
    raw = secrets.token_bytes(entropy_bytes)
    b32 = base64.b32encode(raw).decode("ascii").rstrip("=").upper()
    groups = [b32[i : i + 4] for i in range(0, len(b32), 4)]
    return "WS-" + "-".join(groups)


def generate_binding_code() -> str:
    """Return a one-time WeCom binding capability with >=128-bit entropy.

    A binding credential grants access to a user's private delivery route, so
    expiry alone cannot compensate for a guessable short code.  The grouped
    base32 representation remains copy/paste friendly and the UI displays it
    exactly once; only its keyed HMAC digest is persisted.
    """
    return generate_token(16)


def hmac_digest(key: str, value: str) -> str:
    """Keyed HMAC-SHA256 hex digest. Callers compare sensitive values safely."""
    return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _normalize_aes_key(key: str) -> bytes:
    """WeCom-style AES key: 43-char base64 → 32-byte key."""
    padded = key + "=" * ((4 - len(key) % 4) % 4)
    return base64.b64decode(padded)


def _envelope_key(key: str) -> bytes:
    """Envelope-encryption key: any configured string → 256-bit AES key."""
    return hashlib.sha256(key.encode("utf-8")).digest()


def envelope_encrypt(key: str, plaintext: str) -> str:
    """AES-256-GCM envelope encryption → "v1:<nonce_b64>:<ciphertext_b64>"."""
    aes = AESGCM(_envelope_key(key))
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def envelope_decrypt(key: str, token: str) -> str:
    """Inverse of envelope_encrypt."""
    version, nonce_b64, ct_b64 = token.split(":", 2)
    if version != "v1":
        raise ValueError(f"unsupported envelope version: {version}")
    aes = AESGCM(_envelope_key(key))
    plaintext = aes.decrypt(
        base64.b64decode(nonce_b64), base64.b64decode(ct_b64), None
    )
    return plaintext.decode("utf-8")
