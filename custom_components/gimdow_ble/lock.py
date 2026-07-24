"""Gimdow BLE lock platform — thin HA entity layer.

All lock business logic lives in :mod:`gimdow_ble.lock_manager`.
This file contains only:

  - ``GimdowBLELockMapping`` — HA config data (datapoint IDs)
  - ``GimdowBLECategoryLockMapping`` / ``mapping`` dict — platform setup config
  - ``get_mapping_by_device()`` — setup helper
  - ``GimdowBLELock`` — HA LockEntity delegate (~70 lines)
  - ``async_setup_entry`` — HA platform setup
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import GimdowBLEConfigEntry
from .devices import (
    GimdowBLECategoryMapping,
    GimdowBLEData,
    GimdowBLEEntity,
    GimdowBLEProductInfo,
    get_platform_mapping,
)
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice
from .gimdow_ble.lock_manager import (
    GimdowBLELockManager,
    LockBlockedReason,
    LockManagerConfig,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping config (HA data — not business logic)
# ---------------------------------------------------------------------------


@dataclass
class GimdowBLELockMapping:
    """Datapoint IDs and description for a single lock entity."""

    lock_dp_id: int
    unlock_dp_id: int
    state_dp_id: int
    description: LockEntityDescription
    force_add: bool = True
    dp_type: GimdowBLEDataPointType | None = None
    unlock_value: int | bool = True
    lock_value: int | bool = True


GimdowBLECategoryLockMapping = GimdowBLECategoryMapping[GimdowBLELockMapping]

mapping: dict[str, GimdowBLECategoryLockMapping] = {
    "jtmspro": GimdowBLECategoryLockMapping(
        products={
            "rlyxv7pe": [  # Gimdow Smart Lock
                GimdowBLELockMapping(
                    lock_dp_id=46,
                    unlock_dp_id=46,
                    state_dp_id=47,
                    description=LockEntityDescription(key="lock", name=None),
                    unlock_value=False,
                    lock_value=True,
                ),
            ]
        }
    ),
}


def get_mapping_by_device(device: GimdowBLEDevice) -> list[GimdowBLELockMapping]:
    """Return the lock mappings for a specific device."""
    return get_platform_mapping(mapping, device)


# ---------------------------------------------------------------------------
# Lock Entity
# ---------------------------------------------------------------------------


class GimdowBLELock(GimdowBLEEntity, LockEntity):
    """Representation of a Gimdow BLE Lock.

    This class is a thin HA delegate. All state machine logic,
    auto-lock timers, and pending intent management are handled by
    :class:`~gimdow_ble.lock_manager.GimdowBLELockManager`.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        lock_mapping: GimdowBLELockMapping,
        data: GimdowBLEData,
    ) -> None:
        super().__init__(coordinator, device, product, lock_mapping.description)
        self._mapping = lock_mapping
        self._data = data
        self._last_is_locked: bool | None = None
        self._attr_changed_by: str | None = None
        self._was_connected: bool = False

        self._lock_manager = GimdowBLELockManager(
            device=device,
            hass=hass,
            config=LockManagerConfig(
                unknown_state_action=data.unknown_state_action,
                transition_timeout=data.transition_timeout,
                auto_lock_delay_fallback=data.auto_lock_delay_fallback,
                lock_dp_id=lock_mapping.lock_dp_id,
                unlock_dp_id=lock_mapping.unlock_dp_id,
                state_dp_id=lock_mapping.state_dp_id,
                lock_value=lock_mapping.lock_value,
                unlock_value=lock_mapping.unlock_value,
                get_auto_lock=lambda: data.virtual_auto_lock,
                has_door_sensor=data.has_door_sensor,
            ),
            on_state_change=self.async_write_ha_state,
        )

        _LOGGER.debug(
            "[%s] GimdowBLELock initialized. unknown_state_action=%s",
            self._device.address,
            data.unknown_state_action,
        )

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Deliberately no HA RestoreEntity-based state restore here. The
        # entity's last displayed "locked"/"unlocked" can go stale across any
        # HA-down window (the lock can be operated manually while HA is off),
        # and DP47 is not re-pushed by the device on reconnect (see AGENTS.md)
        # — so a restored value can't be corroborated by a fresh device read
        # either. Reasserting it via a double-command would risk overwriting
        # a legitimate manual operation that happened while HA was down.
        # _last_known_state is seeded exclusively from live DP47 pushes via
        # on_coordinator_update(); on a full restart it starts unknown and
        # confirm_last/force_lock_twice stay unknown until the device reports
        # a real reading — that unknown-state resolution is what
        # GimdowBLELockManager.on_connected() (and _handle_unknown_state) own.

        self.async_on_remove(
            self._device.register_connected_callback(self._on_ble_reconnected)
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._data.door_update_signal,
                self._async_door_sensor_changed,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._data.virtual_auto_lock_signal,
                self._async_virtual_auto_lock_changed,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._data.virtual_auto_lock_time_signal,
                self._async_virtual_auto_lock_time_changed,
            )
        )

        if self._data.is_door_open is not None:
            _LOGGER.debug(
                "[%s] Initial door state: is_open=%s",
                self._device.address,
                self._data.is_door_open,
            )
            self._lock_manager.on_door_changed(self._data.is_door_open)
        else:
            _LOGGER.debug(
                "[%s] Initial door state: unknown (assuming closed)",
                self._device.address,
            )

    async def async_will_remove_from_hass(self) -> None:
        self._lock_manager.cleanup()

    # ------------------------------------------------------------------
    # BLE + Dispatcher callbacks — thin wrappers, delegate to lock manager
    # ------------------------------------------------------------------

    @callback
    def _on_ble_reconnected(self) -> None:
        """Fire on every BLE-level handshake, bypassing the coordinator grace window.

        The coordinator only marks the device disconnected after 10 minutes, so
        _handle_coordinator_update's False→True flip never fires for brief dropouts.
        This callback fires on *every* successful BLE connection handshake.
        """
        self._lock_manager.on_connected()
        self.async_write_ha_state()

    @callback
    def _async_door_sensor_changed(self, is_open: bool) -> None:
        self._lock_manager.on_door_changed(is_open)
        self.async_write_ha_state()

    @callback
    def _async_virtual_auto_lock_changed(self) -> None:
        self._lock_manager.on_auto_lock_setting_changed()

    @callback
    def _async_virtual_auto_lock_time_changed(self) -> None:
        self._lock_manager.on_auto_lock_time_changed()

    # ------------------------------------------------------------------
    # Coordinator update
    # ------------------------------------------------------------------

    @callback
    def _handle_coordinator_update(self) -> None:
        is_connected = self._coordinator.connected
        if is_connected and not self._was_connected:
            self._lock_manager.on_connected()
        self._was_connected = is_connected

        locked = self.is_locked
        self._lock_manager.on_coordinator_update(locked)
        did_change, changed_by = self._lock_manager.update_attribution(
            locked, self._last_is_locked
        )
        if did_change:
            self._attr_changed_by = changed_by
        self._last_is_locked = locked
        super()._handle_coordinator_update()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_locked(self) -> bool | None:
        if self._lock_manager.is_timeout_unknown:
            return None
        return self._device.get_lock_state(self._mapping.state_dp_id)

    @property
    def is_locking(self) -> bool:
        return self._lock_manager.is_locking

    @property
    def is_unlocking(self) -> bool:
        return self._lock_manager.is_unlocking

    @property
    def is_jammed(self) -> bool | None:
        """Two distinct jam conditions:
        A) Physical jam — locked but door sensor reports open.
        B) Software pending — HA requested lock while door open; waiting.
        """
        if self.is_unlocking:
            return False
        if self.is_locked and self._lock_manager.is_door_open:
            return True  # A: real hardware jam
        if self._lock_manager.pending.reason == LockBlockedReason.DOOR_OPEN_PENDING:
            return True  # B: software pending
        return False

    @property
    def extra_state_attributes(self) -> dict:
        """Expose lock_blocked_reason for advanced automations."""
        reason = self._lock_manager.pending.reason.value
        if self.is_locked and self._lock_manager.is_door_open:
            reason = LockBlockedReason.DOOR_OPEN_LOCKED.value
        return {"lock_blocked_reason": reason}

    # ------------------------------------------------------------------
    # Commands — delegate entirely to lock manager
    # ------------------------------------------------------------------

    async def async_lock(self, **kwargs) -> None:
        await self._lock_manager.lock()

    async def async_unlock(self, **kwargs) -> None:
        await self._lock_manager.unlock()


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GimdowBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gimdow BLE lock entities from a config entry."""
    data = entry.runtime_data
    mappings = get_mapping_by_device(data.device)
    entities: list[GimdowBLELock] = []
    for m in mappings:
        if m.force_add or data.device.datapoints.has_id(m.state_dp_id, m.dp_type):
            entities.append(
                GimdowBLELock(
                    hass=hass,
                    coordinator=data.coordinator,
                    device=data.device,
                    product=data.product,
                    lock_mapping=m,
                    data=data,
                )
            )
    async_add_entities(entities)
