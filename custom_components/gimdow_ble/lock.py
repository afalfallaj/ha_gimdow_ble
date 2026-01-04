"""The Tuya BLE integration."""
from __future__ import annotations

from dataclasses import dataclass

import logging
from threading import Timer

from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .devices import TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .gimdow_ble import TuyaBLEDataPointType, TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)


@dataclass
class TuyaBLELockMapping:
    lock_dp_id: int
    unlock_dp_id: int
    state_dp_id: int
    description: LockEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    unlock_value: int | bool = True
    lock_value: int | bool = True


@dataclass
class TuyaBLECategoryLockMapping:
    products: dict[str, list[TuyaBLELockMapping]] | None = None
    mapping: list[TuyaBLELockMapping] | None = None


mapping: dict[str, TuyaBLECategoryLockMapping] = {
    "jtmspro": TuyaBLECategoryLockMapping(
        products={
            "rlyxv7pe": [  # Gimdow Smart Lock
                TuyaBLELockMapping(
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


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLELockMapping]:
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


class TuyaBLELock(TuyaBLEEntity, LockEntity):
    """Representation of a Tuya BLE Lock."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        mapping: TuyaBLELockMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping
        
        # Gimdow specific polling
        if self._device.product_id == "rlyxv7pe":
            self._poll_thread = Timer(60, self._poll_device)
            self._poll_thread.start()

    def _poll_device(self):
        """Send status query to keep connection alive/wake device."""
        if self.hass.is_stopping:
            return
            
        self.hass.create_task(self._device.update())
        # Reschedule
        self._poll_thread = Timer(60, self._poll_device)
        self._poll_thread.start()

    @property
    def is_locked(self) -> bool | None:
        """Return true if lock is locked."""
        datapoint = self._device.datapoints[self._mapping.state_dp_id]
        if datapoint:
            return not bool(datapoint.value)
        return None

    def lock(self, **kwargs) -> None:
        """Lock the device."""
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.lock_dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            self._mapping.lock_value,
        )
        if datapoint:
            self._hass.create_task(datapoint.set_value(self._mapping.lock_value))

    def unlock(self, **kwargs) -> None:
        """Unlock the device."""
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.unlock_dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            self._mapping.unlock_value,
        )
        if datapoint:
            self._hass.create_task(datapoint.set_value(self._mapping.unlock_value))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE locks."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLELock] = []
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.state_dp_id, mapping.dp_type
        ):
            entities.append(
                TuyaBLELock(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )
    async_add_entities(entities)
