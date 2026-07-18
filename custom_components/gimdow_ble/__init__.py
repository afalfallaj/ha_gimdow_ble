"""The Gimdow BLE integration."""

from __future__ import annotations

import logging

from bleak_retry_connector import get_device

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .gimdow_ble import GimdowBLEDevice

from .cloud import HASSGimdowBLEDeviceManager
from .const import (
    CONF_ADAPTER,
    CONF_AUTO_LOCK_DELAY_FALLBACK,
    CONF_DOOR_SENSOR,
    DEFAULT_AUTO_LOCK_DELAY_FALLBACK,
    DOMAIN,
    CONF_UNKNOWN_STATE_ACTION,
    CONF_TRANSITION_TIMEOUT,
    OPTIONS_ONLY_KEYS,
    DEFAULT_UNKNOWN_STATE_ACTION,
    UNKNOWN_STATE_ACTION_CONFIRM_LAST,
)
from .devices import GimdowBLECoordinator, GimdowBLEData, get_device_product_info

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.LOCK,
    Platform.SELECT,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gimdow BLE from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), True
    ) or await get_device(address)
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find Gimdow BLE device with address {address}"
        )
    manager = HASSGimdowBLEDeviceManager(hass, entry.data.copy())
    device = GimdowBLEDevice(manager, ble_device)
    await device.initialize()
    product_info = get_device_product_info(device)

    coordinator = GimdowBLECoordinator(hass, device)

    await coordinator.async_config_entry_first_refresh()

    @callback
    def _async_update_ble(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Update from a ble callback."""
        if (
            adapter := entry.options.get(CONF_ADAPTER)
        ) and service_info.source != adapter:
            return

        device.set_ble_device_and_advertisement_data(
            service_info.device, service_info.advertisement
        )

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble,
            BluetoothCallbackMatcher({ADDRESS: address}),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    data = GimdowBLEData(
        entry.title,
        device,
        product_info,
        manager,
        coordinator,
        door_update_signal=f"gimdow_door_update_{device.address}",
        virtual_auto_lock_signal=f"gimdow_virtual_auto_lock_{device.address}",
        virtual_auto_lock_time_signal=f"gimdow_virtual_auto_lock_time_{device.address}",
        has_door_sensor=bool(entry.options.get(CONF_DOOR_SENSOR)),
        unknown_state_action=entry.options.get(
            CONF_UNKNOWN_STATE_ACTION, DEFAULT_UNKNOWN_STATE_ACTION
        ),
        transition_timeout=int(entry.options.get(CONF_TRANSITION_TIMEOUT, 60)),
        auto_lock_delay_fallback=int(
            entry.options.get(
                CONF_AUTO_LOCK_DELAY_FALLBACK, DEFAULT_AUTO_LOCK_DELAY_FALLBACK
            )
        ),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # async_forward_entry_setups only returns once every platform's entities
    # (and therefore every RestoreEntity restore) have finished — HA gathers
    # all platform setup tasks before returning. NUMBER, SWITCH, BINARY_SENSOR,
    # and LOCK are set up concurrently with no ordering guarantee between them,
    # so the lock's auto-lock timer can be started (via the door-sensor path)
    # before the auto_lock_time/virtual_auto_lock restores land, latching a
    # fallback delay that nothing corrects afterwards. Re-emit the door signal
    # now, once every entity's state is guaranteed final, so the lock
    # recomputes the timer from fully-restored state.
    if data.is_door_open is not None:
        async_dispatcher_send(hass, data.door_update_signal, data.is_door_open)

    async def _async_stop(event: Event) -> None:
        """Close the connection."""
        await device.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data: GimdowBLEData = hass.data[DOMAIN].pop(entry.entry_id)
        data.coordinator.stop()
        await data.device.stop()

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to the current version."""
    _LOGGER.debug("Migrating config entry from version %s", entry.version)
    if entry.version > 4:
        _LOGGER.error(
            "Cannot migrate config entry %s from version %s",
            entry.entry_id,
            entry.version,
        )
        return False

    if entry.version == 1:
        old_options = dict(entry.options)
        new_data = dict(entry.data)
        new_options = {}
        for k, v in old_options.items():
            if k in OPTIONS_ONLY_KEYS:
                new_options[k] = v
            else:
                new_data[k] = v
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options, version=2
        )
        _LOGGER.info("Migrated config entry %s to version 2", entry.entry_id)

    if entry.version == 2:
        # Move CONF_DOOR_SENSOR from entry.data to entry.options if present
        new_data = dict(entry.data)
        new_options = dict(entry.options)
        if CONF_DOOR_SENSOR in new_data:
            new_options.setdefault(CONF_DOOR_SENSOR, new_data.pop(CONF_DOOR_SENSOR))
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options, version=3
        )
        _LOGGER.info("Migrated config entry %s to version 3", entry.entry_id)

    if entry.version == 3:
        # No data shape change; version bump required for new config_flow.py VERSION=4
        hass.config_entries.async_update_entry(entry, version=4)
        _LOGGER.info("Migrated config entry %s to version 4", entry.entry_id)

    return True
