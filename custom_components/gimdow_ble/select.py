"""The Gimdow BLE integration."""

from __future__ import annotations

from dataclasses import dataclass

import logging
from typing import Any

from homeassistant.components.select import (
    SelectEntityDescription,
    SelectEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity


from . import GimdowBLEConfigEntry
from .devices import (
    GimdowBLECategoryMapping,
    GimdowBLEEntity,
    GimdowBLEProductInfo,
    get_platform_mapping,
)
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)


@dataclass
class GimdowBLESelectMapping:
    dp_id: int
    description: SelectEntityDescription
    force_add: bool = True
    dp_type: GimdowBLEDataPointType | None = None
    value_mapping: dict[str, str] | None = None


GimdowBLECategorySelectMapping = GimdowBLECategoryMapping[GimdowBLESelectMapping]

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


def get_mapping_by_device(device: GimdowBLEDevice) -> list[GimdowBLESelectMapping]:
    return get_platform_mapping(mapping, device)


@dataclass
class _SelectExtraData(ExtraStoredData):
    """Persisted independently of entity availability.

    A lock that's disconnected (past its grace period) when HA stops dumps
    state="unavailable", which .state-parsing on restore would silently
    discard — same failure as number.py/switch.py.
    """

    option: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"option": self.option}

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> _SelectExtraData | None:
        try:
            return cls(restored["option"])
        except KeyError:
            return None


class GimdowBLESelect(GimdowBLEEntity, SelectEntity, RestoreEntity):
    """Representation of a Gimdow BLE select."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLESelectMapping,
    ) -> None:
        super().__init__(coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._attr_options = mapping.description.options

    @property
    def extra_restore_state_data(self) -> _SelectExtraData:
        return _SelectExtraData(self.current_option)

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        if self._product.is_lock:
            if (last_extra := await self.async_get_last_extra_data()) is not None:
                restored = _SelectExtraData.from_dict(last_extra.as_dict())
                value = restored.option if restored is not None else None

                if value is not None:
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
            elif (
                isinstance(value, int)
                and value >= 0
                and value < len(self._attr_options)
            ):
                return self._attr_options[value]
            elif isinstance(value, str):
                return value
            else:
                return None

        return None

    async def async_select_option(self, value: str) -> None:
        """Change the selected option."""
        if value in self._attr_options:
            if self._mapping.value_mapping:
                key = next(
                    (k for k, v in self._mapping.value_mapping.items() if v == value),
                    None,
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
                        await datapoint.set_value(key)
            else:
                int_value = self._attr_options.index(value)
                datapoint = self._device.datapoints.get_or_create(
                    self._mapping.dp_id,
                    GimdowBLEDataPointType.DT_ENUM,
                    int_value,
                )
                if datapoint:
                    await datapoint.set_value(int_value)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GimdowBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gimdow BLE sensors."""
    data = entry.runtime_data
    mappings = get_mapping_by_device(data.device)
    entities: list[GimdowBLESelect] = []
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.dp_id, mapping.dp_type
        ):
            entities.append(
                GimdowBLESelect(
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )
    async_add_entities(entities)
