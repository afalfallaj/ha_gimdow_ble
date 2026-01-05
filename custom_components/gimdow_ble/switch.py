"""The Gimdow BLE integration."""
from __future__ import annotations

from dataclasses import dataclass, field

import logging
from typing import Any, Callable

from homeassistant.components.switch import (
    SwitchEntityDescription,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .devices import GimdowBLEData, GimdowBLEEntity, GimdowBLEProductInfo
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)


GimdowBLESwitchGetter = (
    Callable[["GimdowBLESwitch", GimdowBLEProductInfo], bool | None] | None
)


GimdowBLESwitchIsAvailable = (
    Callable[["GimdowBLESwitch", GimdowBLEProductInfo], bool] | None
)


GimdowBLESwitchSetter = (
    Callable[["GimdowBLESwitch", GimdowBLEProductInfo, bool], None] | None
)


@dataclass
class GimdowBLESwitchMapping:
    dp_id: int
    description: SwitchEntityDescription
    force_add: bool = True
    dp_type: GimdowBLEDataPointType | None = None
    bitmap_mask: bytes | None = None
    is_available: GimdowBLESwitchIsAvailable = None
    getter: GimdowBLESwitchGetter = None
    setter: GimdowBLESwitchSetter = None





@dataclass
class GimdowBLECategorySwitchMapping:
    products: dict[str, list[GimdowBLESwitchMapping]] | None = None
    mapping: list[GimdowBLESwitchMapping] | None = None


mapping: dict[str, GimdowBLECategorySwitchMapping] = {
    "jtmspro": GimdowBLECategorySwitchMapping(
        products={
            "rlyxv7pe": [  # Gimdow
                GimdowBLESwitchMapping(
                    dp_id=33,
                    description=SwitchEntityDescription(
                        key="auto_lock",
                        icon="mdi:lock-clock",
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
                GimdowBLESwitchMapping(
                    dp_id=78,
                    description=SwitchEntityDescription(
                        key="change_direction",
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
}


def get_mapping_by_device(device: GimdowBLEDevice) -> list[GimdowBLECategorySwitchMapping]:
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


class GimdowBLESwitch(GimdowBLEEntity, SwitchEntity, RestoreEntity):
    """Representation of a Gimdow BLE Switch."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLESwitchMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        if self._product.lock:
            if (last_state := await self.async_get_last_state()) is not None:
                 if last_state.state in ("on", "off"):
                    is_on = last_state.state == "on"
                    # Populate the device cache so the property logic works
                    self._device.datapoints.get_or_create(
                        self._mapping.dp_id,
                        GimdowBLEDataPointType.DT_BOOL,
                        is_on,
                    )

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""

        if self._mapping.getter:
            return self._mapping.getter(self, self._product)

        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            if (
                datapoint.type
                in [GimdowBLEDataPointType.DT_RAW, GimdowBLEDataPointType.DT_BITMAP]
                and self._mapping.bitmap_mask
            ):
                bitmap_value = bytes(datapoint.value)
                bitmap_mask = self._mapping.bitmap_mask
                for v, m in zip(bitmap_value, bitmap_mask, strict=True):
                    if (v & m) != 0:
                        return True
            else:
                return bool(datapoint.value)
        return False

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        if self._mapping.setter:
            return self._mapping.setter(self, self._product, True)

        new_value: bool | bytes
        if self._mapping.bitmap_mask:
            datapoint = self._device.datapoints.get_or_create(
                self._mapping.dp_id,
                GimdowBLEDataPointType.DT_BITMAP,
                self._mapping.bitmap_mask,
            )
            bitmap_mask = self._mapping.bitmap_mask
            bitmap_value = bytes(datapoint.value)
            new_value = bytes(
                v | m for (v, m) in zip(bitmap_value, bitmap_mask, strict=True)
            )
        else:
            datapoint = self._device.datapoints.get_or_create(
                self._mapping.dp_id,
                GimdowBLEDataPointType.DT_BOOL,
                True,
            )
            new_value = True
        if datapoint:
            self._hass.create_task(datapoint.set_value(new_value))

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if self._mapping.setter:
            return self._mapping.setter(self, self._product, False)

        new_value: bool | bytes
        if self._mapping.bitmap_mask:
            datapoint = self._device.datapoints.get_or_create(
                self._mapping.dp_id,
                GimdowBLEDataPointType.DT_BITMAP,
                self._mapping.bitmap_mask,
            )
            bitmap_mask = self._mapping.bitmap_mask
            bitmap_value = bytes(datapoint.value)
            new_value = bytes(
                v & ~m for (v, m) in zip(bitmap_value, bitmap_mask, strict=True)
            )
        else:
            datapoint = self._device.datapoints.get_or_create(
                self._mapping.dp_id,
                GimdowBLEDataPointType.DT_BOOL,
                False,
            )
            new_value = False
        if datapoint:
            self._hass.create_task(datapoint.set_value(new_value))

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
    entities: list[GimdowBLESwitch] = []
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.dp_id, mapping.dp_type
        ):
            entities.append(
                GimdowBLESwitch(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )
    async_add_entities(entities)
