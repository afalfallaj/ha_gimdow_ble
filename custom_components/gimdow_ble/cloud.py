"""The Gimdow BLE integration."""

from __future__ import annotations

import asyncio
import logging

from dataclasses import dataclass
import json
from typing import Any, Iterable

from homeassistant.const import (
    CONF_ADDRESS,
    CONF_DEVICE_ID,
    CONF_COUNTRY_CODE,
    CONF_PASSWORD,
    CONF_USERNAME,
)

from homeassistant.core import HomeAssistant

from tuya_iot import (
    TuyaOpenAPI,
    AuthType,
    TuyaOpenMQ,
)

from .gimdow_ble import (
    AbstractGimdowBLEDeviceManager,
    GimdowBLEDeviceCredentials,
)

from .const import (
    TUYA_DOMAIN,
    CONF_ACCESS_ID,
    CONF_ACCESS_SECRET,
    CONF_APP_TYPE,
    CONF_AUTH_TYPE,
    CONF_ENDPOINT,
    CONF_PRODUCT_MODEL,
    CONF_UUID,
    CONF_LOCAL_KEY,
    CONF_CATEGORY,
    CONF_PRODUCT_ID,
    CONF_DEVICE_NAME,
    CONF_PRODUCT_NAME,
    CONF_FUNCTIONS,
    CONF_STATUS_RANGE,
    DOMAIN,
    TUYA_API_DEVICES_URL,
    TUYA_API_FACTORY_INFO_URL,
    TUYA_API_DEVICE_SPECIFICATION,
    TUYA_FACTORY_INFO_MAC,
    TUYA_RESPONSE_RESULT,
    TUYA_RESPONSE_SUCCESS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class TuyaCloudCacheItem:
    api: TuyaOpenAPI | None
    login: dict[str, Any]
    credentials: dict[str, dict[str, Any]]


CONF_TUYA_LOGIN_KEYS = [
    CONF_ENDPOINT,
    CONF_ACCESS_ID,
    CONF_ACCESS_SECRET,
    CONF_AUTH_TYPE,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_COUNTRY_CODE,
    CONF_APP_TYPE,
]

CONF_TUYA_DEVICE_KEYS = [
    CONF_UUID,
    CONF_LOCAL_KEY,
    CONF_DEVICE_ID,
    CONF_CATEGORY,
    CONF_PRODUCT_ID,
    CONF_DEVICE_NAME,
    CONF_PRODUCT_NAME,
    CONF_PRODUCT_MODEL,
]


class HASSGimdowBLEDeviceManager(AbstractGimdowBLEDeviceManager):
    """Cloud connected manager of the Gimdow BLE devices credentials."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        if hass is None:
            raise ValueError("hass must not be None")
        self._hass = hass
        self._data = data

    def _cloud_cache(self) -> dict[str, TuyaCloudCacheItem]:
        """Return the shared cloud credential cache stored in hass.data."""
        return self._hass.data.setdefault(DOMAIN, {}).setdefault("_cloud_cache", {})

    def _cloud_lock(self) -> asyncio.Lock:
        """Return (lazily creating) the shared asyncio.Lock for the cloud cache."""
        domain_data = self._hass.data.setdefault(DOMAIN, {})
        if "_cloud_lock" not in domain_data:
            domain_data["_cloud_lock"] = asyncio.Lock()
        return domain_data["_cloud_lock"]

    @staticmethod
    def _is_login_success(response: dict[Any, Any]) -> bool:
        return bool(response.get(TUYA_RESPONSE_SUCCESS, False))

    @staticmethod
    def _get_cache_key(data: dict[str, Any]) -> str:
        key_dict = {key: data.get(key) for key in CONF_TUYA_LOGIN_KEYS}
        return json.dumps(key_dict)

    @staticmethod
    def _has_login(data: dict[Any, Any]) -> bool:
        for key in CONF_TUYA_LOGIN_KEYS:
            if data.get(key) is None:
                return False
        return True

    @staticmethod
    def _has_credentials(data: dict[Any, Any]) -> bool:
        for key in CONF_TUYA_DEVICE_KEYS:
            if data.get(key) is None:
                return False
        return True

    async def login_with_credentials(
        self, data: dict[str, Any], add_to_cache: bool
    ) -> dict[Any, Any]:
        """Login into Tuya cloud using an explicit credentials dictionary."""
        if len(data) == 0:
            return {}

        api = TuyaOpenAPI(
            endpoint=data.get(CONF_ENDPOINT, ""),
            access_id=data.get(CONF_ACCESS_ID, ""),
            access_secret=data.get(CONF_ACCESS_SECRET, ""),
            auth_type=data.get(CONF_AUTH_TYPE, ""),
        )
        api.set_dev_channel("hass")

        response = await self._hass.async_add_executor_job(
            api.connect,
            data.get(CONF_USERNAME, ""),
            data.get(CONF_PASSWORD, ""),
            data.get(CONF_COUNTRY_CODE, ""),
            data.get(CONF_APP_TYPE, ""),
        )

        if self._is_login_success(response):
            _LOGGER.debug("Successful login for %s", data[CONF_USERNAME])
            if add_to_cache:
                auth_type = data[CONF_AUTH_TYPE]
                if isinstance(auth_type, AuthType):
                    data[CONF_AUTH_TYPE] = auth_type.value
                cache_key = self._get_cache_key(data)
                async with self._cloud_lock():
                    cache = self._cloud_cache()
                    cache_item = cache.get(cache_key)
                    if cache_item:
                        cache_item.api = api
                        cache_item.login = data
                    else:
                        cache[cache_key] = TuyaCloudCacheItem(api, data, {})

        return response

    def _check_login(self) -> bool:
        cache_key = self._get_cache_key(self._data)
        return self._cloud_cache().get(cache_key) is not None

    async def login(self, add_to_cache: bool = False) -> dict[Any, Any]:
        return await self.login_with_credentials(self._data, add_to_cache)

    async def _fill_cache_item(self, item: TuyaCloudCacheItem) -> None:
        devices_response = await self._hass.async_add_executor_job(
            item.api.get,
            TUYA_API_DEVICES_URL % (item.api.token_info.uid),
        )
        if devices_response.get(TUYA_RESPONSE_RESULT):
            devices = devices_response.get(TUYA_RESPONSE_RESULT)
            if isinstance(devices, Iterable):
                for device in devices:
                    fi_response = await self._hass.async_add_executor_job(
                        item.api.get,
                        TUYA_API_FACTORY_INFO_URL % (device.get("id")),
                    )

                    fi_response_result = fi_response.get(TUYA_RESPONSE_RESULT)
                    if fi_response_result and len(fi_response_result) > 0:
                        factory_info = fi_response_result[0]
                        if factory_info and (TUYA_FACTORY_INFO_MAC in factory_info):
                            mac = ":".join(
                                factory_info[TUYA_FACTORY_INFO_MAC][i : i + 2]
                                for i in range(0, 12, 2)
                            ).upper()
                            item.credentials[mac] = {
                                CONF_ADDRESS: mac,
                                CONF_UUID: device.get("uuid"),
                                CONF_LOCAL_KEY: device.get("local_key"),
                                CONF_DEVICE_ID: device.get("id"),
                                CONF_CATEGORY: device.get("category"),
                                CONF_PRODUCT_ID: device.get("product_id"),
                                CONF_DEVICE_NAME: device.get("name"),
                                CONF_PRODUCT_MODEL: device.get("model"),
                                CONF_PRODUCT_NAME: device.get("product_name"),
                            }

                            spec_response = await self._hass.async_add_executor_job(
                                item.api.get,
                                TUYA_API_DEVICE_SPECIFICATION % device.get("id"),
                            )

                            spec_response_result = spec_response.get(
                                TUYA_RESPONSE_RESULT
                            )
                            if spec_response_result:
                                functions = spec_response_result.get("functions")
                                if functions:
                                    item.credentials[mac][CONF_FUNCTIONS] = functions
                                status = spec_response_result.get("status")
                                if status:
                                    item.credentials[mac][CONF_STATUS_RANGE] = status

    async def build_cache(self) -> None:
        data = {}
        for domain in (TUYA_DOMAIN, DOMAIN):
            for config_entry in self._hass.config_entries.async_entries(domain):
                data.clear()
                data.update(config_entry.data)
                key = self._get_cache_key(data)
                async with self._cloud_lock():
                    item = self._cloud_cache().get(key)
                    needs_fill = item is None or len(item.credentials) == 0
                if needs_fill:
                    if self._is_login_success(
                        await self.login_with_credentials(data, True)
                    ):
                        async with self._cloud_lock():
                            item = self._cloud_cache().get(key)
                        if item and len(item.credentials) == 0:
                            await self._fill_cache_item(item)

    def get_login_from_cache(self, address: str | None = None) -> None:
        """Pre-populate self._data with login credentials from the shared cache.

        When an address is provided, prefer the cache entry whose credentials
        contain that device — avoiding cross-account pre-fill in multi-account
        setups.  Falls back to the sole cached entry when there is no ambiguity.
        """
        cache = self._cloud_cache()
        if not cache:
            return
        if address:
            for cache_item in cache.values():
                if address.upper() in cache_item.credentials:
                    self._data.update(cache_item.login)
                    return
        if len(cache) == 1:
            self._data.update(next(iter(cache.values())).login)

    async def get_device_credentials(
        self,
        address: str,
        force_update: bool = False,
        save_data: bool = False,
    ) -> GimdowBLEDeviceCredentials | None:
        """Get credentials of the Gimdow BLE device."""
        item: TuyaCloudCacheItem | None = None
        credentials: dict[str, Any] | None = None
        result: GimdowBLEDeviceCredentials | None = None

        if not force_update and self._has_credentials(self._data):
            credentials = self._data.copy()
        else:
            cache_key: str | None = None
            async with self._cloud_lock():
                cache = self._cloud_cache()
                if self._has_login(self._data):
                    cache_key = self._get_cache_key(self._data)
                else:
                    for key in cache:
                        if cache[key].credentials.get(address) is not None:
                            cache_key = key
                            break
                if cache_key:
                    item = cache.get(cache_key)

            if item is None or force_update:
                if self._is_login_success(await self.login(True)):
                    async with self._cloud_lock():
                        item = self._cloud_cache().get(cache_key)
                    if item:
                        await self._fill_cache_item(item)

            if item:
                async with self._cloud_lock():
                    credentials = item.credentials.get(address)

        if credentials:
            result = GimdowBLEDeviceCredentials(
                credentials.get(CONF_UUID, ""),
                credentials.get(CONF_LOCAL_KEY, ""),
                credentials.get(CONF_DEVICE_ID, ""),
                credentials.get(CONF_CATEGORY, ""),
                credentials.get(CONF_PRODUCT_ID, ""),
                credentials.get(CONF_DEVICE_NAME, ""),
                credentials.get(CONF_PRODUCT_MODEL, ""),
                credentials.get(CONF_PRODUCT_NAME, ""),
                credentials.get(CONF_FUNCTIONS, []),
                credentials.get(CONF_STATUS_RANGE, []),
            )
            _LOGGER.debug("Retrieved: %s", result)
            if save_data:
                if item:
                    self._data.update(item.login)
                self._data.update(credentials)

        return result

    @property
    def data(self) -> dict[str, Any]:
        return self._data
