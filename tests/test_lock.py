"""Tests for lock.py — GimdowBLELock construction and async_added_to_hass.

Regression coverage: async_added_to_hass must NOT restore last-known-state
from HA's RestoreEntity (deliberately removed — see the comment in
lock.py::async_added_to_hass). The lock manager's own
on_connected()/_handle_unknown_state() logic is the sole authority for
resolving lock state after a restart: restoring a possibly-stale
"locked"/"unlocked" from before an HA-down window (during which the lock
could have been operated manually) risked reasserting the wrong position.

This file also exists because lock.py had zero test coverage before this
change (homeassistant.components.lock was a bare MagicMock in conftest.py,
so `class GimdowBLELock(..., LockEntity)` couldn't even be defined) — so a
green full-suite run alone would not have caught an MRO/import break here.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.gimdow_ble.devices import GimdowBLEData
from custom_components.gimdow_ble.lock import GimdowBLELock, GimdowBLELockMapping


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
    dev.register_connected_callback.return_value = MagicMock()
    return dev


def _make_lock(device: MagicMock) -> tuple[GimdowBLELock, GimdowBLEData]:
    mapping = GimdowBLELockMapping(
        lock_dp_id=46,
        unlock_dp_id=6,
        state_dp_id=47,
        unlock_value=True,
        lock_value=True,
        description=SimpleNamespace(key="lock", translation_key=None),
    )
    product = SimpleNamespace(name="A1 PRO MAX", manufacturer="Gimdow", is_lock=True)
    data = GimdowBLEData(
        title="Gimdow Lock",
        device=device,
        product=product,
        manager=MagicMock(),
        coordinator=MagicMock(),
        door_update_signal="gimdow_door_update_test",
        virtual_auto_lock_signal="gimdow_virtual_auto_lock_test",
        virtual_auto_lock_time_signal="gimdow_virtual_auto_lock_time_test",
    )
    hass = MagicMock()
    entity = GimdowBLELock(
        hass=hass,
        coordinator=MagicMock(),
        device=device,
        product=product,
        lock_mapping=mapping,
        data=data,
    )
    entity.hass = hass
    return entity, data


class TestLockConstruction:
    def test_constructs_without_error(self) -> None:
        device = _make_device()
        entity, _ = _make_lock(device)
        assert entity is not None

    def test_no_restore_entity_apis_referenced(self) -> None:
        device = _make_device()
        entity, _ = _make_lock(device)
        assert not hasattr(entity, "async_get_last_state")
        assert not hasattr(entity._lock_manager, "restore_last_known_state")


class TestLockAddedToHass:
    async def test_runs_without_restoring_last_known_state(self) -> None:
        device = _make_device()
        entity, _ = _make_lock(device)

        await entity.async_added_to_hass()

        assert entity._lock_manager._last_known_state is None

    async def test_door_state_known_at_add_time_still_evaluated(self) -> None:
        """is_door_open pre-populated (e.g. door sensor restored first) must
        still reach on_door_changed — unrelated to the removed restore path,
        and must keep working."""
        device = _make_device()
        entity, data = _make_lock(device)
        data.is_door_open = False

        await entity.async_added_to_hass()

        assert entity._lock_manager._door_state_known is True
