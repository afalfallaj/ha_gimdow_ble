"""The Gimdow BLE integration."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import asyncio

from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval, async_call_later
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import STATE_ON, STATE_OFF

from .const import DOMAIN
from .devices import GimdowBLEData, GimdowBLEEntity, GimdowBLEProductInfo
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)


@dataclass
class GimdowBLELockMapping:
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
                    description=LockEntityDescription(
                        key="lock",
                        name=None,
                    ),
                    unlock_value=True,
                    lock_value=True,
                ),
            ]
        }
    ),
}


def get_mapping_by_device(device: GimdowBLEDevice) -> list[GimdowBLELockMapping]:
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        else:
            return []
    else:
        return []


class GimdowBLELock(GimdowBLEEntity, LockEntity):
    """Representation of a Gimdow BLE Lock."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLELockMapping,
        data: GimdowBLEData,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._data = data
        self._is_door_open = False
        self._pending_lock = False
        self._auto_lock_timer = None
        self._unlock_wait_future = None
        self._is_unlocking = False
        _LOGGER.debug(f"GimdowBLELock initialized with data: {self._data}")

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        
        # Connect to dispatcher
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._data.door_update_signal,
                self._async_door_sensor_changed
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._data.virtual_auto_lock_signal,
                self._async_virtual_auto_lock_changed
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._data.virtual_auto_lock_time_signal,
                self._async_virtual_auto_lock_time_changed
            )
        )

        # Initialize state
        if self._data.is_door_open is not None:
             self._is_door_open = self._data.is_door_open
             _LOGGER.debug(f"Initial door state from data: {self._is_door_open}")
             self._start_auto_lock_timer()
        else:
             _LOGGER.debug(f"Initial door state: Unknown (assuming closed)")

    @callback
    def _async_door_sensor_changed(self, is_open: bool) -> None:
        """Handle door sensor state changes."""
        self._is_door_open = is_open
        _LOGGER.debug(f"Door state changed to: is_open={self._is_door_open}")
        
        if not self._is_door_open and self._pending_lock:
                _LOGGER.debug("Door closed and pending lock is set. Executing lock.")
                self.hass.async_create_task(self.async_lock())
        
        self._start_auto_lock_timer()
        
        self.async_write_ha_state()

    @callback
    def _async_virtual_auto_lock_changed(self) -> None:
        """Handle virtual auto lock state changes."""
        _LOGGER.debug(f"Virtual auto lock setting changed to: {self._data.virtual_auto_lock}")
        self._start_auto_lock_timer()

    @callback
    def _async_virtual_auto_lock_time_changed(self) -> None:
        """Handle virtual auto lock time changes."""
        _LOGGER.debug(f"Virtual auto lock time setting changed.")
        self._start_auto_lock_timer()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self._unlock_wait_future and not self._unlock_wait_future.done():
             if self.is_locked is False:
                  self._unlock_wait_future.set_result(True)
        
        if self.is_locked is False:
             self._is_unlocking = False
        
        if self._data.virtual_auto_lock and not self.is_locked and not self._is_door_open and self._auto_lock_timer is None:
             _LOGGER.debug("Auto lock: Detected unlocked state with no active timer. Starting timer.")
             self._start_auto_lock_timer()

        super()._handle_coordinator_update()

    @property
    def is_jammed(self) -> bool | None:
        """Return true if lock is jammed (locked while open)."""
        if self.is_unlocking:
             return False

        if (self._is_door_open and self.is_locked) or self._pending_lock:
            return True
        return None

    @property
    def is_unlocking(self) -> bool:
        """Return true if the lock is currently unlocking."""
        return self._is_unlocking


    @property
    def is_locked(self) -> bool | None:
        """Return true if lock is locked."""
        datapoint = self._device.datapoints[self._mapping.state_dp_id]
        if datapoint:
            return not bool(datapoint.value)
        return None

    async def async_lock(self, **kwargs) -> None:
        """Lock the device."""
        self._stop_auto_lock_timer()
        _LOGGER.debug(f"Attempting to lock. is_door_open={self._is_door_open}")

        if self.is_locked is None:
            _LOGGER.warning("Lock state is Unknown. Trying to UNLOCK first to ensure mechanical state.")
            self._unlock_wait_future = self.hass.loop.create_future()
            await self.async_unlock()
            try:
                await asyncio.wait_for(self._unlock_wait_future, timeout=10)
                _LOGGER.debug("Device confirmed UNLOCKED. Proceeding to LOCK.")
            except asyncio.TimeoutError:
                _LOGGER.error("Timed out waiting for device to report UNLOCKED state. Aborting lock to prevent jamming.")
                self._unlock_wait_future = None
                return
            finally:
                self._unlock_wait_future = None

        if self._is_door_open:
            _LOGGER.warning("Door is open. Setting pending lock (Jammed state) and waiting for door close.")
            self._pending_lock = True
            self.async_write_ha_state()
            return
        
        self._pending_lock = False # Clear pending if we are proceeding

        datapoint = self._device.datapoints.get_or_create(
            self._mapping.lock_dp_id,
            GimdowBLEDataPointType.DT_BOOL,
            self._mapping.lock_value,
        )
        if datapoint:
            await datapoint.set_value(self._mapping.lock_value)

    async def async_unlock(self, **kwargs) -> None:
        """Unlock the device."""
        if self._pending_lock:
             _LOGGER.debug("Unlock requested. Clearing pending lock.")
             self._pending_lock = False
             self.async_write_ha_state()
             return

        datapoint = self._device.datapoints.get_or_create(
            self._mapping.unlock_dp_id,
            GimdowBLEDataPointType.DT_BOOL,
            self._mapping.unlock_value,
        )
        if datapoint:
            self._is_unlocking = True
            await datapoint.set_value(self._mapping.unlock_value)
            self._start_auto_lock_timer()

    def _start_auto_lock_timer(self) -> None:
        """Start the auto lock timer if conditions are met."""
        self._stop_auto_lock_timer()
        
        _LOGGER.debug(f"Auto lock: Checking conditions. virtual_auto_lock={self._data.virtual_auto_lock}, is_door_open={self._is_door_open}, is_locked={self.is_locked}")
        
        # Check virtual_auto_lock state (set by switch.py)
        if not self._data.virtual_auto_lock:
            _LOGGER.debug("Auto lock: Virtual auto lock is disabled.")
            return

        if self._is_door_open:
            _LOGGER.debug("Auto lock: Door is open, timer not started.")
            return

        if self._is_door_open:
            _LOGGER.debug("Auto lock: Door is open, timer not started.")
            return

        # Get delay from device DP 36 (Auto Lock Time), default to 10s
        auto_lock_delay = 10
        delay_dp = self._device.datapoints[36]
        if delay_dp and delay_dp.value:
            auto_lock_delay = int(delay_dp.value)

        _LOGGER.debug(f"Auto lock: Starting timer for {auto_lock_delay} seconds.")
        self._auto_lock_timer = async_call_later(
            self.hass, auto_lock_delay, self._async_auto_lock_callback
        )

    def _stop_auto_lock_timer(self) -> None:
        """Stop the auto lock timer."""
        if self._auto_lock_timer:
            self._auto_lock_timer()
            self._auto_lock_timer = None
            _LOGGER.debug("Auto lock: Timer stopped.")

    async def _async_auto_lock_callback(self, now) -> None:
        """Handle auto lock timer expiration."""
        self._auto_lock_timer = None
        
        if self.is_locked:
             _LOGGER.debug("Auto lock: Timer expired, but lock is already locked. skipping.")
             return
             
        _LOGGER.debug("Auto lock: Timer expired. Locking.")
        await self.async_lock()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gimdow BLE locks."""
    data: GimdowBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[GimdowBLELock] = []
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.state_dp_id, mapping.dp_type
        ):
            entities.append(
                GimdowBLELock(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                    data=data,
                )
            )
    async_add_entities(entities)
