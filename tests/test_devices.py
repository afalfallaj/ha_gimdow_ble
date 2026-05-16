"""Tests for devices.py — utility functions and GimdowBLECoordinator."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.gimdow_ble.devices import (
    GimdowBLECoordinator,
    GimdowBLEProductInfo,
    get_device_info,
    get_device_product_info,
    get_product_info_by_ids,
    get_short_address,
)
from homeassistant.helpers.update_coordinator import UpdateFailed


def _make_device(
    *,
    address: str = "AA:BB:CC:DD:EE:FF",
    category: str = "jtmspro",
    product_id: str = "rlyxv7pe",
) -> MagicMock:
    dev = MagicMock()
    dev.address = address
    dev.category = category
    dev.product_id = product_id
    dev.name = "My Lock"
    dev.device_id = "dev-001"
    dev.hardware_version = "1.0"
    dev.device_version = "3.1"
    dev.protocol_version = "2.0"
    dev.product_model = "A1 PRO MAX"
    dev.product_name = "Gimdow Lock"
    dev.datapoints = MagicMock()
    dev.register_connected_callback.return_value = MagicMock()
    dev.register_callback.return_value = MagicMock()
    dev.register_disconnected_callback.return_value = MagicMock()
    dev.update = AsyncMock()
    return dev


# ---------------------------------------------------------------------------
# TestGetShortAddress
# ---------------------------------------------------------------------------


class TestGetShortAddress:
    def test_standard_mac_last_three_octets(self) -> None:
        assert get_short_address("AA:BB:CC:DD:EE:FF") == "DDEEFF"

    def test_hyphen_separated_mac(self) -> None:
        assert get_short_address("AA-BB-CC-DD-EE-FF") == "DDEEFF"

    def test_result_is_6_chars(self) -> None:
        assert len(get_short_address("11:22:33:44:55:66")) == 6

    def test_uppercased(self) -> None:
        result = get_short_address("aa:bb:cc:dd:ee:ff")
        assert result == result.upper()


# ---------------------------------------------------------------------------
# TestGetProductInfoByIds
# ---------------------------------------------------------------------------


class TestGetProductInfoByIds:
    def test_known_category_and_product(self) -> None:
        info = get_product_info_by_ids("jtmspro", "rlyxv7pe")
        assert info is not None
        assert info.name == "A1 PRO MAX"

    def test_unknown_category_returns_none(self) -> None:
        assert get_product_info_by_ids("unknown_cat", "any") is None

    def test_unknown_product_falls_back_to_none(self) -> None:
        assert get_product_info_by_ids("jtmspro", "unknown_product") is None

    def test_get_device_product_info_delegates(self) -> None:
        dev = _make_device(category="jtmspro", product_id="rlyxv7pe")
        info = get_device_product_info(dev)
        assert info is not None
        assert info.name == "A1 PRO MAX"


# ---------------------------------------------------------------------------
# TestGetDeviceInfo
# ---------------------------------------------------------------------------


class TestGetDeviceInfo:
    def test_returns_device_info_object(self) -> None:
        assert get_device_info(_make_device()) is not None

    def test_bt_address_in_connections(self) -> None:
        from homeassistant.helpers import device_registry as dr

        dev = _make_device(address="AA:BB:CC:DD:EE:FF")
        with patch("custom_components.gimdow_ble.devices.DeviceInfo") as MockDI:
            MockDI.return_value = MagicMock()
            get_device_info(dev)
        kwargs = MockDI.call_args.kwargs
        assert (dr.CONNECTION_BLUETOOTH, "AA:BB:CC:DD:EE:FF") in kwargs["connections"]

    def test_sw_version_includes_protocol(self) -> None:
        dev = _make_device()
        with patch("custom_components.gimdow_ble.devices.DeviceInfo") as MockDI:
            MockDI.return_value = MagicMock()
            get_device_info(dev)
        assert "protocol" in MockDI.call_args.kwargs["sw_version"]

    def test_unknown_product_falls_back_gracefully(self) -> None:
        assert (
            get_device_info(_make_device(category="unknown", product_id="unknown"))
            is not None
        )


# ---------------------------------------------------------------------------
# TestCoordinatorUpdate
# ---------------------------------------------------------------------------


class TestCoordinatorUpdate:
    async def test_successful_update_returns_device(self, hass) -> None:
        dev = _make_device()
        result = await GimdowBLECoordinator(hass, dev)._async_update_data()
        assert result is dev

    async def test_timeout_error_returns_device_without_raising(self, hass) -> None:
        # Sleeping BLE locks produce TimeoutError on the first poll.
        # _async_update_data must return the device gracefully, not raise UpdateFailed.
        dev = _make_device()
        dev.update = AsyncMock(side_effect=asyncio.TimeoutError())
        result = await GimdowBLECoordinator(hass, dev)._async_update_data()
        assert result is dev

    async def test_timeout_via_wait_for_returns_device_without_raising(
        self, hass
    ) -> None:
        dev = _make_device()
        coordinator = GimdowBLECoordinator(hass, dev)
        with patch(
            "custom_components.gimdow_ble.devices.asyncio.wait_for"
        ) as mock_wait:
            mock_wait.side_effect = asyncio.TimeoutError
            result = await coordinator._async_update_data()
        assert result is dev

    async def test_unexpected_exception_wrapped_in_update_failed(self, hass) -> None:
        dev = _make_device()
        dev.update = AsyncMock(side_effect=RuntimeError("BLE error"))
        with pytest.raises(UpdateFailed, match="BLE error"):
            await GimdowBLECoordinator(hass, dev)._async_update_data()


# ---------------------------------------------------------------------------
# TestCoordinatorCallbackCleanup
# ---------------------------------------------------------------------------


class TestCoordinatorCallbackCleanup:
    def test_callbacks_registered_on_init(self, hass) -> None:
        dev = _make_device()
        GimdowBLECoordinator(hass, dev)
        dev.register_connected_callback.assert_called_once()
        dev.register_callback.assert_called_once()
        dev.register_disconnected_callback.assert_called_once()

    def test_unregister_callbacks_stored(self, hass) -> None:
        coordinator = GimdowBLECoordinator(hass, _make_device())
        assert hasattr(coordinator, "_unregister_callbacks")
        assert len(coordinator._unregister_callbacks) == 3

    def test_stop_calls_all_unregisters(self, hass) -> None:
        dev = _make_device()
        unreg1 = dev.register_connected_callback.return_value
        unreg2 = dev.register_callback.return_value
        unreg3 = dev.register_disconnected_callback.return_value
        coordinator = GimdowBLECoordinator(hass, dev)
        coordinator.stop()
        unreg1.assert_called_once()
        unreg2.assert_called_once()
        unreg3.assert_called_once()
        assert coordinator._unregister_callbacks == []

    def test_stop_cancels_unsub_disconnect(self, hass) -> None:
        coordinator = GimdowBLECoordinator(hass, _make_device())
        unsub = MagicMock()
        coordinator._unsub_disconnect = unsub
        coordinator.stop()
        unsub.assert_called_once()
        assert coordinator._unsub_disconnect is None

    def test_set_disconnected_marks_disconnected(self, hass) -> None:
        coord = GimdowBLECoordinator(hass, _make_device())
        coord._disconnected = False
        coord._set_disconnected(None)
        assert coord.connected is False

    def test_async_handle_disconnect_schedules_delay(self, hass) -> None:
        coord = GimdowBLECoordinator(hass, _make_device())
        with patch("custom_components.gimdow_ble.devices.async_call_later") as mock_acl:
            mock_acl.return_value = MagicMock()
            coord._async_handle_disconnect()
        mock_acl.assert_called_once()
        assert coord._unsub_disconnect is not None

    def test_async_handle_disconnect_does_not_double_schedule(self, hass) -> None:
        coord = GimdowBLECoordinator(hass, _make_device())
        with patch("custom_components.gimdow_ble.devices.async_call_later") as mock_acl:
            mock_acl.return_value = MagicMock()
            coord._async_handle_disconnect()
            first_unsub = coord._unsub_disconnect
            coord._async_handle_disconnect()
        assert coord._unsub_disconnect is first_unsub
        assert mock_acl.call_count == 1


# ---------------------------------------------------------------------------
# TestReconnectSchedulesDeviceUpdate
# ---------------------------------------------------------------------------


class TestReconnectSchedulesDeviceUpdate:
    def test_initial_disconnected_state_is_true(self, hass) -> None:
        coordinator = GimdowBLECoordinator(hass, _make_device())
        assert coordinator._disconnected is True
        assert coordinator.connected is False

    async def test_connected_after_disconnect_calls_update(self, hass) -> None:
        dev = _make_device()
        coordinator = GimdowBLECoordinator(hass, dev)
        coordinator._disconnected = True
        connected_cb = dev.register_connected_callback.call_args[0][0]
        connected_cb()
        await asyncio.sleep(0)
        dev.schedule_update.assert_called_once()

    async def test_connected_when_not_disconnected_skips_update(self, hass) -> None:
        dev = _make_device()
        coordinator = GimdowBLECoordinator(hass, dev)
        coordinator._disconnected = False
        hass.async_create_task.reset_mock()
        connected_cb = dev.register_connected_callback.call_args[0][0]
        connected_cb()
        await asyncio.sleep(0)
        hass.async_create_task.assert_not_called()

    async def test_async_handle_connect_cancels_pending_disconnect(self, hass) -> None:
        dev = _make_device()
        hass.async_create_task.side_effect = asyncio.create_task
        coord = GimdowBLECoordinator(hass, dev)
        coord._disconnected = True
        fake_unsub = MagicMock()
        coord._unsub_disconnect = fake_unsub
        coord._async_handle_connect()
        await asyncio.sleep(0)
        fake_unsub.assert_called_once()
