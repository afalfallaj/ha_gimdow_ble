"""The Gimdow BLE integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        # Gimdow specific polling
        if self._product.lock:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass, self._async_poll_device, timedelta(seconds=60)
                )
            )

    async def _async_poll_device(self, now=None):
        """Send status query to keep connection alive/wake device."""
        # Check if stopping is not needed as async_track_time_interval handles cleanup via async_on_remove
        await self._device.update()

    @property
    def is_locked(self) -> bool | None:
        """Return true if lock is locked."""
        datapoint = self._device.datapoints[self._mapping.state_dp_id]
        if datapoint:
            return not bool(datapoint.value)
        return None

    async def async_lock(self, **kwargs) -> None:
        """Lock the device."""
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
                )
            )
    async_add_entities(entities)
