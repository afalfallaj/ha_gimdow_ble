"""Tests for GimdowBLEDevice — properties, operations, and datapoint helpers."""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.gimdow_ble.gimdow_ble.const import GimdowBLEDataPointType
from custom_components.gimdow_ble.gimdow_ble.datapoints import (
    GimdowBLEDataPoints,
    GimdowBLEDeviceFunction,
    GimdowBLEEntityDescription,
)
from custom_components.gimdow_ble.gimdow_ble.device import GimdowBLEDevice
from custom_components.gimdow_ble.gimdow_ble.manager import GimdowBLEDeviceCredentials


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_device(
    *,
    local_key: str = "testlocalkey12",
    uuid: str = "test-uuid-1234",
    has_info: bool = True,
    has_advertisement: bool = True,
) -> GimdowBLEDevice:
    ble_device = MagicMock()
    ble_device.address = "AA:BB:CC:DD:EE:FF"
    ble_device.name = "GimdowLock"

    advert = MagicMock() if has_advertisement else None
    if advert:
        advert.rssi = -65
        advert.service_data = {}
        advert.manufacturer_data = {}

    dev = GimdowBLEDevice(MagicMock(), ble_device, advert)

    if has_info:
        creds = GimdowBLEDeviceCredentials(
            uuid=uuid,
            local_key=local_key,
            device_id="dev-001",
            category="jtmspro",
            product_id="rlyxv7pe",
            device_name="My Lock",
            product_model="A1 PRO MAX",
            product_name="Gimdow Lock",
            functions=[],
            status_range=[],
        )
        dev._device_info = creds
        dev._local_key = creds.local_key[:6].encode()
        dev._login_key = hashlib.md5(dev._local_key).digest()

    return dev


# ---------------------------------------------------------------------------
# TestDevicePropertiesWithInfo
# ---------------------------------------------------------------------------


class TestDevicePropertiesWithInfo:
    def test_address_delegates_to_ble_device(self) -> None:
        dev = _make_device()
        assert dev.address == "AA:BB:CC:DD:EE:FF"

    def test_name_from_device_info(self) -> None:
        dev = _make_device()
        assert dev.name == "My Lock"

    def test_rssi_from_advertisement(self) -> None:
        dev = _make_device()
        assert dev.rssi == -65

    def test_uuid_from_device_info(self) -> None:
        dev = _make_device()
        assert dev.uuid == "test-uuid-1234"

    def test_local_key_from_device_info(self) -> None:
        dev = _make_device()
        assert dev.local_key == "testlocalkey12"

    def test_category_from_device_info(self) -> None:
        dev = _make_device()
        assert dev.category == "jtmspro"

    def test_device_id_from_device_info(self) -> None:
        dev = _make_device()
        assert dev.device_id == "dev-001"

    def test_product_id_from_device_info(self) -> None:
        dev = _make_device()
        assert dev.product_id == "rlyxv7pe"

    def test_product_model_from_device_info(self) -> None:
        dev = _make_device()
        assert dev.product_model == "A1 PRO MAX"

    def test_product_name_from_device_info(self) -> None:
        dev = _make_device()
        assert dev.product_name == "Gimdow Lock"


# ---------------------------------------------------------------------------
# TestDevicePropertiesWithoutInfo
# ---------------------------------------------------------------------------


class TestDevicePropertiesWithoutInfo:
    def test_name_falls_back_to_ble_name(self) -> None:
        dev = _make_device(has_info=False)
        assert dev.name == "GimdowLock"

    def test_rssi_none_without_advertisement(self) -> None:
        dev = _make_device(has_info=False, has_advertisement=False)
        assert dev.rssi is None

    def test_uuid_empty_string(self) -> None:
        dev = _make_device(has_info=False)
        assert dev.uuid == ""

    def test_local_key_empty_string(self) -> None:
        dev = _make_device(has_info=False)
        assert dev.local_key == ""

    def test_category_empty_string(self) -> None:
        dev = _make_device(has_info=False)
        assert dev.category == ""

    def test_device_id_empty_string(self) -> None:
        dev = _make_device(has_info=False)
        assert dev.device_id == ""

    def test_product_id_empty_string(self) -> None:
        dev = _make_device(has_info=False)
        assert dev.product_id == ""

    def test_product_model_empty_string(self) -> None:
        dev = _make_device(has_info=False)
        assert dev.product_model == ""

    def test_product_name_empty_string(self) -> None:
        dev = _make_device(has_info=False)
        assert dev.product_name == ""


# ---------------------------------------------------------------------------
# TestFirmwareVersionProperties
# ---------------------------------------------------------------------------


class TestFirmwareVersionProperties:
    def test_device_version(self) -> None:
        dev = _make_device()
        dev._device_version = "3.1"
        assert dev.device_version == "3.1"

    def test_hardware_version(self) -> None:
        dev = _make_device()
        dev._hardware_version = "1.5"
        assert dev.hardware_version == "1.5"

    def test_protocol_version(self) -> None:
        dev = _make_device()
        dev._protocol_version_str = "2.0"
        assert dev.protocol_version == "2.0"

    def test_is_paired(self) -> None:
        dev = _make_device()
        dev._is_paired = True
        assert dev.is_paired is True


# ---------------------------------------------------------------------------
# TestStatusProperty
# ---------------------------------------------------------------------------


class TestStatusProperty:
    def test_empty_status_when_no_datapoints(self) -> None:
        dev = _make_device()
        assert dev.status == {}

    def test_status_includes_known_dpcode(self) -> None:
        dev = _make_device()
        f = GimdowBLEDeviceFunction(
            dp_id=46, code="switch_1", type="Boolean", values={}
        )
        dev._function["switch_1"] = f
        dev._datapoints.get_or_create(46, GimdowBLEDataPointType.DT_BOOL, True)
        assert dev.status.get("switch_1") is True


# ---------------------------------------------------------------------------
# TestGetLockState
# ---------------------------------------------------------------------------


class TestGetLockState:
    def test_returns_none_when_dp_absent(self) -> None:
        dev = _make_device()
        assert dev.get_lock_state(47) is None

    def test_dp_true_means_unlocked(self) -> None:
        """DP value=True → device is unlocked → get_lock_state returns False."""
        dev = _make_device()
        dev._datapoints.get_or_create(47, GimdowBLEDataPointType.DT_BOOL, True)
        assert dev.get_lock_state(47) is False

    def test_dp_false_means_locked(self) -> None:
        """DP value=False → device is locked → get_lock_state returns True."""
        dev = _make_device()
        dev._datapoints.get_or_create(47, GimdowBLEDataPointType.DT_BOOL, False)
        assert dev.get_lock_state(47) is True


# ---------------------------------------------------------------------------
# TestSetBleDeviceAndAdvertisement
# ---------------------------------------------------------------------------


class TestSetBleDeviceAndAdvertisement:
    def test_updates_ble_device_and_advertisement(self) -> None:
        dev = _make_device()
        new_ble = MagicMock()
        new_ble.address = "11:22:33:44:55:66"
        new_advert = MagicMock()
        dev.set_ble_device_and_advertisement_data(new_ble, new_advert)
        assert dev._ble_device is new_ble
        assert dev._advertisement_data is new_advert


# ---------------------------------------------------------------------------
# TestBuildPairingRequest
# ---------------------------------------------------------------------------


class TestBuildPairingRequest:
    def test_length_is_44_bytes(self) -> None:
        dev = _make_device()
        assert len(dev._build_pairing_request()) == 44

    def test_starts_with_uuid(self) -> None:
        dev = _make_device(uuid="abcd-1234")
        result = dev._build_pairing_request()
        assert result[:9] == b"abcd-1234"

    def test_contains_local_key_bytes(self) -> None:
        dev = _make_device(local_key="abcdefghijklmn")
        result = dev._build_pairing_request()
        local_key_bytes = dev._local_key
        key_start = len(dev._device_info.uuid)
        assert result[key_start : key_start + len(local_key_bytes)] == local_key_bytes

    def test_padded_with_zeros_to_44(self) -> None:
        dev = _make_device()
        result = dev._build_pairing_request()
        assert len(result) == 44
        assert all(b == 0 for b in result[result.rindex(b"\x00") :])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TestAppendFunctions
# ---------------------------------------------------------------------------


class TestAppendFunctions:
    def test_appends_function_by_code(self) -> None:
        dev = _make_device()
        dev.append_functions(
            [{"code": "switch_1", "dp_id": 46, "type": "Boolean", "values": None}], []
        )
        assert "switch_1" in dev.function
        assert dev.function["switch_1"].dp_id == 46

    def test_appends_status_range_by_code(self) -> None:
        dev = _make_device()
        dev.append_functions(
            [],
            [{"code": "lock_motor_state", "dp_id": 47, "type": "Enum", "values": None}],
        )
        assert "lock_motor_state" in dev.status_range

    def test_skips_entries_without_code(self) -> None:
        dev = _make_device()
        dev.append_functions([{"dp_id": 1, "type": "Boolean", "values": None}], [])
        assert len(dev.function) == 0

    def test_empty_lists_are_noop(self) -> None:
        dev = _make_device()
        dev.append_functions([], [])
        assert dev.function == {}
        assert dev.status_range == {}


# ---------------------------------------------------------------------------
# TestUpdateDescription
# ---------------------------------------------------------------------------


class TestUpdateDescription:
    def test_none_description_is_noop(self) -> None:
        dev = _make_device()
        dev.update_description(None)

    def test_values_override_replaces_existing(self) -> None:
        dev = _make_device()
        dev._function["switch_1"] = GimdowBLEDeviceFunction(
            code="switch_1", dp_id=46, type="Boolean", values={"range": ["a"]}
        )
        desc = GimdowBLEEntityDescription()
        desc.function = [
            {"code": "switch_1", "dp_id": 46, "type": "Boolean", "values": None}
        ]
        desc.status_range = []
        desc.values_overrides = {"switch_1": {"range": ["locked", "unlocked"]}}
        desc.values_defaults = None
        dev.update_description(desc)
        assert dev.function["switch_1"].values == {"range": ["locked", "unlocked"]}

    def test_values_default_sets_when_empty(self) -> None:
        dev = _make_device()
        dev._function["switch_1"] = GimdowBLEDeviceFunction(
            code="switch_1", dp_id=46, type="Boolean", values=None
        )
        desc = GimdowBLEEntityDescription()
        desc.function = []
        desc.status_range = []
        desc.values_overrides = None
        desc.values_defaults = {"switch_1": {"range": ["on", "off"]}}
        dev.update_description(desc)
        assert dev.function["switch_1"].values == {"range": ["on", "off"]}

    def test_values_default_does_not_override_existing(self) -> None:
        dev = _make_device()
        original = {"range": ["existing"]}
        dev._function["switch_1"] = GimdowBLEDeviceFunction(
            code="switch_1", dp_id=46, type="Boolean", values=original
        )
        desc = GimdowBLEEntityDescription()
        desc.function = []
        desc.status_range = []
        desc.values_overrides = None
        desc.values_defaults = {"switch_1": {"range": ["new"]}}
        dev.update_description(desc)
        assert dev.function["switch_1"].values == original


# ---------------------------------------------------------------------------
# TestDecodeAdvertisementData
# ---------------------------------------------------------------------------


class TestDecodeAdvertisementData:
    def test_no_advertisement_data_is_noop(self) -> None:
        dev = _make_device()
        dev._advertisement_data = None
        dev._decode_advertisement_data()

    def test_empty_service_and_manufacturer_data(self) -> None:
        dev = _make_device()
        dev._advertisement_data.service_data = {}
        dev._advertisement_data.manufacturer_data = {}
        dev._decode_advertisement_data()

    def test_manufacturer_data_sets_protocol_version_and_is_bound(self) -> None:
        from custom_components.gimdow_ble.gimdow_ble.const import MANUFACTURER_DATA_ID

        dev = _make_device()
        mfr_data = bytes([0x80, 3]) + b"\x00" * 5  # is_bound=True, protocol=3
        dev._advertisement_data.manufacturer_data = {MANUFACTURER_DATA_ID: mfr_data}
        dev._decode_advertisement_data()
        assert dev._is_bound is True
        assert dev._protocol_version == 3


# ---------------------------------------------------------------------------
# TestSendControlDatapoint
# ---------------------------------------------------------------------------


class TestSendControlDatapoint:
    async def test_creates_datapoint_and_sets_value(self) -> None:
        dev = _make_device()
        dev._datapoints._send_callback = AsyncMock()
        dp = await dev.send_control_datapoint(46, True)
        assert dp is not None
        assert dp.value is True
        dev._datapoints._send_callback.assert_awaited_once_with([46])

    async def test_returns_datapoint_object(self) -> None:
        dev = _make_device()
        dev._datapoints._send_callback = AsyncMock()
        dp = await dev.send_control_datapoint(46, False)
        assert dp.id == 46


# ---------------------------------------------------------------------------
# TestPropertyAccessors
# ---------------------------------------------------------------------------


class TestPropertyAccessors:
    def test_function_property_returns_dict(self) -> None:
        dev = _make_device()
        assert isinstance(dev.function, dict)

    def test_status_range_property_returns_dict(self) -> None:
        dev = _make_device()
        assert isinstance(dev.status_range, dict)

    def test_datapoints_property_returns_collection(self) -> None:
        dev = _make_device()
        assert isinstance(dev.datapoints, GimdowBLEDataPoints)

    def test_get_or_create_datapoint_creates_and_returns(self) -> None:
        dev = _make_device()
        dp = dev.get_or_create_datapoint(99, GimdowBLEDataPointType.DT_BOOL, True)
        assert dp is not None
        assert dp.id == 99
        assert dp.value is True

    def test_get_or_create_datapoint_returns_existing(self) -> None:
        dev = _make_device()
        dp1 = dev.get_or_create_datapoint(99, GimdowBLEDataPointType.DT_BOOL, True)
        dp2 = dev.get_or_create_datapoint(99, GimdowBLEDataPointType.DT_BOOL, False)
        assert dp1 is dp2


# ---------------------------------------------------------------------------
# TestSendCommandWaitStateEcho
# ---------------------------------------------------------------------------


class TestSendCommandWaitStateEcho:
    async def test_returns_true_when_echo_arrives(self) -> None:
        dev = _make_device()
        dev.send_control_datapoint = AsyncMock()

        async def _trigger_echo(*args, **kwargs):
            dp = dev._datapoints.get_or_create(
                47, GimdowBLEDataPointType.DT_BOOL, False
            )
            for cb in list(dev._callbacks):
                cb([dp])

        dev.send_control_datapoint.side_effect = _trigger_echo
        result = await dev.send_command_wait_state_echo(46, True, 47, timeout=5.0)
        assert result is True

    async def test_returns_false_on_timeout(self) -> None:
        dev = _make_device()
        dev.send_control_datapoint = AsyncMock()
        result = await dev.send_command_wait_state_echo(46, True, 47, timeout=0.05)
        assert result is False

    async def test_callback_removed_after_success(self) -> None:
        dev = _make_device()
        dev.send_control_datapoint = AsyncMock()
        initial_cb_count = len(dev._callbacks)

        async def _trigger_echo(*args, **kwargs):
            dp = dev._datapoints.get_or_create(
                47, GimdowBLEDataPointType.DT_BOOL, False
            )
            for cb in list(dev._callbacks):
                cb([dp])

        dev.send_control_datapoint.side_effect = _trigger_echo
        await dev.send_command_wait_state_echo(46, True, 47, timeout=5.0)
        assert len(dev._callbacks) == initial_cb_count

    async def test_callback_removed_after_timeout(self) -> None:
        dev = _make_device()
        dev.send_control_datapoint = AsyncMock()
        initial_cb_count = len(dev._callbacks)
        await dev.send_command_wait_state_echo(46, True, 47, timeout=0.05)
        assert len(dev._callbacks) == initial_cb_count
