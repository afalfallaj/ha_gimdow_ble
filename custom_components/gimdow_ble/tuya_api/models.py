"""Data models for the Tuya OpenAPI client."""
from typing import Any


class TuyaTokenInfo:
    """Tuya token info."""

    def __init__(self, token_response: dict[str, Any] = None):
        """Init TuyaTokenInfo."""
        token_response = token_response or {}
        result = token_response.get("result", {})

        self.expire_time = (
            token_response.get("t", 0)
            + result.get("expire", result.get("expire_time", 0)) * 1000
        )
        self.access_token = result.get("access_token", "")
        self.refresh_token = result.get("refresh_token", "")
        self.uid = result.get("uid", "")
        self.platform_url = result.get("platform_url", "")
