"""A1 PRO local controls must never send unsupported DP33/36 writes."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.gimdow_ble.devices import GimdowBLEData
from custom_components.gimdow_ble.ha_controls import (
    GimdowBLEHAAutoLockDelay, GimdowBLEHAAutoLockSwitch, _HAAutoLockData,
    supports_ha_auto_lock,
)
from custom_components.gimdow_ble.sensor import GimdowBLESensor, get_mapping_by_device
from custom_components.gimdow_ble.gimdow_ble import GimdowBLEDataPointType
from tests.test_lock import _make_device
from tests.gimdow_ble.test_lock_manager import _make_manager


def make_data():
    device = _make_device()
    device.category = "ms"
    device.product_id = "uueg45qu"
    return GimdowBLEData(
        title="Side gate", device=device, coordinator=MagicMock(),
        product=SimpleNamespace(name="A1 PRO", manufacturer="Gimdow", is_lock=True),
        manager=MagicMock(), door_update_signal="door",
        virtual_auto_lock_signal="enabled", virtual_auto_lock_time_signal="delay",
    )


def entity(cls, data):
    result = cls(data)
    result.hass = MagicMock()
    return result


def no_writes(data):
    data.device.datapoints.get_or_create.assert_not_called()
    data.device._create_safe_task.assert_not_called()


async def test_switch_defaults_off_and_toggles_without_ble_writes():
    data = make_data()
    control = entity(GimdowBLEHAAutoLockSwitch, data)
    await control.async_added_to_hass()
    assert not control.is_on
    await control.async_turn_on()
    assert control.is_on
    assert control.extra_restore_state_data.as_dict() == {"enabled": True}
    await control.async_turn_off()
    assert not control.is_on
    no_writes(data)


async def test_controls_restore_and_ignore_unrelated_device_pushes():
    data = make_data()
    switch = entity(GimdowBLEHAAutoLockSwitch, data)
    delay = entity(GimdowBLEHAAutoLockDelay, data)
    switch._stub_last_extra_data = _HAAutoLockData(True)
    delay._stub_last_number_data = SimpleNamespace(native_value=75)
    await delay.async_added_to_hass()
    await switch.async_added_to_hass()
    switch._handle_coordinator_update()
    delay._handle_coordinator_update()
    assert switch.is_on
    assert delay.native_value == 75
    await delay.async_set_native_value(120)
    assert data.ha_auto_lock_delay == 120
    no_writes(data)


@pytest.mark.parametrize("bad", [0, -1, 1801, 1.5, float("nan"), float("inf")])
async def test_invalid_delay_does_not_change_setting(bad):
    data = make_data()
    delay = entity(GimdowBLEHAAutoLockDelay, data)
    with pytest.raises(ValueError):
        await delay.async_set_native_value(bad)
    assert delay.native_value == 30
    no_writes(data)


def test_no_ha_controls_for_pro_max_or_other_ms_product():
    assert not supports_ha_auto_lock(SimpleNamespace(category="jtmspro", product_id="rlyxv7pe"))
    assert not supports_ha_auto_lock(SimpleNamespace(category="ms", product_id="other"))


def test_local_delay_used_by_existing_door_aware_timer():
    manager, _, device = _make_manager(virtual_auto_lock=True)
    manager._config = replace(manager._config, get_auto_lock_delay=lambda: 75)
    manager._door_state_known = True
    device.get_lock_state.return_value = False
    with patch("custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later") as later:
        manager.start_auto_lock_timer()
    assert later.call_args.args[1] == 75


def test_auto_lock_does_not_start_without_door_reading():
    manager, _, _ = _make_manager(virtual_auto_lock=True)
    manager._config = replace(manager._config, get_auto_lock_delay=lambda: 75)
    manager._door_state_known = False
    with patch("custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later") as later:
        manager.start_auto_lock_timer()
    later.assert_not_called()


@pytest.mark.parametrize("value,expected", [(0, "low_battery"), (1, "power_off"), (2, None), (-1, None)])
def test_battery_alarm_decodes_report_without_clear_inference(value, expected):
    data = make_data()
    mapping = next(m for m in get_mapping_by_device(data.device) if m.dp_id == 21)
    mapping = replace(mapping, description=SimpleNamespace(
        key="last_battery_alarm", translation_key=None,
        options=["low_battery", "power_off"],
    ))
    sensor = GimdowBLESensor(data.coordinator, data.device, data.product, mapping)
    data.device.datapoints.__getitem__.return_value = SimpleNamespace(
        type=GimdowBLEDataPointType.DT_ENUM, value=value,
    )
    sensor._handle_coordinator_update()
    assert sensor._attr_native_value == expected


@pytest.mark.parametrize("state", ["unknown", "unavailable", "0"])
async def test_alarm_does_not_restore_invalid_enum_state(state):
    data = make_data()
    mapping = next(m for m in get_mapping_by_device(data.device) if m.dp_id == 21)
    mapping = replace(mapping, description=SimpleNamespace(
        key="last_battery_alarm", translation_key=None,
        options=["low_battery", "power_off"],
    ))
    sensor = GimdowBLESensor(data.coordinator, data.device, data.product, mapping)
    sensor._attr_native_value = None
    sensor._stub_last_state = SimpleNamespace(state=state)
    await sensor.async_added_to_hass()
    assert sensor._attr_native_value is None
