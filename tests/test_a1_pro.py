"""A1 PRO must use its own product mapping and wire types."""

from types import SimpleNamespace

from custom_components.gimdow_ble.devices import get_device_product_info
from custom_components.gimdow_ble.lock import get_mapping_by_device as locks
from custom_components.gimdow_ble.sensor import get_mapping_by_device as sensors
from custom_components.gimdow_ble.select import get_mapping_by_device as selects
from custom_components.gimdow_ble.button import get_mapping_by_device as buttons
from custom_components.gimdow_ble.number import get_mapping_by_device as numbers
from custom_components.gimdow_ble.switch import get_mapping_by_device as switches


def test_side_gate_maps_as_a1_pro():
    device = SimpleNamespace(category="ms", product_id="uueg45qu")
    product = get_device_product_info(device)
    assert product.name == "A1 PRO"
    assert product.is_lock
    lock, = locks(device)
    assert (lock.lock_dp_id, lock.unlock_dp_id, lock.state_dp_id) == (46, 6, 47)
    assert lock.unlock_value == b"\x01\x01"
    assert lock.lock_value is True
    assert [m.dp_id for m in sensors(device)] == [9, 21]
    assert [m.dp_id for m in selects(device)] == [30, 48]
    # Do not advertise the PRO MAX's unconfirmed configuration controls.
    assert buttons(device) == []
    assert numbers(device) == []
    assert switches(device) == []


def test_pro_max_keeps_boolean_control():
    lock, = locks(SimpleNamespace(category="jtmspro", product_id="rlyxv7pe"))
    assert lock.unlock_value is True


def test_unknown_ms_product_does_not_inherit_side_gate_mapping():
    device = SimpleNamespace(category="ms", product_id="unknown")
    assert get_device_product_info(device) is None
    assert locks(device) == []
    assert sensors(device) == []
    assert selects(device) == []
