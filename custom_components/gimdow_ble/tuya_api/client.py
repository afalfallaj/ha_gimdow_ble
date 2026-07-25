"""Client for interacting with Tuya OpenAPI natively and asynchronously."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from aiohttp import ClientSession

from .const import (
    TO_C_CUSTOM_REFRESH_TOKEN_API,
    TO_C_SMART_HOME_REFRESH_TOKEN_API,
    TO_C_CUSTOM_TOKEN_API,
    TO_C_SMART_HOME_TOKEN_API,
    TUYA_ERROR_CODE_TOKEN_INVALID,
    VERSION,
    AuthType,
)
from .models import TuyaTokenInfo

_LOGGER = logging.getLogger(__name__)


class TuyaOpenAPI:
    """Async Tuya OpenAPI Client."""

    def __init__(
        self,
        endpoint: str,
        access_id: str,
        access_secret: str,
        auth_type: AuthType = AuthType.SMART_HOME,
        lang: str = "en",
    ) -> None:
        """Init TuyaOpenAPI."""
        self.endpoint = endpoint
        self.access_id = access_id
        self.access_secret = access_secret
        self.lang = lang

        self.auth_type = auth_type
        if self.auth_type == AuthType.CUSTOM:
            self.__login_path = TO_C_CUSTOM_TOKEN_API
        else:
            self.__login_path = TO_C_SMART_HOME_TOKEN_API

        self.token_info: TuyaTokenInfo | None = None
        self.dev_channel: str = "hass"

        self.__username = ""
        self.__password = ""
        self.__country_code = ""
        self.__schema = ""

    def set_dev_channel(self, dev_channel: str):
        """Set dev channel."""
        self.dev_channel = dev_channel

    def _calculate_sign(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[str, int]:
        str_to_sign = method
        str_to_sign += "\n"

        content_to_sha256 = (
            "" if body_str is None or len(body_str) == 0 else body_str
        )

        str_to_sign += (
            hashlib.sha256(content_to_sha256.encode("utf8")).hexdigest().lower()
        )
        str_to_sign += "\n"
        str_to_sign += "\n"
        str_to_sign += path

        if params is not None and len(params.keys()) > 0:
            str_to_sign += "?"
            params_keys = sorted(params.keys())
            query_builder = "".join(f"{key}={params[key]}&" for key in params_keys)
            str_to_sign += query_builder[:-1]

        t = int(time.time() * 1000)

        message = self.access_id
        if self.token_info is not None:
            message += self.token_info.access_token
        message += str(t) + str_to_sign
        sign = (
            hmac.new(
                self.access_secret.encode("utf8"),
                msg=message.encode("utf8"),
                digestmod=hashlib.sha256,
            )
            .hexdigest()
            .upper()
        )
        return sign, t

    async def __refresh_access_token_if_need(self, session: ClientSession, path: str):
        if not self.is_connect():
            return

        if path.startswith(self.__login_path):
            return

        now = int(time.time() * 1000)
        expired_time = self.token_info.expire_time

        if expired_time - 60 * 1000 > now:
            return

        self.token_info.access_token = ""

        if self.auth_type == AuthType.CUSTOM:
            response = await self.post(
                session, TO_C_CUSTOM_REFRESH_TOKEN_API + self.token_info.refresh_token
            )
        else:
            response = await self.get(
                session, TO_C_SMART_HOME_REFRESH_TOKEN_API + self.token_info.refresh_token
            )

        self.token_info = TuyaTokenInfo(response)

    async def connect(
        self,
        session: ClientSession,
        username: str = "",
        password: str = "",
        country_code: str = "",
        schema: str = "",
    ) -> dict[str, Any]:
        """Connect to Tuya Cloud."""
        self.__username = username
        self.__password = password
        self.__country_code = country_code
        self.__schema = schema

        if self.auth_type == AuthType.CUSTOM:
            response = await self.post(
                session,
                TO_C_CUSTOM_TOKEN_API,
                {
                    "username": username,
                    "password": hashlib.sha256(password.encode("utf8"))
                    .hexdigest()
                    .lower(),
                },
            )
        else:
            response = await self.post(
                session,
                TO_C_SMART_HOME_TOKEN_API,
                {
                    "username": username,
                    "password": hashlib.md5(password.encode("utf8")).hexdigest(),
                    "country_code": country_code,
                    "schema": schema,
                },
            )

        if not response or not response.get("success"):
            return response or {}

        self.token_info = TuyaTokenInfo(response)
        return response

    def is_connect(self) -> bool:
        """Is connect to tuya cloud."""
        return self.token_info is not None and len(self.token_info.access_token) > 0

    async def __request(
        self,
        session: ClientSession,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body_str: str | None = None,
    ) -> dict[str, Any]:
        await self.__refresh_access_token_if_need(session, path)

        access_token = self.token_info.access_token if self.token_info else ""
        sign, t = self._calculate_sign(method, path, params, body_str)
        headers = {
            "client_id": self.access_id,
            "sign": sign,
            "sign_method": "HMAC-SHA256",
            "access_token": access_token,
            "t": str(t),
            "lang": self.lang,
        }

        if path == self.__login_path or \
            path.startswith(TO_C_CUSTOM_REFRESH_TOKEN_API) or\
            path.startswith(TO_C_SMART_HOME_REFRESH_TOKEN_API):
            headers["dev_lang"] = "python"
            headers["dev_version"] = VERSION
            headers["dev_channel"] = self.dev_channel

        url = self.endpoint + path
        try:
            async with session.request(
                method, url, params=params, data=body_str, headers=headers
            ) as response:
                if not response.ok:
                    _LOGGER.error("Tuya API response error: code=%s", response.status)
                    return {}
                result = await response.json()

                if result.get("code", -1) == TUYA_ERROR_CODE_TOKEN_INVALID:
                    self.token_info = None
                    await self.connect(
                        session,
                        self.__username,
                        self.__password,
                        self.__country_code,
                        self.__schema,
                    )

                return result
        except Exception as e:
            _LOGGER.error("Tuya API request failed: %s", e)
            return {}

    async def get(self, session: ClientSession, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.__request(session, "GET", path, params, None)

    async def post(self, session: ClientSession, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body_str = json.dumps(body) if body else ""
        return await self.__request(session, "POST", path, None, body_str)

    async def put(self, session: ClientSession, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body_str = json.dumps(body) if body else ""
        return await self.__request(session, "PUT", path, None, body_str)

    async def delete(self, session: ClientSession, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.__request(session, "DELETE", path, params, None)
