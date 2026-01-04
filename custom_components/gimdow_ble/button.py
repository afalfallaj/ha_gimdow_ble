"""The Tuya BLE integration."""
from __future__ import annotations

from dataclasses import dataclass, field

import logging
from typing import Any, Callable

from homeassistant.components.button import (
    ButtonEntityDescription,
    ButtonEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .devices import TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .gimdow_ble import TuyaBLEDataPointType, TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)


TuyaBLEButtonIsAvailable = Callable[["TuyaBLEButton", TuyaBLEProductInfo], bool] | None


@dataclass
class TuyaBLEButtonMapping:
    dp_id: int
    description: ButtonEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    is_available: TuyaBLEButtonIsAvailable = None
    value: Any | None = None


@dataclass
class TuyaBLECategoryButtonMapping:
    products: dict[str, list[TuyaBLEButtonMapping]] | None = None
    mapping: list[TuyaBLEButtonMapping] | None = None


mapping: dict[str, TuyaBLECategoryButtonMapping] = {
    "jtmspro": TuyaBLECategoryButtonMapping(
        products={
            "rlyxv7pe":  # Gimdow
            [
                TuyaBLEButtonMapping(
                    dp_id=44,
                    description=ButtonEntityDescription(
                        key="sync_clock",
                        icon="mdi:clock",
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
                TuyaBLEButtonMapping(
                    dp_id=68,
                    description=ButtonEntityDescription(
                        key="recalibrate",
                        icon="mdi:wrench",
                        entity_category=EntityCategory.CONFIG,
                    ),
                    value=0,
                    dp_type=TuyaBLEDataPointType.DT_ENUM,
                ),
                TuyaBLEButtonMapping(
                    dp_id=68,
                    description=ButtonEntityDescription(
                        key="unlock_more",
                        icon="mdi:lock-open-plus",
                        entity_category=EntityCategory.CONFIG,
                    ),
                    value=1,
                    dp_type=TuyaBLEDataPointType.DT_ENUM,
                ),
                TuyaBLEButtonMapping(
                    dp_id=68,
                    description=ButtonEntityDescription(
                        key="keep_retracted",
                        icon="mdi:lock-open-minus",
                        entity_category=EntityCategory.CONFIG,
                    ),
                    value=2,
                    dp_type=TuyaBLEDataPointType.DT_ENUM,
                ),
                TuyaBLEButtonMapping(
                    dp_id=68,
                    description=ButtonEntityDescription(
                        key="add_force",
                        icon="mdi:arm-flex",
                        entity_category=EntityCategory.CONFIG,
                    ),
                    value=3,
                    dp_type=TuyaBLEDataPointType.DT_ENUM,
                ),
            ],

        },
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLECategoryButtonMapping]:
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


class TuyaBLEButton(TuyaBLEEntity, ButtonEntity):
    """Representation of a Tuya BLE Button."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        mapping: TuyaBLEButtonMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping

    def press(self) -> None:
        """Press the button."""
        dptype = self._mapping.dp_type or TuyaBLEDataPointType.DT_BOOL
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            dptype,
            False if dptype == TuyaBLEDataPointType.DT_BOOL else 0,
        )
        if datapoint:
            if self._mapping.value is not None:
                self._hass.create_task(datapoint.set_value(self._mapping.value))
            elif self._product.lock:
                #Gimdow need true to activate lock/unlock commands
                self._hass.create_task(datapoint.set_value(True))
            else:
                self._hass.create_task(datapoint.set_value(not bool(datapoint.value)))

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
    """Set up the Tuya BLE sensors."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLEButton] = []
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.dp_id, mapping.dp_type
        ):
            entities.append(
                TuyaBLEButton(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )
    async_add_entities(entities)
