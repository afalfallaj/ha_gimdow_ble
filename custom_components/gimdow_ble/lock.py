"""The Gimdow BLE integration."""
from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import STATE_ON, STATE_OFF

from .const import DOMAIN, CONF_DOOR_SENSOR
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
        door_sensor: str | None = None,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._door_sensor = door_sensor
        self._is_door_open = False

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        if self._door_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._door_sensor], self._async_door_sensor_changed
                )
            )
            # Initialize state
            state = self.hass.states.get(self._door_sensor)
            if state:
                self._is_door_open = state.state == STATE_ON

    @callback
    def _async_door_sensor_changed(self, event) -> None:
        """Handle door sensor state changes."""
        new_state = event.data.get("new_state")
        if new_state:
            self._is_door_open = new_state.state == STATE_ON
            self.async_write_ha_state()

    @property
    def is_jammed(self) -> bool | None:
        """Return true if lock is jammed (locked while open)."""
        if self._is_door_open and self.is_locked:
            return True
        return None


    @property
    def is_locked(self) -> bool | None:
        """Return true if lock is locked."""
        datapoint = self._device.datapoints[self._mapping.state_dp_id]
        if datapoint:
            return not bool(datapoint.value)
        return None

    async def async_lock(self, **kwargs) -> None:
        """Lock the device."""
        if self._is_door_open:
            raise HomeAssistantError("Cannot lock while door is open")

        datapoint = self._device.datapoints.get_or_create(
            self._mapping.lock_dp_id,
            GimdowBLEDataPointType.DT_BOOL,
            self._mapping.lock_value,
        )
        if datapoint:
            await datapoint.set_value(self._mapping.lock_value)

    async def async_unlock(self, **kwargs) -> None:
        """Unlock the device."""
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.unlock_dp_id,
            GimdowBLEDataPointType.DT_BOOL,
            self._mapping.unlock_value,
        )
        if datapoint:
            await datapoint.set_value(self._mapping.unlock_value)


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
                    door_sensor=entry.options.get(CONF_DOOR_SENSOR) or entry.data.get(CONF_DOOR_SENSOR),
                )
            )
    async_add_entities(entities)
