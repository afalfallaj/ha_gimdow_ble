"""The Gimdow BLE integration."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from .countries import Country, TUYA_COUNTRIES

DOMAIN: Final = "gimdow_ble"

# Gimdow A1 Pro Max product identity — single source of truth for all platform files
GIMDOW_CATEGORY: Final = "jtmspro"
GIMDOW_PRODUCT_ID: Final = "rlyxv7pe"
GIMDOW_PRODUCT_MODEL: Final = "A1 PRO MAX"
GIMDOW_PRODUCT_NAME: Final = "Gimdow Lock"

DEVICE_DEF_MANUFACTURER: Final = "Gimdow"
# 10-minute grace before marking entities unavailable: intentional deviation from
# local_push convention. Avoids flapping during brief BLE dropout / phone handoff.
SET_DISCONNECTED_DELAY = 10 * 60

CONF_UUID: Final = "uuid"
CONF_LOCAL_KEY: Final = "local_key"
CONF_CATEGORY: Final = "category"
CONF_PRODUCT_ID: Final = "product_id"
CONF_DEVICE_NAME: Final = "device_name"
CONF_PRODUCT_MODEL: Final = "product_model"
CONF_PRODUCT_NAME: Final = "product_name"
CONF_FUNCTIONS: Final = "functions"
CONF_STATUS_RANGE: Final = "status_range"
CONF_DOOR_SENSOR: Final = "door_sensor"
CONF_ADAPTER: Final = "adapter"

ACTION_SOURCE_AUTO: Final = "auto_lock"
ACTION_SOURCE_HA: Final = "ha"

CONF_UNKNOWN_STATE_ACTION: Final = "unknown_state_action"
CONF_TRANSITION_TIMEOUT: Final = "transition_timeout"
CONF_AUTO_LOCK_DELAY_FALLBACK: Final = "auto_lock_delay_fallback"
DEFAULT_AUTO_LOCK_DELAY_FALLBACK: Final = 30
UNKNOWN_STATE_ACTION_CONFIRM_LAST: Final = "confirm_last"
UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION: Final = "double_on_action"
UNKNOWN_STATE_ACTION_FORCE_LOCK_TWICE: Final = "force_lock_twice"
DEFAULT_UNKNOWN_STATE_ACTION: Final = UNKNOWN_STATE_ACTION_CONFIRM_LAST

OPTIONS_ONLY_KEYS: Final = frozenset(
    {
        CONF_DOOR_SENSOR,
        CONF_ADAPTER,
        CONF_UNKNOWN_STATE_ACTION,
        CONF_TRANSITION_TIMEOUT,
        CONF_AUTO_LOCK_DELAY_FALLBACK,
    }
)

CONF_AUTH_TYPE: Final = "auth_type"

CONF_ENDPOINT: Final = "endpoint"
CONF_ACCESS_ID: Final = "access_id"
CONF_ACCESS_SECRET: Final = "access_secret"
CONF_APP_TYPE: Final = "tuya_app_type"
TUYA_RESPONSE_CODE: Final = "code"
TUYA_RESPONSE_RESULT: Final = "result"
TUYA_RESPONSE_MSG: Final = "msg"
TUYA_RESPONSE_SUCCESS: Final = "success"


TUYA_DOMAIN: Final = "tuya"

TUYA_SMART_APP: Final = "tuyaSmart"
SMARTLIFE_APP: Final = "smartlife"

TUYA_API_DEVICES_URL: Final = "/v1.0/users/%s/devices"
TUYA_API_FACTORY_INFO_URL: Final = "/v1.0/iot-03/devices/factory-infos?device_ids=%s"
TUYA_API_DEVICE_SPECIFICATION: Final = "/v1.1/devices/%s/specifications"
TUYA_FACTORY_INFO_MAC: Final = "mac"

BATTERY_STATE_LOW: Final = "low"
BATTERY_STATE_NORMAL: Final = "normal"
BATTERY_STATE_MEDIUM: Final = "medium"
BATTERY_STATE_HIGH: Final = "high"
BATTERY_STATE_POWEROFF: Final = "poweroff"


class DPType(StrEnum):
    """Data point types."""

    BOOLEAN = "Boolean"
    ENUM = "Enum"
    INTEGER = "Integer"
    JSON = "Json"
    RAW = "Raw"
    STRING = "String"
