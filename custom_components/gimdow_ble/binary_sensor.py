"""The Gimdow BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.const import STATE_ON, CONF_DEVICE_ID

from .const import DOMAIN, CONF_DOOR_SENSOR
from .devices import GimdowBLEData, GimdowBLEEntity, GimdowBLEProductInfo
from .gimdow_ble import GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)


class GimdowBLEBinarySensor(GimdowBLEEntity, BinarySensorEntity):
    """Representation of a Gimdow BLE Binary Sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        door_sensor: str,
        data: GimdowBLEData,
    ) -> None:
        description = BinarySensorEntityDescription(
            key="door_sensor",
            device_class=BinarySensorDeviceClass.DOOR,
            icon="mdi:door",
            name="Door Sensor",
        )
        super().__init__(hass, coordinator, device, product, description)
        self._door_sensor = door_sensor
        self._data = data
        self._attr_is_on = None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        if self._door_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._door_sensor], self._async_door_sensor_changed
                )
            )
            # Initialize state
            state = self.hass.states.get(self._door_sensor)
            if state:
                self._attr_is_on = state.state == STATE_ON
                self._update_state()
                self.async_write_ha_state()

    @callback
    def _async_door_sensor_changed(self, event) -> None:
        """Handle door sensor state changes."""
        new_state = event.data.get("new_state")
        if new_state:
            self._attr_is_on = new_state.state == STATE_ON
            self._update_state()
            self.async_write_ha_state()

    def _update_state(self) -> None:
        """Update shared state and notify listeners."""
        if self._attr_is_on is None:
             return
             
        is_open = self._attr_is_on
        
        self._data.is_door_open = is_open
        async_dispatcher_send(self.hass, self._data.door_update_signal, is_open)

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        return self._attr_is_on


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gimdow BLE binary sensors."""
    data: GimdowBLEData = hass.data[DOMAIN][entry.entry_id]
    
    door_sensor = entry.options.get(CONF_DOOR_SENSOR) or entry.data.get(CONF_DOOR_SENSOR)
    
    if door_sensor:
        async_add_entities([
            GimdowBLEBinarySensor(
                hass,
                data.coordinator,
                data.device,
                data.product,
                door_sensor,
                data=data,
            )
        ])
