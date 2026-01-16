"""The Gimdow BLE integration."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from .gimdow_ble import GimdowBLEDataPointType

import logging
from homeassistant.const import CONF_ADDRESS, CONF_DEVICE_ID

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import (
    DeviceInfo,
    EntityDescription,
    generate_entity_id,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from home_assistant_bluetooth import BluetoothServiceInfoBleak
from .gimdow_ble import (
    AbstaractGimdowBLEDeviceManager,
    GimdowBLEDataPoint,
    GimdowBLEDevice,
    GimdowBLEDeviceCredentials,
)

from .cloud import HASSGimdowBLEDeviceManager
from .const import (
    DEVICE_DEF_MANUFACTURER,
    DOMAIN,
    SET_DISCONNECTED_DELAY,
    DPCode,
    DPType,
)

from .base import IntegerTypeData, EnumTypeData
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)





@dataclass
class GimdowBLEProductInfo:
    name: str
    manufacturer: str = DEVICE_DEF_MANUFACTURER
    lock: int | None = None

class GimdowBLEEntity(CoordinatorEntity):
    """Gimdow BLE base entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: GimdowBLECoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._hass = hass
        self._coordinator = coordinator
        self._device = device
        self._product = product
        if description.translation_key is None:
            self._attr_translation_key = description.key
        self.entity_description = description
        self._attr_has_entity_name = True
        self._attr_device_info = get_device_info(self._device)
        self._attr_unique_id = f"{self._device.device_id}-{description.key}"
        self.entity_id = generate_entity_id(
            "sensor.{}", self._attr_unique_id, hass=hass
        )
        if product.lock:
             pass

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        """Return if entity is available."""
        # For Gimdow lock (specifically A1 PRO MAX), non-lock entities (battery, etc.) 
        # should persist state even when disconnected/sleeping.
        # Lock entity itself (DP 47) should show unavailable to avoid false security.
        is_gimdow = (
            self._product.lock 
            and self.entity_description.key != "lock"
        )
        if is_gimdow:
             return True
        
        # _LOGGER.debug(
        #     "Entity %s available check: connected=%s, product.lock=%s, product_id=%s", 
        #     self.entity_id, 
        #     self._coordinator.connected,
        #     self._product.lock if self._product else "None",
        #     self._device.product_id
        # )
        return self._coordinator.connected

    @property
    def device(self) -> GimdowBLEDevice:
        """Return the associated BLE Device."""
        return self._device

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    def send_dp_value(self,
        key: DPCode | None,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str | None = None) -> None:

        dpid = self.find_dpid(key)
        if dpid is not None:
            datapoint = self._device.datapoints.get_or_create(
                    dpid,
                    type,
                    value,
                )
            self._hass.async_create_task(datapoint.set_value(value))

    
    def _send_command(self, commands : list[dict[str, Any]]) -> None:
        """Send the commands to the device"""
        for command in commands:
            code = command.get("code")
            value = command.get("value")

            if code and value is not None:
                dttype = self.get_dptype(code)
                if isinstance(value, str):
                    # We suppose here that cloud JSON type are sent as string
                    if dttype == DPType.STRING or dttype == DPType.JSON:
                        self.send_dp_value(code, GimdowBLEDataPointType.DT_STRING, value)
                    elif dttype == DPType.ENUM:
                        int_value = 0
                        values = self.device.function[code].values
                        if isinstance(self.device.function[code].values, dict):
                            range = self.device.function[code].values.get("range")
                            if isinstance(range, list):
                                int_value = range.index(value) if value in range else None
                        self.send_dp_value(code, GimdowBLEDataPointType.DT_ENUM, int_value)

                elif isinstance(value, bool):
                    self.send_dp_value(code, GimdowBLEDataPointType.DT_BOOL, value)
                else:
                    self.send_dp_value(code, GimdowBLEDataPointType.DT_VALUE, value)


    def find_dpid(
        self, dpcode: DPCode | None, prefer_function: bool = False
    ) -> int | None:
        """Returns the dp id for the given code"""
        if dpcode is None:
            return None

        order = ["status_range", "function"]
        if prefer_function:
            order = ["function", "status_range"]
        for key in order:
            if dpcode in getattr(self.device, key):
                return getattr(self.device, key)[dpcode].dp_id

        return None

    def find_dpcode(
        self,
        dpcodes: str | DPCode | tuple[DPCode, ...] | None,
        *,
        prefer_function: bool = False,
        dptype: DPType | None = None,
    ) -> DPCode | EnumTypeData | IntegerTypeData | None:
        """Find a matching DP code available on for this device."""
        if dpcodes is None:
            return None

        if isinstance(dpcodes, str):
            dpcodes = (DPCode(dpcodes),)
        elif not isinstance(dpcodes, tuple):
            dpcodes = (dpcodes,)

        order = ["status_range", "function"]
        if prefer_function:
            order = ["function", "status_range"]

        # When we are not looking for a specific datatype, we can append status for
        # searching
        if not dptype:
            order.append("status")

        for dpcode in dpcodes:
            for key in order:
                if dpcode not in getattr(self.device, key):
                    continue
                if (
                    dptype == DPType.ENUM
                    and getattr(self.device, key)[dpcode].type == DPType.ENUM
                ):
                    if not (
                        enum_type := EnumTypeData.from_json(
                            dpcode, getattr(self.device, key)[dpcode].values
                        )
                    ):
                        continue
                    return enum_type

                if (
                    dptype == DPType.INTEGER
                    and getattr(self.device, key)[dpcode].type == DPType.INTEGER
                ):
                    if not (
                        integer_type := IntegerTypeData.from_json(
                            dpcode, getattr(self.device, key)[dpcode].values
                        )
                    ):
                        continue
                    return integer_type

                if dptype not in (DPType.ENUM, DPType.INTEGER):
                    return dpcode

        return None


    def get_dptype(
        self, dpcode: DPCode | None, prefer_function: bool = False
    ) -> DPType | None:
        """Find a matching DPCode data type available on for this device."""
        if dpcode is None:
            return None

        order = ["status_range", "function"]
        if prefer_function:
            order = ["function", "status_range"]
        for key in order:
            if dpcode in getattr(self.device, key):
                return DPType(getattr(self.device, key)[dpcode].type)

        return None




class GimdowBLECoordinator(DataUpdateCoordinator[GimdowBLEDevice]):
    """Data coordinator for receiving Gimdow BLE updates."""

    def __init__(self, hass: HomeAssistant, device: GimdowBLEDevice) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=60),
        )
        self._device = device
        self._disconnected: bool = True
        self._unsub_disconnect: CALLBACK_TYPE | None = None
        device.register_connected_callback(self._async_handle_connect)
        device.register_callback(self._async_handle_update)
        device.register_disconnected_callback(self._async_handle_disconnect)

    @property
    def connected(self) -> bool:
        return not self._disconnected

    @callback
    def _async_handle_connect(self) -> None:
        if self._unsub_disconnect is not None:
            self._unsub_disconnect()
        if self._disconnected:
            self._disconnected = False
            self.async_update_listeners()

    async def _async_update_data(self) -> GimdowBLEDevice:
        """Fetch data from the device."""
        # Polling logic
        await self._device.update()
        return self._device

    @callback
    def _async_handle_update(self, updates: list[GimdowBLEDataPoint]) -> None:
        """Just trigger the callbacks."""
        self._async_handle_connect()
        self.async_set_updated_data(self._device)

    @callback
    def _set_disconnected(self, _: None) -> None:
        """Invoke the idle timeout callback, called when the alarm fires."""
        self._disconnected = True
        self._unsub_disconnect = None
        self.async_update_listeners()

    @callback
    def _async_handle_disconnect(self) -> None:
        """Trigger the callbacks for disconnected."""
        if self._unsub_disconnect is None:
            delay: float = SET_DISCONNECTED_DELAY
            self._unsub_disconnect = async_call_later(
                self.hass, delay, self._set_disconnected
            )


@dataclass
class GimdowBLEData:
    """Data for the Gimdow BLE integration."""

    title: str
    device: GimdowBLEDevice
    product: GimdowBLEProductInfo
    manager: HASSGimdowBLEDeviceManager
    coordinator: GimdowBLECoordinator
    coordinator: GimdowBLECoordinator
    door_update_signal: str
    virtual_auto_lock: bool = False
    is_door_open: bool | None = None


@dataclass
class GimdowBLECategoryInfo:
    products: dict[str, GimdowBLEProductInfo]
    info: GimdowBLEProductInfo | None = None


devices_database: dict[str, GimdowBLECategoryInfo] = {
    "jtmspro": GimdowBLECategoryInfo(
        products={
            "rlyxv7pe":  # Gimdow device product_id
            GimdowBLEProductInfo(
                name="A1 PRO MAX",
                # Gimdow identity
                lock=1,
            ),
        },
    ),
}

def get_product_info_by_ids(
    category: str, product_id: str
) -> GimdowBLEProductInfo | None:
    category_info = devices_database.get(category)
    if category_info is not None:
        product_info = category_info.products.get(product_id)
        if product_info is not None:
            return product_info
        return category_info.info
    else:
        return None


def get_device_product_info(device: GimdowBLEDevice) -> GimdowBLEProductInfo | None:
    return get_product_info_by_ids(device.category, device.product_id)


def get_short_address(address: str) -> str:
    results = address.replace("-", ":").upper().split(":")
    return f"{results[-3]}{results[-2]}{results[-1]}"[-6:]


async def get_device_readable_name(
    discovery_info: BluetoothServiceInfoBleak,
    manager: AbstaractGimdowBLEDeviceManager | None,
) -> str:
    credentials: GimdowBLEDeviceCredentials | None = None
    product_info: GimdowBLEProductInfo | None = None
    if manager:
        credentials = await manager.get_device_credentials(discovery_info.address)
        if credentials:
            product_info = get_product_info_by_ids(
                credentials.category,
                credentials.product_id,
            )
    short_address = get_short_address(discovery_info.address)
    if product_info:
        return "%s %s" % (product_info.name, short_address)
    if credentials:
        return "%s %s" % (credentials.device_name, short_address)
    return "%s %s" % (discovery_info.device.name, short_address)


def get_device_info(device: GimdowBLEDevice) -> DeviceInfo | None:
    product_info = None
    if device.category and device.product_id:
        product_info = get_product_info_by_ids(device.category, device.product_id)
    product_name: str
    if product_info:
        product_name = product_info.name
    else:
        product_name = device.name
    result = DeviceInfo(
        connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        hw_version=device.hardware_version,
        identifiers={(DOMAIN, device.address)},
        manufacturer=(
            product_info.manufacturer if product_info else DEVICE_DEF_MANUFACTURER
        ),
        model=("%s (%s)")
        % (
            device.product_model or product_name,
            device.product_id,
        ),
        name=("%s %s")
        % (
            product_name,
            get_short_address(device.address),
        ),
        sw_version=("%s (protocol %s)")
        % (
            device.device_version,
            device.protocol_version,
        ),
    )
    return result
