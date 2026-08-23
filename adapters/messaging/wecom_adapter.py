"""WeChat Work (WeCom) secure messaging adapter for WealthSail.

Handles Markdown message publishing and time-limited pairing-code
verification. Durable delivery idempotency belongs to the database Outbox;
the in-process set in this prototype is only a same-process safety net.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import os
from typing import Optional
import logging
import requests

logger = logging.getLogger(__name__)


class WeComAdapter:
    """Enterprise WeChat messaging and identity binding adapter."""

    def __init__(
        self,
        corp_id: Optional[str] = None,
        corp_secret: Optional[str] = None,
        agent_id: Optional[int] = None,
    ) -> None:
        """Initializes the WeCom adapter using environment variables by default.

        Args:
            corp_id: WeCom Corp ID (or from WECOM_CORPID).
            corp_secret: WeCom Corp Secret (or from WECOM_CORPSECRET).
            agent_id: WeCom Application Agent ID (or from WECOM_AGENTID).
        """
        self.corp_id = corp_id if corp_id is not None else os.getenv("WECOM_CORPID", "")
        self.corp_secret = corp_secret if corp_secret is not None else os.getenv("WECOM_CORPSECRET", "")
        self.agent_id = agent_id if agent_id is not None else int(os.getenv("WECOM_AGENTID", "1000002"))
        self._sent_idempotency_keys: set[str] = set()

    def get_access_token(self) -> str:
        """Fetches the active WeCom API access token.

        Returns:
            API access token string.

        Raises:
            ValueError: If credentials are not configured.
            RuntimeError: If WeCom API returns an error.
        """
        if not self.corp_id or not self.corp_secret:
            raise ValueError("未配置 WECOM_CORPID 或 WECOM_CORPSECRET 环境变量")

        url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        resp = requests.get(
            url,
            params={"corpid": self.corp_id, "corpsecret": self.corp_secret},
            timeout=10,
        ).json()
        if resp.get("errcode") != 0:
            raise RuntimeError(f"获取微信 Access Token 失败: {resp.get('errmsg')}")
        access_token = resp.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("企业微信 Access Token 响应缺少有效令牌")
        return access_token

    def send_markdown_message(
        self,
        content: str,
        to_user: str,
        idempotency_key: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict[str, str]:
        """Sends a rich Markdown formatted message to WeCom with deduplication.

        Args:
            content: Markdown formatted content string.
            to_user: Explicit target WeCom user identifier. Broadcast targets
                are forbidden for tenant-private financial messages.
            idempotency_key: Optional idempotency key for at-most-once delivery.
                This prototype only suppresses duplicates in the current
                process; production delivery uses the persistent Outbox.
            dry_run: Explicitly simulate delivery without calling WeCom.

        Returns:
            Dict containing 'status' and 'message'.
        """
        normalized_recipient = to_user.strip()
        if not normalized_recipient:
            raise ValueError("企业微信收件人不能为空")
        if normalized_recipient.lower() == "@all":
            raise ValueError("租户私有投研消息禁止群发给 @all")

        if idempotency_key and idempotency_key in self._sent_idempotency_keys:
            logger.info("Message suppressed by idempotency key: %s", idempotency_key)
            return {"status": "DUPLICATE_IGNORED", "message": "重复消息已自动抑制"}

        if dry_run:
            if idempotency_key:
                self._sent_idempotency_keys.add(idempotency_key)
            return {"status": "DRY_RUN", "message": "显式 dry-run：未调用企业微信接口"}

        if not self.corp_id or not self.corp_secret:
            logger.error("WeCom delivery rejected because credentials are missing")
            return {"status": "CONFIG_ERROR", "message": "未配置企业微信凭据，消息未发送"}

        try:
            token = self.get_access_token()
            url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
            payload = {
                "touser": normalized_recipient,
                "msgtype": "markdown",
                "agentid": self.agent_id,
                "markdown": {"content": content},
                "enable_duplicate_check": 1,
            }
            res = requests.post(url, json=payload, timeout=10).json()
            if res.get("errcode") == 0:
                if idempotency_key:
                    self._sent_idempotency_keys.add(idempotency_key)
                return {"status": "DELIVERED", "message": "✅ 消息已成功推送至企业微信！"}
            return {"status": "FAILED", "message": f"❌ 微信接口报错: {res.get('errmsg')}"}
        except Exception as exc:
            # Request exceptions may contain prepared URLs and query strings.
            # Record only the exception class so Corp Secret/access token can
            # never be echoed to logs or to the caller-facing error message.
            logger.error("WeCom send failed; error_class=%s", type(exc).__name__)
            return {"status": "ERROR", "message": "发送异常，消息未发送"}

    @staticmethod
    def hash_binding_token(token: str, secret: str) -> str:
        """Calculates HMAC-SHA256 hash of a one-time binding token.

        Args:
            token: Raw high-entropy one-time token (for example,
                ``WS-8K4P-9Q2M``). Length/attempt limits are enforced by the
                binding application service, not this hash primitive.
            secret: Server-side binding secret supplied by the trusted API.

        Returns:
            Hex string of token hash.
        """
        if not secret:
            raise ValueError("绑定令牌密钥不能为空")
        return hmac.new(secret.encode("utf-8"), token.strip().encode("utf-8"), hashlib.sha256).hexdigest()

    @classmethod
    def verify_binding_token(
        cls,
        input_token: str,
        expected_token_hash: str,
        expires_at: datetime,
        secret: str,
    ) -> bool:
        """Verifies a user-submitted one-time binding code against hash and expiration.

        Args:
            input_token: Raw code entered by user.
            expected_token_hash: Hash stored in database.
            expires_at: Token expiry timestamp.
            secret: Server-side binding secret supplied by the trusted API.

        Returns:
            True if code matches and is not expired, False otherwise.
        """
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            logger.warning("Binding token rejected because expiry timestamp has no timezone")
            return False
        now = datetime.now(timezone.utc)
        if now > expires_at:
            logger.warning("Binding token expired at %s, current is %s", expires_at, now)
            return False

        input_hash = cls.hash_binding_token(input_token, secret)
        return hmac.compare_digest(input_hash, expected_token_hash)
