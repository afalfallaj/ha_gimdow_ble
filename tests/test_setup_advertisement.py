"""Set the advertised protocol before the first encrypted BLE handshake."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.gimdow_ble import async_setup_entry


@pytest.mark.parametrize("has_advertisement", [True, False])
async def test_initial_advertisement_passed_to_device(has_advertisement):
    import custom_components.gimdow_ble as integration

    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = MagicMock()
    entry.data = {"address": "AA:BB:CC:DD:EE:FF"}
    entry.options = {}
    advertisement = object()
    info = SimpleNamespace(advertisement=advertisement) if has_advertisement else None
    ble_device = MagicMock()
    device = MagicMock()
    device.initialize = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    with (
        patch.object(integration.bluetooth, "async_ble_device_from_address", return_value=ble_device),
        patch.object(integration.bluetooth, "async_last_service_info", return_value=info),
        patch.object(integration, "HASSGimdowBLEDeviceManager") as manager_factory,
        patch.object(integration, "GimdowBLEDevice", return_value=device) as factory,
        patch.object(integration, "get_device_product_info", return_value=MagicMock()),
        patch.object(integration, "GimdowBLECoordinator", return_value=coordinator),
    ):
        assert await async_setup_entry(hass, entry)
        factory.assert_called_once_with(
            manager_factory.return_value, ble_device,
            advertisement_data=advertisement if has_advertisement else None,
        )
