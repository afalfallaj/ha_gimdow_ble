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
import time
from dataclasses import dataclass

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .devices import GimdowBLEData, GimdowBLEEntity, GimdowBLEProductInfo
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice
from .gimdow_ble.diagnostics import GimdowBLEDiagContext
from .gimdow_ble.lock_manager import GimdowBLELockManager, LockBlockedReason

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


@dataclass
class GimdowBLECategoryLockMapping:
    """Mapping from product ID to list of lock entity configs."""

    products: dict[str, list[GimdowBLELockMapping]] | None = None
    mapping: list[GimdowBLELockMapping] | None = None


mapping: dict[str, GimdowBLECategoryLockMapping] = {
    "jtmspro": GimdowBLECategoryLockMapping(
        products={
            "rlyxv7pe": [  # Gimdow Smart Lock
                GimdowBLELockMapping(
                    lock_dp_id=46,
                    unlock_dp_id=6,
                    state_dp_id=47,
                    description=LockEntityDescription(key="lock", name=None),
                    unlock_value=True,
                    lock_value=True,
                ),
            ]
        }
    ),
}


def get_mapping_by_device(device: GimdowBLEDevice) -> list[GimdowBLELockMapping]:
    """Return the lock mappings for a specific device."""
    cat = mapping.get(device.category)
    if cat is not None:
        if cat.products:
            product_mapping = cat.products.get(device.product_id)
            if product_mapping:
                return product_mapping
        if cat.mapping:
            return cat.mapping
    return []


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
        super().__init__(hass, coordinator, device, product, lock_mapping.description)
        self._mapping = lock_mapping
        self._data = data
        self._last_is_locked: bool | None = None
        self._attr_changed_by: str | None = None

        self._lock_manager = GimdowBLELockManager(
            device=device,
            hass=hass,
            data=data,
            mapping=lock_mapping,
            on_state_change=self.async_write_ha_state,
        )

        _LOGGER.debug(
            "[%s] GimdowBLELock initialized. unknown_state_action=%s",
            self._device.address, data.unknown_state_action,
        )

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

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
            _LOGGER.debug("[%s] Initial door state: is_open=%s", self._device.address, self._data.is_door_open)
            self._lock_manager.on_door_changed(self._data.is_door_open)
        else:
            _LOGGER.debug("[%s] Initial door state: unknown (assuming closed)", self._device.address)

    # ------------------------------------------------------------------
    # Dispatcher callbacks — thin wrappers, delegate to lock manager
    # ------------------------------------------------------------------

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
        self._lock_manager.on_coordinator_update(self.is_locked)
        changed_by = self._lock_manager.update_attribution(self.is_locked, self._last_is_locked)
        if changed_by is not None:
            self._attr_changed_by = changed_by
        self._last_is_locked = self.is_locked
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

    # ------------------------------------------------------------------
    # Diagnostic snapshot (HA-layer context)
    # ------------------------------------------------------------------

    def _diag_snapshot(self, action: str, error: str | None = None) -> GimdowBLEDiagContext:
        def _dp_val(dp_id: int):
            dp = self._device.datapoints[dp_id]
            return dp.value if dp else None

        return GimdowBLEDiagContext(
            timestamp=time.time(),
            address=self._device.address,
            is_connected=self._coordinator.connected,
            is_paired=self._device.is_paired,
            is_resolving=self._device.is_resolving,
            dp_state={
                "dp47_lock_state": _dp_val(47),
                "dp46_lock_cmd":    _dp_val(46),
                "dp6_unlock_cmd":   _dp_val(6),
                "dp36_auto_lock_delay": _dp_val(36),
            },
            action=action,
            error=error,
            extra={
                "is_door_open":           self._lock_manager.is_door_open,
                "pending_reason":         self._lock_manager.pending.reason.value,
                "is_locking":             self._lock_manager.is_locking,
                "is_unlocking":           self._lock_manager.is_unlocking,
                "auto_lock_enabled":      self._data.virtual_auto_lock,
                "auto_lock_timer_active": self._lock_manager._auto_lock_timer is not None,
                "pending_action_source":  self._lock_manager.pending_action_source,
                "is_locked":              self.is_locked,
            },
        )


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gimdow BLE lock entities from a config entry."""
    data: GimdowBLEData = hass.data[DOMAIN][entry.entry_id]
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
