"""The Gimdow BLE integration."""

from __future__ import annotations

from dataclasses import dataclass

import logging
from typing import Any

from homeassistant.components.select import (
    SelectEntityDescription,
    SelectEntity,
)
from homeassistant.core import HomeAssistant, callback
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
    value_mapping: dict[Any, str] | None = None


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
                            "normal",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
                GimdowBLESelectMapping(
                    dp_id=78,
                    description=SelectEntityDescription(
                        key="change_direction",
                        options=[
                            "right_hand",
                            "left_hand",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                    dp_type=GimdowBLEDataPointType.DT_BOOL,
                    value_mapping={
                        True: "left_hand",
                        False: "right_hand",
                    }
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
        self._master_option: str | None = None

    @property
    def extra_restore_state_data(self) -> _SelectExtraData:
        return _SelectExtraData(self.current_option)

    def _get_raw_from_option(self, value: str) -> Any:
        if self._mapping.value_mapping:
            for k, v in self._mapping.value_mapping.items():
                if v == value:
                    return k
        elif value in self._attr_options:
            return self._attr_options.index(value)
        return None

    def _get_option_from_raw(self, value: Any) -> str | None:
        if self._mapping.value_mapping:
            return self._mapping.value_mapping.get(value)
        elif isinstance(value, int) and 0 <= value < len(self._attr_options):
            return self._attr_options[value]
        elif isinstance(value, str):
            return value
        return None

    def _get_dp_type(self) -> GimdowBLEDataPointType:
        if self._mapping.value_mapping:
            return self._mapping.dp_type or GimdowBLEDataPointType.DT_STRING
        return self._mapping.dp_type or GimdowBLEDataPointType.DT_ENUM

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        if self._product.is_lock:
            if (last_extra := await self.async_get_last_extra_data()) is not None:
                restored = _SelectExtraData.from_dict(last_extra.as_dict())
                value = restored.option if restored is not None else None

                if value is not None:
                    self._master_option = value
                    raw_value = self._get_raw_from_option(value)
                    if raw_value is not None:
                        self._device.datapoints.get_or_create(
                            self._mapping.dp_id,
                            self._get_dp_type(),
                            raw_value,
                        )

    @callback
    def _handle_coordinator_update(self) -> None:
        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            device_option = self._get_option_from_raw(datapoint.value)
            
            if self._master_option is None:
                self._master_option = device_option
            elif device_option is not None and device_option != self._master_option:
                _LOGGER.warning(
                    "[%s] Device pushed %s=%s, but HA master is %s. Overwriting device.",
                    self._device.address,
                    self._mapping.description.key,
                    device_option,
                    self._master_option
                )
                raw_value = self._get_raw_from_option(self._master_option)
                if raw_value is not None:
                    dp = self._device.datapoints.get_or_create(
                        self._mapping.dp_id, self._get_dp_type(), raw_value
                    )
                    self._device._create_safe_task(dp.set_value(raw_value))
                    
        self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:
        """Return the selected entity option to represent the entity state."""
        if self._master_option is not None:
            return self._master_option
            
        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            return self._get_option_from_raw(datapoint.value)
        return None

    async def async_select_option(self, value: str) -> None:
        """Change the selected option."""
        if value in self._attr_options:
            self._master_option = value
            self.async_write_ha_state()
            
            raw_value = self._get_raw_from_option(value)
            if raw_value is not None:
                datapoint = self._device.datapoints.get_or_create(
                    self._mapping.dp_id,
                    self._get_dp_type(),
                    raw_value,
                )
                if datapoint:
                    await datapoint.set_value(raw_value)



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
