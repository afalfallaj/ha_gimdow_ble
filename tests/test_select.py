"""Tests for select.py — GimdowBLESelect restore-on-restart behavior.

Same "unavailable at shutdown" restore bug as number.py/switch.py, applied to
the beep_volume select: the old code only restored when last_state.state was
a real option string, silently discarding it when the entity was unavailable
when HA stopped (common for a sleeping BLE lock past its disconnect grace
period — "unavailable" is neither "unknown" nor a valid option). The fix
persists via extra_restore_state_data instead.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.gimdow_ble.gimdow_ble import GimdowBLEDataPointType
from custom_components.gimdow_ble.select import (
    GimdowBLESelect,
    GimdowBLESelectMapping,
    _SelectExtraData,
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


def _make_select(
    device: MagicMock, *, value_mapping: dict[str, str] | None = None
) -> GimdowBLESelect:
    mapping = GimdowBLESelectMapping(
        dp_id=31,
        description=SimpleNamespace(
            key="beep_volume",
            translation_key=None,
            options=["mute", "normal"],
        ),
        value_mapping=value_mapping,
    )
    product = SimpleNamespace(name="A1 PRO MAX", manufacturer="Gimdow", is_lock=True)
    entity = GimdowBLESelect(
        coordinator=MagicMock(), device=device, product=product, mapping=mapping
    )
    entity.hass = MagicMock()
    return entity


class TestSelectExtraDataRoundTrip:
    def test_round_trips(self) -> None:
        original = _SelectExtraData("normal")
        restored = _SelectExtraData.from_dict(original.as_dict())
        assert restored == _SelectExtraData("normal")

    def test_from_dict_missing_key_returns_none(self) -> None:
        assert _SelectExtraData.from_dict({}) is None


class TestSelectRestore:
    async def test_restores_option_by_index(self) -> None:
        device = _make_device()
        entity = _make_select(device)
        entity._stub_last_extra_data = _SelectExtraData("normal")

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_called_once_with(
            31, GimdowBLEDataPointType.DT_ENUM, 1  # "normal" is index 1
        )

    async def test_restores_option_via_value_mapping(self) -> None:
        device = _make_device()
        entity = _make_select(device, value_mapping={"function1": "normal"})
        entity._stub_last_extra_data = _SelectExtraData("normal")

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_called_once_with(
            31, GimdowBLEDataPointType.DT_ENUM, "function1"
        )

    async def test_no_restore_data_does_not_touch_datapoint_cache(self) -> None:
        """Fresh install / never-restored entity — no crash, no seed."""
        device = _make_device()
        entity = _make_select(device)
        entity._stub_last_extra_data = None

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_not_called()

    async def test_extra_data_present_but_option_none_does_not_seed(self) -> None:
        device = _make_device()
        entity = _make_select(device)
        entity._stub_last_extra_data = _SelectExtraData(None)

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_not_called()

    async def test_unknown_option_does_not_seed(self) -> None:
        """A restored option no longer in _attr_options (e.g. after a device
        firmware change) must not raise or seed a bogus datapoint."""
        device = _make_device()
        entity = _make_select(device)
        entity._stub_last_extra_data = _SelectExtraData("not-a-real-option")

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_not_called()

    async def test_non_lock_product_skips_restore(self) -> None:
        device = _make_device()
        entity = _make_select(device)
        entity._product = SimpleNamespace(
            name="Other", manufacturer="Gimdow", is_lock=False
        )
        entity._stub_last_extra_data = _SelectExtraData("normal")

        await entity.async_added_to_hass()

        device.datapoints.get_or_create.assert_not_called()
