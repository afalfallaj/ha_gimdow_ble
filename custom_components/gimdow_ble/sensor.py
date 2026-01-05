"""The Gimdow BLE integration."""
from __future__ import annotations

from dataclasses import dataclass, field

import logging
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    BATTERY_STATE_HIGH,
    BATTERY_STATE_LOW,
    BATTERY_STATE_NORMAL,
    DOMAIN,
)
from .devices import GimdowBLEData, GimdowBLEEntity, GimdowBLEProductInfo
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)

SIGNAL_STRENGTH_DP_ID = -1


GimdowBLESensorIsAvailable = Callable[["GimdowBLESensor", GimdowBLEProductInfo], bool] | None


@dataclass
class GimdowBLESensorMapping:
    dp_id: int
    description: SensorEntityDescription
    force_add: bool = True
    dp_type: GimdowBLEDataPointType | None = None
    getter: Callable[[GimdowBLESensor], None] | None = None
    coefficient: float = 1.0
    icons: list[str] | None = None
    is_available: GimdowBLESensorIsAvailable = None


@dataclass
class GimdowBLEBatteryMapping(GimdowBLESensorMapping):
    description: SensorEntityDescription = field(
        default_factory=lambda: SensorEntityDescription(
            key="battery",
            device_class=SensorDeviceClass.BATTERY,
            native_unit_of_measurement=PERCENTAGE,
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.MEASUREMENT,
        )
    )


@dataclass
class GimdowBLETemperatureMapping(GimdowBLESensorMapping):
    description: SensorEntityDescription = field(
        default_factory=lambda: SensorEntityDescription(
            key="temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
        )
    )


def is_co2_alarm_enabled(self: GimdowBLESensor, product: GimdowBLEProductInfo) -> bool:
    result: bool = True
    datapoint = self._device.datapoints[13]
    if datapoint:
        result = bool(datapoint.value)
    return result


def battery_enum_getter(self: GimdowBLESensor) -> None:
    datapoint = self._device.datapoints[104]
    if datapoint:
        self._attr_native_value = datapoint.value * 20.0


@dataclass
class GimdowBLECategorySensorMapping:
    products: dict[str, list[GimdowBLESensorMapping]] | None = None
    mapping: list[GimdowBLESensorMapping] | None = None


mapping: dict[str, GimdowBLECategorySensorMapping] = {
    "jtmspro": GimdowBLECategorySensorMapping(
        products={
            "rlyxv7pe":  # Smart Lock
            [
                GimdowBLESensorMapping(
                    dp_id=9,
                    description=SensorEntityDescription(
                        key="battery_state",
                        icon="mdi:battery",
                        device_class=SensorDeviceClass.ENUM,
                        options=[
                            BATTERY_STATE_HIGH,
                            BATTERY_STATE_NORMAL,
                            BATTERY_STATE_LOW,
                            BATTERY_STATE_LOW,
                        ],
                    ),
                    icons=[
                        "mdi:battery-check",
                        "mdi:battery-50",
                        "mdi:battery-alert",
                        "mdi:battery-alert",
                    ],
                ),
            ],
        }
    ),
}


def rssi_getter(sensor: GimdowBLESensor) -> None:
    sensor._attr_native_value = sensor._device.rssi


rssi_mapping = GimdowBLESensorMapping(
    dp_id=SIGNAL_STRENGTH_DP_ID,
    description=SensorEntityDescription(
        key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    getter=rssi_getter,
)


def get_mapping_by_device(device: GimdowBLEDevice) -> list[GimdowBLESensorMapping]:
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


class GimdowBLESensor(GimdowBLEEntity, SensorEntity, RestoreEntity):
    """Representation of a Gimdow BLE sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLESensorMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        if self._product.lock:
            if (last_state := await self.async_get_last_state()) is not None:
                if last_state.state != "unknown":
                    self._attr_native_value = last_state.state

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self._mapping.getter is not None:
            self._mapping.getter(self)
        else:
            datapoint = self._device.datapoints[self._mapping.dp_id]
            if datapoint:
                if datapoint.type == GimdowBLEDataPointType.DT_ENUM:
                    if self.entity_description.options is not None:
                        if datapoint.value >= 0 and datapoint.value < len(
                            self.entity_description.options
                        ):
                            self._attr_native_value = self.entity_description.options[
                                datapoint.value
                            ]
                        else:
                            self._attr_native_value = datapoint.value
                    if self._mapping.icons is not None:
                        if datapoint.value >= 0 and datapoint.value < len(
                            self._mapping.icons
                        ):
                            self._attr_icon = self._mapping.icons[datapoint.value]
                elif datapoint.type == GimdowBLEDataPointType.DT_VALUE:
                    self._attr_native_value = (
                        datapoint.value / self._mapping.coefficient
                    )
                else:
                    self._attr_native_value = datapoint.value
        self.async_write_ha_state()

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
    entities: list[GimdowBLESensor] = [
        GimdowBLESensor(
            hass,
            data.coordinator,
            data.device,
            data.product,
            rssi_mapping,
        )
    ]
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.dp_id, mapping.dp_type
        ):
            entities.append(
                GimdowBLESensor(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )
    async_add_entities(entities)
