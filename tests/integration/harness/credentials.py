from __future__ import annotations

import asyncio
import os
from typing import Any

from tuya_iot import TuyaOpenAPI, AuthType

from custom_components.gimdow_ble.gimdow_ble.manager import (
    AbstractGimdowBLEDeviceManager,
    GimdowBLEDeviceCredentials,
)
from custom_components.gimdow_ble.const import (
    TUYA_API_DEVICES_URL,
    TUYA_API_FACTORY_INFO_URL,
    TUYA_API_DEVICE_SPECIFICATION,
    TUYA_FACTORY_INFO_MAC,
    TUYA_RESPONSE_SUCCESS,
    TUYA_RESPONSE_RESULT,
    CONF_UUID,
    CONF_LOCAL_KEY,
    CONF_CATEGORY,
    CONF_PRODUCT_ID,
    CONF_DEVICE_NAME,
    CONF_PRODUCT_MODEL,
    CONF_PRODUCT_NAME,
    CONF_FUNCTIONS,
    CONF_STATUS_RANGE,
    TUYA_COUNTRIES,
    TUYA_SMART_APP,
    SMARTLIFE_APP,
)


class StandaloneManager(AbstractGimdowBLEDeviceManager):
    def __init__(self, credentials: GimdowBLEDeviceCredentials) -> None:
        self._credentials = credentials

    async def get_device_credentials(
        self, address: str, force_update: bool = False, save_data: bool = False
    ) -> GimdowBLEDeviceCredentials | None:
        return self._credentials


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


async def _prompt(
    label: str, env_key: str = "", default: str = "", secret: bool = False
) -> str:
    """Prompt the user, pre-filling from env var or default. Never blocks event loop."""
    env_val = _env(env_key) if env_key else ""
    effective_default = env_val or default
    if effective_default:
        hint = (
            f"[{effective_default[:4]}…] "
            if secret and len(effective_default) > 8
            else f"[{effective_default}] "
        )
    else:
        hint = ""
    value = (await asyncio.to_thread(input, f"  {label} {hint}: ")).strip()
    return value or effective_default


async def load_credentials_manual(
    skip_prompts: bool = False,
) -> tuple[GimdowBLEDeviceCredentials, str]:
    if skip_prompts:
        mac = _env("GIMDOW_MAC")
        uuid = _env("GIMDOW_UUID")
        local_key = _env("GIMDOW_LOCAL_KEY")
        device_id = _env("GIMDOW_DEVICE_ID")
        category = _env("GIMDOW_CATEGORY", "jtmspro")
        product_id = _env("GIMDOW_PRODUCT_ID", "rlyxv7pe")
        device_name = _env("GIMDOW_DEVICE_NAME") or None
        product_model = _env("GIMDOW_PRODUCT_MODEL", "A1 PRO MAX") or None
        product_name = _env("GIMDOW_PRODUCT_NAME", "Gimdow Lock") or None
    else:
        print("\n  -- Manual credential entry --")
        print("  (Press Enter to accept value shown in [brackets] from .env)")
        mac = await _prompt("BLE MAC address (AA:BB:CC:DD:EE:FF)", "GIMDOW_MAC")
        uuid = await _prompt("uuid", "GIMDOW_UUID", secret=True)
        local_key = await _prompt("local_key", "GIMDOW_LOCAL_KEY", secret=True)
        device_id = await _prompt("device_id", "GIMDOW_DEVICE_ID")
        category = await _prompt("category", "GIMDOW_CATEGORY", default="jtmspro")
        product_id = await _prompt(
            "product_id", "GIMDOW_PRODUCT_ID", default="rlyxv7pe"
        )
        device_name = (
            await _prompt("device_name", "GIMDOW_DEVICE_NAME", default="") or None
        )
        product_model = (
            await _prompt("product_model", "GIMDOW_PRODUCT_MODEL", default="A1 PRO MAX")
            or None
        )
        product_name = (
            await _prompt("product_name", "GIMDOW_PRODUCT_NAME", default="Gimdow Lock")
            or None
        )

    creds = GimdowBLEDeviceCredentials(
        uuid=uuid,
        local_key=local_key,
        device_id=device_id,
        category=category,
        product_id=product_id,
        device_name=device_name,
        product_model=product_model,
        product_name=product_name,
        functions=[],
        status_range=[],
    )
    lk = local_key
    print(
        f"\n  uuid={uuid[:4]}…{uuid[-4:]}  local_key={lk[:4]}…{lk[-4:]}  device_id={device_id}"
    )
    return creds, mac.upper()


async def fetch_credentials_from_cloud(
    access_id: str,
    access_secret: str,
    username: str,
    password: str,
    country_code: str,
    target_mac: str,
) -> tuple[GimdowBLEDeviceCredentials, str]:
    country = next((c for c in TUYA_COUNTRIES if c.country_code == country_code), None)
    if country:
        endpoint = country.endpoint
        print(f"\n  Country: {country.name}  endpoint: {endpoint}")
    else:
        endpoint = _env("GIMDOW_CLOUD_ENDPOINT", "https://openapi.tuyaeu.com")
        print(
            f"\n  Country code {country_code!r} not found in list, using endpoint: {endpoint}"
        )

    print("  Logging in to Tuya cloud…")
    api = None
    response: dict = {}

    for app_type, auth_type in (
        (TUYA_SMART_APP, AuthType.SMART_HOME),
        (SMARTLIFE_APP, AuthType.SMART_HOME),
        ("", AuthType.CUSTOM),
    ):
        candidate = TuyaOpenAPI(endpoint, access_id, access_secret, auth_type)
        candidate.set_dev_channel("hass")
        response = await asyncio.to_thread(
            candidate.connect, username, password, country_code, app_type
        )
        if response.get(TUYA_RESPONSE_SUCCESS):
            api = candidate
            print(f"  Login OK (auth_type={auth_type.name}, app_type={app_type!r})")
            break
        print(
            f"  Tried auth_type={auth_type.name} app_type={app_type!r}: "
            f"code={response.get('code')} msg={response.get('msg')}"
        )

    if api is None:
        raise RuntimeError(
            f"Cloud login failed after all attempts.\n"
            f"Last response: {response}\n"
            f"Check: country code is correct and account exists in the Tuya app."
        )

    uid = api.token_info.uid
    print(f"  Fetching device list for uid={uid}…")
    devices_resp = await asyncio.to_thread(api.get, TUYA_API_DEVICES_URL % uid)
    devices = devices_resp.get(TUYA_RESPONSE_RESULT) or []

    target = target_mac.upper().replace(":", "").replace("-", "")
    for device in devices:
        dev_id = device.get("id", "")
        fi_resp = await asyncio.to_thread(api.get, TUYA_API_FACTORY_INFO_URL % dev_id)
        fi_result = fi_resp.get(TUYA_RESPONSE_RESULT) or []
        if not fi_result:
            continue
        raw_mac = (
            (fi_result[0].get(TUYA_FACTORY_INFO_MAC) or "")
            .replace(":", "")
            .replace("-", "")
        )
        if raw_mac.upper() != target:
            continue

        formatted_mac = ":".join(raw_mac[i : i + 2] for i in range(0, 12, 2)).upper()
        print(f"  Found matching device: {formatted_mac} (id={dev_id})")

        spec_resp = await asyncio.to_thread(
            api.get, TUYA_API_DEVICE_SPECIFICATION % dev_id
        )
        spec = spec_resp.get(TUYA_RESPONSE_RESULT) or {}
        functions = spec.get("functions") or []
        status_range = spec.get("status") or []

        creds = GimdowBLEDeviceCredentials(
            uuid=device.get("uuid", ""),
            local_key=device.get("local_key", ""),
            device_id=device.get("id", ""),
            category=device.get("category", "jtmspro"),
            product_id=device.get("product_id", "rlyxv7pe"),
            device_name=device.get("name"),
            product_model=device.get("model"),
            product_name=device.get("product_name"),
            functions=functions,
            status_range=status_range,
        )
        print(f"\n  Credentials (save to .env for next time):")
        print(f"  GIMDOW_UUID={creds.uuid}")
        print(f"  GIMDOW_LOCAL_KEY={creds.local_key}")
        print(f"  GIMDOW_DEVICE_ID={creds.device_id}")
        return creds, formatted_mac

    raise RuntimeError(f"Device with MAC {target_mac} not found in cloud account")


async def load_credentials_cloud(
    skip_prompts: bool = False,
) -> tuple[GimdowBLEDeviceCredentials, str]:
    if skip_prompts:
        return await fetch_credentials_from_cloud(
            _env("GIMDOW_CLOUD_ACCESS_ID"),
            _env("GIMDOW_CLOUD_ACCESS_SECRET"),
            _env("GIMDOW_CLOUD_USERNAME"),
            _env("GIMDOW_CLOUD_PASSWORD"),
            _env("GIMDOW_CLOUD_COUNTRY_CODE", "1"),
            _env("GIMDOW_MAC"),
        )
    print("\n  -- Tuya cloud credential fetch --")
    access_id = await _prompt("access_id", "GIMDOW_CLOUD_ACCESS_ID", secret=True)
    access_secret = await _prompt(
        "access_secret", "GIMDOW_CLOUD_ACCESS_SECRET", secret=True
    )
    username = await _prompt("username", "GIMDOW_CLOUD_USERNAME")
    password = await _prompt("password", "GIMDOW_CLOUD_PASSWORD", secret=True)
    country_code = await _prompt(
        "country_code", "GIMDOW_CLOUD_COUNTRY_CODE", default="1"
    )
    mac = await _prompt("BLE MAC address (AA:BB:CC:DD:EE:FF)", "GIMDOW_MAC")
    return await fetch_credentials_from_cloud(
        access_id, access_secret, username, password, country_code, mac
    )


def _env_manual_complete() -> bool:
    return all(
        _env(k)
        for k in ("GIMDOW_MAC", "GIMDOW_UUID", "GIMDOW_LOCAL_KEY", "GIMDOW_DEVICE_ID")
    )


def _env_cloud_complete() -> bool:
    return all(
        _env(k)
        for k in (
            "GIMDOW_MAC",
            "GIMDOW_CLOUD_ACCESS_ID",
            "GIMDOW_CLOUD_ACCESS_SECRET",
            "GIMDOW_CLOUD_USERNAME",
            "GIMDOW_CLOUD_PASSWORD",
            "GIMDOW_CLOUD_COUNTRY_CODE",
        )
    )


async def load_credentials() -> tuple[GimdowBLEDeviceCredentials, str]:
    if _env_manual_complete():
        mac = _env("GIMDOW_MAC")
        print(f"\n  .env has full manual credentials (MAC={mac})")
        confirm = (
            (await asyncio.to_thread(input, "  Proceed with these? [Y/n]: "))
            .strip()
            .lower()
        )
        if confirm in ("", "y"):
            return await load_credentials_manual(skip_prompts=True)

    elif _env_cloud_complete():
        print(
            f"\n  .env has full cloud credentials (user={_env('GIMDOW_CLOUD_USERNAME')})"
        )
        confirm = (
            (await asyncio.to_thread(input, "  Proceed with these? [Y/n]: "))
            .strip()
            .lower()
        )
        if confirm in ("", "y"):
            return await load_credentials_cloud(skip_prompts=True)

    print("\nHow do you want to provide credentials?")
    print("  [1] Enter manually (MAC, uuid, local_key, device_id)")
    print("  [2] Fetch from Tuya cloud (access_id, access_secret, username, password)")
    choice = (await asyncio.to_thread(input, "Choice [1]: ")).strip()
    if choice == "2":
        return await load_credentials_cloud()
    return await load_credentials_manual()
