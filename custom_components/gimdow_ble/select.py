"""The Gimdow BLE integration."""
from __future__ import annotations

from dataclasses import dataclass, field

import logging

from homeassistant.components.select import (
    SelectEntityDescription,
    SelectEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.restore_state import RestoreEntity


from .const import DOMAIN
from .devices import GimdowBLEData, GimdowBLEEntity, GimdowBLEProductInfo
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)


@dataclass
class GimdowBLESelectMapping:
    dp_id: int
    description: SelectEntityDescription
    force_add: bool = True
    dp_type: GimdowBLEDataPointType | None = None
    value_mapping: dict[str, str] | None = None


@dataclass
class TemperatureUnitDescription(SelectEntityDescription):
    key: str = "temperature_unit"
    icon: str = "mdi:thermometer"
    entity_category: EntityCategory = EntityCategory.CONFIG





@dataclass
class GimdowBLECategorySelectMapping:
    products: dict[str, list[GimdowBLESelectMapping]] | None = None
    mapping: list[GimdowBLESelectMapping] | None = None


mapping: dict[str, GimdowBLECategorySelectMapping] = {
    "jtmspro": GimdowBLECategorySelectMapping(
        products={
            "rlyxv7pe":  # Smart Lock
            [
                GimdowBLESelectMapping(
                    dp_id=31,
                    description=SelectEntityDescription(
                        key="beep_volume",
                        options=[
                            "mute",
                            "low",
                            "normal",
                            "high",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        }
    ),
}


def get_mapping_by_device(
    device: GimdowBLEDevice
) -> list[GimdowBLECategorySelectMapping]:
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


class GimdowBLESelect(GimdowBLEEntity, SelectEntity, RestoreEntity):
    """Representation of a Gimdow BLE select."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLESelectMapping,
    ) -> None:
        super().__init__(
            hass,
            coordinator,
            device,
            product,
            mapping.description
        )
        self._mapping = mapping
        self._attr_options = mapping.description.options

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        if self._product.lock:
            if (last_state := await self.async_get_last_state()) is not None:
                if last_state.state != "unknown":
                    value = last_state.state
                    raw_value = None
                    
                    # Reverse lookup if value mapping exists
                    if self._mapping.value_mapping:
                        for k, v in self._mapping.value_mapping.items():
                            if v == value:
                                raw_value = k
                                break
                    # Else check if it's an option index
                    elif value in self._attr_options:
                        raw_value = self._attr_options.index(value)
                    
                    if raw_value is not None:
                         # Default to DT_ENUM if not specified, assumption based on select usage
                        dptype = self._mapping.dp_type or GimdowBLEDataPointType.DT_ENUM
                        self._device.datapoints.get_or_create(
                            self._mapping.dp_id,
                            dptype,
                            raw_value,
                        )

    @property
    def current_option(self) -> str | None:
        """Return the selected entity option to represent the entity state."""
        # Raw value
        value: str | None = None
        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            value = datapoint.value
            if self._mapping.value_mapping:
                return self._mapping.value_mapping.get(value)
            elif isinstance(value, int) and value >= 0 and value < len(self._attr_options):
                return self._attr_options[value]
            else:
                return value
        

        return None

    def select_option(self, value: str) -> None:
        """Change the selected option."""
        if value in self._attr_options:
            if self._mapping.value_mapping:
                key = next(
                    (k for k, v in self._mapping.value_mapping.items() if v == value),
                    None
                )
                if key:
                     # For string/enum mapped values, we send the key (e.g. "function1")
                    dptype = self._mapping.dp_type or GimdowBLEDataPointType.DT_STRING
                    datapoint = self._device.datapoints.get_or_create(
                        self._mapping.dp_id,
                        dptype,
                        key,
                    )
                    if datapoint:
                        self._hass.create_task(datapoint.set_value(key))
            else:
                int_value = self._attr_options.index(value)
                datapoint = self._device.datapoints.get_or_create(
                    self._mapping.dp_id,
                    GimdowBLEDataPointType.DT_ENUM,
                    int_value,
                )
                if datapoint:
                    self._hass.create_task(datapoint.set_value(int_value))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gimdow BLE sensors."""
    data: GimdowBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[GimdowBLESelect] = []
    for mapping in mappings:
        if (
            mapping.force_add or
            data.device.datapoints.has_id(mapping.dp_id, mapping.dp_type)
        ):
            entities.append(GimdowBLESelect(
                hass,
                data.coordinator,
                data.device,
                data.product,
                mapping,
            ))
    async_add_entities(entities)
