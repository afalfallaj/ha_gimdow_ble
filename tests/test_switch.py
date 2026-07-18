"""Tests for switch.py — GimdowBLEVirtualAutoLockSwitch restore-on-restart behavior.

Same "unavailable at shutdown" restore bug as number.py (see test_number.py),
applied to the virtual auto-lock switch: the old code only restored when
last_state.state was literally "on" or "off", silently leaving auto-lock off
if the switch was unavailable when HA stopped — which is common for a
sleeping BLE lock. The fix persists via extra_restore_state_data instead,
which HA captures independently of entity availability.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.gimdow_ble.gimdow_ble import GimdowBLEDataPointType
from custom_components.gimdow_ble.switch import (
    GimdowBLESwitchMapping,
    GimdowBLEVirtualAutoLockSwitch,
    _VirtualAutoLockExtraData,
)


def _make_device(*, address: str = "AA:BB:CC:DD:EE:FF") -> MagicMock:
    dev = MagicMock()
    dev.address = address
    dev.category = "jtmspro"
    dev.product_id = "rlyxv7pe"
    dev.name = "My Lock"
    dev.device_id = "dev-001"
    dev.hardware_version = "1.0"
    dev.device_version = "3.1"
    dev.protocol_version = "2.0"
    dev.product_model = "A1 PRO MAX"
    dev.product_name = "Gimdow Lock"
    dev.datapoints = MagicMock()
    return dev


def _make_switch(device: MagicMock, data: MagicMock) -> GimdowBLEVirtualAutoLockSwitch:
    mapping = GimdowBLESwitchMapping(
        dp_id=33,
        description=SimpleNamespace(key="auto_lock", translation_key=None),
    )
    product = SimpleNamespace(name="A1 PRO MAX", manufacturer="Gimdow", is_lock=True)
    entity = GimdowBLEVirtualAutoLockSwitch(
        coordinator=MagicMock(),
        device=device,
        product=product,
        mapping=mapping,
        data=data,
    )
    entity.hass = MagicMock()
    return entity


class TestVirtualAutoLockExtraDataRoundTrip:
    """The JSON round trip is exactly what HA's storage layer does: as_dict()
    at shutdown, from_dict() of the (JSON-serialized-and-reloaded) result on
    restart. Both directions need to agree independently of the pytest process."""

    @pytest.mark.parametrize("is_on", [True, False])
    def test_round_trips(self, is_on: bool) -> None:
        original = _VirtualAutoLockExtraData(is_on)
        restored = _VirtualAutoLockExtraData.from_dict(original.as_dict())
        assert restored == _VirtualAutoLockExtraData(is_on)

    def test_from_dict_missing_key_returns_none(self) -> None:
        assert _VirtualAutoLockExtraData.from_dict({}) is None


class TestVirtualAutoLockSwitchRestore:
    async def test_restores_on_state(self) -> None:
        device = _make_device()
        data = MagicMock()
        data.virtual_auto_lock = False
        entity = _make_switch(device, data)
        entity._stub_last_extra_data = _VirtualAutoLockExtraData(True)

        await entity.async_added_to_hass()

        assert data.virtual_auto_lock is True
        device.datapoints.get_or_create.assert_called_once_with(
            33, GimdowBLEDataPointType.DT_BOOL, False
        )

    async def test_restores_off_state(self) -> None:
        device = _make_device()
        data = MagicMock()
        data.virtual_auto_lock = True
        entity = _make_switch(device, data)
        entity._stub_last_extra_data = _VirtualAutoLockExtraData(False)

        await entity.async_added_to_hass()

        assert data.virtual_auto_lock is False

    async def test_no_restore_data_leaves_default_untouched(self) -> None:
        """Fresh install / never-restored entity — no crash, default stays False."""
        device = _make_device()
        data = MagicMock()
        data.virtual_auto_lock = False
        entity = _make_switch(device, data)
        entity._stub_last_extra_data = None

        await entity.async_added_to_hass()

        assert data.virtual_auto_lock is False
        device.datapoints.get_or_create.assert_not_called()

    async def test_non_lock_product_skips_restore(self) -> None:
        device = _make_device()
        data = MagicMock()
        data.virtual_auto_lock = False
        entity = _make_switch(device, data)
        entity._product = SimpleNamespace(
            name="Other", manufacturer="Gimdow", is_lock=False
        )
        entity._stub_last_extra_data = _VirtualAutoLockExtraData(True)

        await entity.async_added_to_hass()

        assert data.virtual_auto_lock is False
