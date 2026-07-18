"""Tests for number.py — GimdowBLENumber restore-on-restart behavior.

Regression coverage for the "unavailable at shutdown" restore bug: number.py
used to parse RestoreEntity's stringified `.state`, which HA overwrites with
"unavailable" whenever the entity's `available` property is False at
shutdown — true whenever a sleeping BLE lock is past its disconnect grace
period, which is common, not rare, for this integration. `float("unavailable")`
raised ValueError, silently discarding the real value. The fix reads
`extra_restore_state_data` (via RestoreNumber) instead, which Home Assistant
populates independently of entity availability.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.gimdow_ble.gimdow_ble import GimdowBLEDataPointType
from custom_components.gimdow_ble.number import GimdowBLENumber, GimdowBLENumberMapping


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


def _make_number(device: MagicMock, *, coefficient: float = 1.0) -> GimdowBLENumber:
    mapping = GimdowBLENumberMapping(
        dp_id=36,
        description=SimpleNamespace(key="auto_lock_time", translation_key=None),
        coefficient=coefficient,
    )
    product = SimpleNamespace(name="A1 PRO MAX", manufacturer="Gimdow", is_lock=True)
    entity = GimdowBLENumber(
        coordinator=MagicMock(),
        device=device,
        product=product,
        mapping=mapping,
        data=MagicMock(),
    )
    entity.hass = MagicMock()
    return entity


class TestAutoLockTimeRestore:
    async def test_restores_value_into_datapoint_cache(self) -> None:
        device = _make_device()
        entity = _make_number(device)
        entity._stub_last_number_data = SimpleNamespace(native_value=300.0)

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_called_once_with(
            36, GimdowBLEDataPointType.DT_VALUE, 300
        )

    async def test_no_restore_data_does_not_touch_datapoint_cache(self) -> None:
        """Fresh install / never-restored entity — no crash, no seed."""
        device = _make_device()
        entity = _make_number(device)
        entity._stub_last_number_data = None

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_not_called()

    async def test_extra_data_present_but_native_value_none_does_not_seed(self) -> None:
        device = _make_device()
        entity = _make_number(device)
        entity._stub_last_number_data = SimpleNamespace(native_value=None)

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_not_called()

    async def test_applies_coefficient(self) -> None:
        device = _make_device()
        entity = _make_number(device, coefficient=10.0)
        entity._stub_last_number_data = SimpleNamespace(native_value=30.0)

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_called_once_with(
            36, GimdowBLEDataPointType.DT_VALUE, 300
        )

    async def test_non_lock_product_skips_restore(self) -> None:
        device = _make_device()
        entity = _make_number(device)
        entity._product = SimpleNamespace(
            name="Other", manufacturer="Gimdow", is_lock=False
        )
        entity._stub_last_number_data = SimpleNamespace(native_value=300.0)

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_not_called()
