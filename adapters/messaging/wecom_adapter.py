"""WeChat Work (WeCom) secure messaging adapter for WealthSail.

Handles Markdown message publishing, idempotent deduplication, and
time-limited one-time pairing code verification.
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
        self.corp_id = corp_id or os.getenv("WECOM_CORPID", "")
        self.corp_secret = corp_secret or os.getenv("WECOM_CORPSECRET", "")
        self.agent_id = agent_id or int(os.getenv("WECOM_AGENTID", "1000002"))
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

        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={self.corp_id}&corpsecret={self.corp_secret}"
        resp = requests.get(url, timeout=10).json()
        if resp.get("errcode") != 0:
            raise RuntimeError(f"获取微信 Access Token 失败: {resp.get('errmsg')}")
        return resp.get("access_token", "")

    def send_markdown_message(
        self,
        content: str,
        to_user: str = "@all",
        idempotency_key: Optional[str] = None,
    ) -> dict[str, str]:
        """Sends a rich Markdown formatted message to WeCom with deduplication.

        Args:
            content: Markdown formatted content string.
            to_user: Target WeCom user identifier (defaults to '@all').
            idempotency_key: Optional idempotency key for at-most-once delivery.

        Returns:
            Dict containing 'status' and 'message'.
        """
        if idempotency_key and idempotency_key in self._sent_idempotency_keys:
            logger.info("Message suppressed by idempotency key: %s", idempotency_key)
            return {"status": "DUPLICATE_IGNORED", "message": "重复消息已自动抑制"}

        if not self.corp_id or not self.corp_secret:
            logger.warning("WeCom credentials missing, mock send succeeded")
            if idempotency_key:
                self._sent_idempotency_keys.add(idempotency_key)
            return {"status": "MOCK_SENT", "message": "模拟发送成功（未配置真实凭证）"}

        try:
            token = self.get_access_token()
            url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
            payload = {
                "touser": to_user,
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
        except Exception as e:
            logger.error("WeCom send exception: %s", e)
            return {"status": "ERROR", "message": f"发送异常: {str(e)}"}

    @staticmethod
    def hash_binding_token(token: str, salt: str = "wealthsail_salt_v2") -> str:
        """Calculates HMAC-SHA256 hash of a one-time binding token.

        Args:
            token: Raw 6-character token (e.g. 'WS-8899').
            salt: Security salt.

        Returns:
            Hex string of token hash.
        """
        return hmac.new(salt.encode("utf-8"), token.strip().encode("utf-8"), hashlib.sha256).hexdigest()

    @classmethod
    def verify_binding_token(
        cls,
        input_token: str,
        expected_token_hash: str,
        expires_at: datetime,
        salt: str = "wealthsail_salt_v2",
    ) -> bool:
        """Verifies a user-submitted one-time binding code against hash and expiration.

        Args:
            input_token: Raw code entered by user.
            expected_token_hash: Hash stored in database.
            expires_at: Token expiry timestamp.
            salt: Security salt.

        Returns:
            True if code matches and is not expired, False otherwise.
        """
        now = datetime.now(timezone.utc)
        if now > expires_at:
            logger.warning("Binding token expired at %s, current is %s", expires_at, now)
            return False

        input_hash = cls.hash_binding_token(input_token, salt)
        return hmac.compare_digest(input_hash, expected_token_hash)
