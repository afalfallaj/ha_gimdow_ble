"""The Gimdow BLE integration."""

from __future__ import annotations

from dataclasses import dataclass

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
from .devices import (
    GimdowBLECategoryMapping,
    GimdowBLEData,
    GimdowBLEEntity,
    GimdowBLEProductInfo,
    get_platform_mapping,
)
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)


GimdowBLEButtonIsAvailable = (
    Callable[["GimdowBLEButton", GimdowBLEProductInfo], bool] | None
)


@dataclass
class GimdowBLEButtonMapping:
    dp_id: int
    description: ButtonEntityDescription
    force_add: bool = True
    dp_type: GimdowBLEDataPointType | None = None
    is_available: GimdowBLEButtonIsAvailable = None
    value: Any | None = None


GimdowBLECategoryButtonMapping = GimdowBLECategoryMapping[GimdowBLEButtonMapping]

mapping: dict[str, GimdowBLECategoryButtonMapping] = {
    "jtmspro": GimdowBLECategoryButtonMapping(
        products={
            "rlyxv7pe":  # Gimdow
            [
                GimdowBLEButtonMapping(
                    dp_id=44,
                    description=ButtonEntityDescription(
                        key="sync_clock",
                        icon="mdi:clock",
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
                GimdowBLEButtonMapping(
                    dp_id=68,
                    description=ButtonEntityDescription(
                        key="recalibrate",
                        icon="mdi:wrench",
                        entity_category=EntityCategory.CONFIG,
                    ),
                    value=0,
                    dp_type=GimdowBLEDataPointType.DT_ENUM,
                ),
                GimdowBLEButtonMapping(
                    dp_id=68,
                    description=ButtonEntityDescription(
                        key="unlock_more",
                        icon="mdi:lock-open-plus",
                        entity_category=EntityCategory.CONFIG,
                    ),
                    value=1,
                    dp_type=GimdowBLEDataPointType.DT_ENUM,
                ),
                GimdowBLEButtonMapping(
                    dp_id=68,
                    description=ButtonEntityDescription(
                        key="keep_retracted",
                        icon="mdi:lock-open-minus",
                        entity_category=EntityCategory.CONFIG,
                        entity_registry_enabled_default=False,
                    ),
                    value=2,
                    dp_type=GimdowBLEDataPointType.DT_ENUM,
                ),
                GimdowBLEButtonMapping(
                    dp_id=68,
                    description=ButtonEntityDescription(
                        key="add_force",
                        icon="mdi:arm-flex",
                        entity_category=EntityCategory.CONFIG,
                    ),
                    value=3,
                    dp_type=GimdowBLEDataPointType.DT_ENUM,
                ),
            ],
        },
    ),
}


def get_mapping_by_device(device: GimdowBLEDevice) -> list[GimdowBLEButtonMapping]:
    return get_platform_mapping(mapping, device)


class GimdowBLEButton(GimdowBLEEntity, ButtonEntity):
    """Representation of a Gimdow BLE Button."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLEButtonMapping,
    ) -> None:
        super().__init__(coordinator, device, product, mapping.description)
        self._mapping = mapping

    async def async_press(self) -> None:
        """Press the button."""
        dptype = self._mapping.dp_type or GimdowBLEDataPointType.DT_BOOL
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            dptype,
            False if dptype == GimdowBLEDataPointType.DT_BOOL else 0,
        )
        if datapoint:
            if self._mapping.value is not None:
                await datapoint.set_value(self._mapping.value)
            elif self._product.is_lock:
                # Gimdow need true to activate lock/unlock commands
                await datapoint.set_value(True)
            else:
                await datapoint.set_value(not bool(datapoint.value))

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
    entities: list[GimdowBLEButton] = []
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.dp_id, mapping.dp_type
        ):
            entities.append(
                GimdowBLEButton(
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )
    async_add_entities(entities)
