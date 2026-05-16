"""Global test fixtures — sys.modules stubs for homeassistant, tuya_iot, and bleak.

All stubs are injected at module-import time (top level, not inside fixtures)
so that they are in place before any test file is imported.
"""

from __future__ import annotations

# Exclude the interactive harness script — it requires real BLE hardware and
# is not a pytest test file.
collect_ignore = ["test_gimdow_interactive.py"]

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Exception / base stubs that must be real Python classes
# ---------------------------------------------------------------------------


class _UpdateFailed(Exception):
    """Stand-in for homeassistant.helpers.update_coordinator.UpdateFailed."""


class _HomeAssistant:
    """Minimal stand-in for homeassistant.core.HomeAssistant."""


# ---------------------------------------------------------------------------
# DataUpdateCoordinator stub — sets self.hass so subclasses can use it
# ---------------------------------------------------------------------------


class _DataUpdateCoordinator:
    """Minimal stand-in that mirrors the parts GimdowBLECoordinator relies on."""

    def __init__(self, hass, logger=None, *, name=None, update_interval=None, **kwargs):
        self.hass = hass
        self.logger = logger
        self.name = name

    def async_update_listeners(self) -> None:
        pass

    def async_set_updated_data(self, data) -> None:
        pass

    def __class_getitem__(cls, item):
        return cls


class _CoordinatorEntity:
    """Minimal stand-in for homeassistant.helpers.update_coordinator.CoordinatorEntity."""

    def __init__(self, coordinator):
        pass


# ---------------------------------------------------------------------------
# homeassistant.components.bluetooth.match stub
# ---------------------------------------------------------------------------

_ha_bluetooth_match = MagicMock()
_ha_bluetooth_match.ADDRESS = "address"
_ha_bluetooth_match.BluetoothCallbackMatcher = MagicMock()

# ---------------------------------------------------------------------------
# homeassistant stub tree
# ---------------------------------------------------------------------------

_ha_core = MagicMock()
_ha_core.HomeAssistant = _HomeAssistant
_ha_core.callback = lambda f: f  # passthrough decorator
_ha_core.CALLBACK_TYPE = type(None)
_ha_core.Event = MagicMock()

_ha_event = MagicMock()
_ha_event.async_call_later = MagicMock(return_value=MagicMock())

_ha_udc = MagicMock()
_ha_udc.UpdateFailed = _UpdateFailed
_ha_udc.DataUpdateCoordinator = _DataUpdateCoordinator
_ha_udc.CoordinatorEntity = _CoordinatorEntity

_ha_const = MagicMock()
_ha_const.CONF_ADDRESS = "address"
_ha_const.CONF_DEVICE_ID = "device_id"
_ha_const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
_ha_const.Platform = MagicMock()

_ha_exc = MagicMock()
_ha_exc.ConfigEntryNotReady = Exception


# BleakNotFoundError must be a distinct class (not OSError) so that
# `except BleakNotFoundError: raise` in _send_packet does NOT absorb plain
# OSError before the retry handler gets a chance to run.
class _BleakNotFoundError(OSError):
    """Stub for bleak_retry_connector.BleakNotFoundError."""


# bleak_retry_connector needs get_device as an AsyncMock so it's awaitable
_bleak_rc = MagicMock()
_bleak_rc.BLEAK_BACKOFF_TIME = 0.25
_bleak_rc.BLEAK_RETRY_EXCEPTIONS = (OSError,)
_bleak_rc.BleakError = OSError
_bleak_rc.BleakNotFoundError = _BleakNotFoundError
_bleak_rc.BleakClientWithServiceCache = MagicMock
_bleak_rc.establish_connection = AsyncMock()
_bleak_rc.get_device = AsyncMock(return_value=None)

_bleak_exc = MagicMock()


# BleakDBusError must be a distinct subclass of OSError so that
# `except BleakDBusError` does NOT catch plain OSError — mirroring production
# where BleakDBusError(BleakError) is separate from the generic OSError path.
class _BleakDBusError(OSError):
    """Stub for bleak.exc.BleakDBusError."""


_bleak_exc.BleakError = OSError
_bleak_exc.BleakDBusError = _BleakDBusError

sys.modules.update(
    {
        # homeassistant core
        "homeassistant": MagicMock(),
        "homeassistant.core": _ha_core,
        "homeassistant.const": _ha_const,
        "homeassistant.exceptions": _ha_exc,
        "homeassistant.config_entries": MagicMock(),
        # helpers
        "homeassistant.helpers": MagicMock(),
        "homeassistant.helpers.event": _ha_event,
        "homeassistant.helpers.update_coordinator": _ha_udc,
        "homeassistant.helpers.entity": MagicMock(),
        "homeassistant.helpers.entity_platform": MagicMock(),
        "homeassistant.helpers.restore_state": MagicMock(),
        "homeassistant.helpers.dispatcher": MagicMock(),
        "homeassistant.helpers.device_registry": MagicMock(),
        # components — top-level and deep submodules
        "homeassistant.components": MagicMock(),
        "homeassistant.components.bluetooth": MagicMock(),
        "homeassistant.components.bluetooth.match": _ha_bluetooth_match,
        "homeassistant.components.sensor": MagicMock(),
        "homeassistant.components.binary_sensor": MagicMock(),
        "homeassistant.components.lock": MagicMock(),
        "homeassistant.components.button": MagicMock(),
        "homeassistant.components.switch": MagicMock(),
        "homeassistant.components.select": MagicMock(),
        "homeassistant.components.number": MagicMock(),
        "homeassistant.components.persistent_notification": MagicMock(),
        # home_assistant_bluetooth — separate PyPI package
        "home_assistant_bluetooth": MagicMock(),
    }
)

# ---------------------------------------------------------------------------
# tuya_iot stub
# ---------------------------------------------------------------------------

_tuya = MagicMock()
_tuya.TuyaCloudOpenAPIEndpoint = MagicMock()
_tuya.TuyaOpenAPI = MagicMock()
_tuya.TuyaOpenMQ = MagicMock()
_tuya.AuthType = MagicMock()
_tuya.TuyaDevice = MagicMock()
_tuya.TuyaDeviceManager = MagicMock()
sys.modules["tuya_iot"] = _tuya

# ---------------------------------------------------------------------------
# bleak / bleak_retry_connector stubs
# ---------------------------------------------------------------------------

sys.modules.update(
    {
        "bleak": MagicMock(),
        "bleak.backends": MagicMock(),
        "bleak.backends.device": MagicMock(),
        "bleak.backends.scanner": MagicMock(),
        "bleak.exc": _bleak_exc,
        "bleak_retry_connector": _bleak_rc,
    }
)

# ---------------------------------------------------------------------------
# pycountry stub
# ---------------------------------------------------------------------------

sys.modules["pycountry"] = MagicMock()

# ---------------------------------------------------------------------------
# typing_extensions — may not be present in minimal envs
# ---------------------------------------------------------------------------

try:
    import typing_extensions  # noqa: F401
except ImportError:
    sys.modules["typing_extensions"] = MagicMock()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hass():
    """Minimal HA mock — async_create_task routes through asyncio.create_task."""
    mock = MagicMock()
    mock.async_create_task.side_effect = asyncio.create_task
    return mock


@pytest.fixture
def mock_config_entry_data() -> dict:
    """Minimal config entry data for a manually-configured device."""
    return {
        "address": "AA:BB:CC:DD:EE:FF",
        "uuid": "test-uuid-1234567890abcdef",
        "local_key": "testlocalkey1234",
        "device_id": "test-device-id-001",
        "category": "jtmspro",
        "product_id": "rlyxv7pe",
        "device_name": "Test Lock",
        "product_model": "A1 PRO MAX",
        "product_name": "Gimdow Lock",
        "functions": [],
        "status_range": [],
    }
