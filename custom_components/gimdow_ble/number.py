"""The Gimdow BLE integration."""
from __future__ import annotations

from dataclasses import dataclass, field

import logging
from typing import Any, Callable

from homeassistant.components.number import (
    NumberEntityDescription,
    NumberEntity,
)
from homeassistant.components.number.const import NumberDeviceClass, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .devices import GimdowBLEData, GimdowBLEEntity, GimdowBLEProductInfo
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)

GimdowBLENumberGetter = (
    Callable[["GimdowBLENumber", GimdowBLEProductInfo], float | None] | None
)


GimdowBLENumberIsAvailable = (
    Callable[["GimdowBLENumber", GimdowBLEProductInfo], bool] | None
)


GimdowBLENumberSetter = (
    Callable[["GimdowBLENumber", GimdowBLEProductInfo, float], None] | None
)


@dataclass
class GimdowBLENumberMapping:
    dp_id: int
    description: NumberEntityDescription
    force_add: bool = True
    dp_type: GimdowBLEDataPointType | None = None
    coefficient: float = 1.0
    is_available: GimdowBLENumberIsAvailable = None
    getter: GimdowBLENumberGetter = None
    setter: GimdowBLENumberSetter = None
    mode: NumberMode = NumberMode.BOX





@dataclass
class GimdowBLECategoryNumberMapping:
    products: dict[str, list[GimdowBLENumberMapping]] | None = None
    mapping: list[GimdowBLENumberMapping] | None = None


mapping: dict[str, GimdowBLECategoryNumberMapping] = {
    "jtmspro": GimdowBLECategoryNumberMapping(
        products={
            "rlyxv7pe": [  # Gimdow
                GimdowBLENumberMapping(
                    dp_id=36,
                    description=NumberEntityDescription(
                        key="auto_lock_time",
                        icon="mdi:lock-clock",
                        native_max_value=1800,
                        native_min_value=1,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        native_step=1,
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
}


def get_mapping_by_device(device: GimdowBLEDevice) -> list[GimdowBLECategoryNumberMapping]:
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


class GimdowBLENumber(GimdowBLEEntity, NumberEntity, RestoreEntity):
    """Representation of a Gimdow BLE Number."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLENumberMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._attr_mode = mapping.mode

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        if self._product.lock:
            if (last_state := await self.async_get_last_state()) is not None:
                if last_state.state != "unknown":
                    try:
                        value = float(last_state.state)
                        # Calculate raw value based on coefficient to store in device cache
                        # The property logic divides by coefficient, so we multiply here.
                        raw_value = int(value * self._mapping.coefficient)
                        
                        self._device.datapoints.get_or_create(
                            self._mapping.dp_id,
                            GimdowBLEDataPointType.DT_VALUE,
                            raw_value,
                        )
                    except ValueError:
                        pass

    @property
    def native_value(self) -> float | None:
        """Return the entity value to represent the entity state."""
        if self._mapping.getter:
            return self._mapping.getter(self, self._product)

        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            return datapoint.value / self._mapping.coefficient

        return self._mapping.description.native_min_value

    def set_native_value(self, value: float) -> None:
        """Set new value."""
        if self._mapping.setter:
            self._mapping.setter(self, self._product, value)
            return
        int_value = int(value * self._mapping.coefficient)
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            GimdowBLEDataPointType.DT_VALUE,
            int(int_value),
        )
        if datapoint:
            self._hass.create_task(datapoint.set_value(int_value))

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        result = super().available
        if result and self._mapping.is_available:
            result = self._mapping.is_available(self, self._product)
        return result


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gimdow BLE sensors."""
    data: GimdowBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[GimdowBLENumber] = []
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.dp_id, mapping.dp_type
        ):
            entities.append(
                GimdowBLENumber(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )
    async_add_entities(entities)
