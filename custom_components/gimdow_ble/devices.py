"""The Gimdow BLE integration."""

from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Generic, TypeVar
import logging

from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
    async_dismiss as pn_async_dismiss,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import (
    DeviceInfo,
    EntityDescription,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .gimdow_ble import (
    AbstractGimdowBLEDeviceManager,
    GimdowBLEDataPoint,
    GimdowBLEDevice,
    GimdowBLEDeviceCredentials,
)
from .gimdow_ble.const import DP_LOCK_MOTOR_STATE

if TYPE_CHECKING:
    from home_assistant_bluetooth import BluetoothServiceInfoBleak
    from .cloud import HASSGimdowBLEDeviceManager
from .const import (
    DEFAULT_AUTO_LOCK_DELAY_FALLBACK,
    DEVICE_DEF_MANUFACTURER,
    DOMAIN,
    SET_DISCONNECTED_DELAY,
    DEFAULT_UNKNOWN_STATE_ACTION,
    UNKNOWN_STATE_ACTION_CONFIRM_LAST,
)

_LOGGER = logging.getLogger(__name__)

# Entity keys that remain available when BLE is disconnected.
# Sensors report last-known readings; door_sensor mirrors an HA entity (no BLE dependency).
_PERSISTENT_ENTITY_KEYS = frozenset({"battery_state", "signal_strength", "door_sensor"})


@dataclass
class GimdowBLEProductInfo:
    name: str
    manufacturer: str = DEVICE_DEF_MANUFACTURER
    is_lock: bool = False


class GimdowBLEEntity(CoordinatorEntity["GimdowBLECoordinator"]):
    """Gimdow BLE base entity."""

    def __init__(
        self,
        coordinator: GimdowBLECoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._device = device
        self._product = product
        if description.translation_key is None:
            self._attr_translation_key = description.key
        self.entity_description = description
        self._attr_has_entity_name = True
        self._attr_device_info = get_device_info(self._device)
        self._attr_unique_id = f"{self._device.device_id}-{description.key}"

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Sensor/binary_sensor entities in _PERSISTENT_ENTITY_KEYS stay available when
        BLE is sleeping — they report last-known values rather than showing unavailable.
        All other entities (lock, buttons, switches, numbers, selects) follow live BLE
        connectivity, preventing misleading "available" state when the device is unreachable.
        """
        if self.entity_description.key in _PERSISTENT_ENTITY_KEYS:
            return True
        return self._coordinator.connected

    @property
    def device(self) -> GimdowBLEDevice:
        """Return the associated BLE Device."""
        return self._device

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class GimdowBLECoordinator(DataUpdateCoordinator[GimdowBLEDevice]):
    """Data coordinator for receiving Gimdow BLE updates."""

    def __init__(self, hass: HomeAssistant, device: GimdowBLEDevice) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )
        self._device = device
        self._disconnected: bool = True
        self._unsub_disconnect: CALLBACK_TYPE | None = None
        self._unregister_callbacks: list[Callable[[], None]] = [
            device.register_connected_callback(self._async_handle_connect),
            device.register_callback(self._async_handle_update),
            device.register_disconnected_callback(self._async_handle_disconnect),
        ]

    @property
    def connected(self) -> bool:
        return not self._disconnected

    def stop(self) -> None:
        """Unregister all device callbacks and cancel pending timers."""
        for unregister in self._unregister_callbacks:
            unregister()
        self._unregister_callbacks.clear()
        if self._unsub_disconnect is not None:
            self._unsub_disconnect()
            self._unsub_disconnect = None

    @callback
    def _async_handle_connect(self) -> None:
        pn_async_dismiss(self.hass, f"gimdow_ble_disconnected_{self._device.address}")
        if self._unsub_disconnect is not None:
            self._unsub_disconnect()
            self._unsub_disconnect = None
        if self._disconnected:
            # DP47 (lock_motor_state) is push-only and never sent by the device on
            # reconnect. Clear the stale pre-disconnect value so is_locked returns None
            # until the device pushes DP47 again (command echo or manual operation).
            self._device.datapoints.clear(DP_LOCK_MOTOR_STATE)
            self._disconnected = False
            self.async_update_listeners()
            # Refresh diagnostics (DP9 battery etc.) after reconnect. DP47 won't
            # be returned by the device, but other DPs will be updated.
            self._device.schedule_update(name="post-reconnect-update")

    async def _async_update_data(self) -> GimdowBLEDevice:
        """Fetch data from the device."""
        try:
            await asyncio.wait_for(self._device.update(), timeout=30)
        except asyncio.TimeoutError:
            # Sleeping locks won't respond during the initial poll; that is expected
            # and should not prevent the entry from loading.
            _LOGGER.debug(
                "Initial update timed out for %s — device may be sleeping",
                self._device.address,
            )
        except Exception as err:
            raise UpdateFailed(f"Error polling {self._device.address}: {err}") from err
        return self._device

    @callback
    def _async_handle_update(self, updates: list[GimdowBLEDataPoint]) -> None:
        """Trigger coordinator listeners on device DP push.

        Data arriving proves the BLE session is still alive — cancel any
        pending disconnect grace timer, but do not duplicate the connect
        handshake that _async_handle_connect already handles.
        """
        if self._unsub_disconnect is not None:
            self._unsub_disconnect()
            self._unsub_disconnect = None
        self.async_set_updated_data(self._device)

    @callback
    def _set_disconnected(self, _: None) -> None:
        """Invoke the idle timeout callback, called when the alarm fires."""
        self._disconnected = True
        self._unsub_disconnect = None
        pn_async_create(
            self.hass,
            message=(
                f"The Gimdow lock at {self._device.address} has been unreachable "
                f"for {SET_DISCONNECTED_DELAY // 60} minutes. "
                "Check BLE range and adapter status."
            ),
            title="Gimdow Lock Unreachable",
            notification_id=f"gimdow_ble_disconnected_{self._device.address}",
        )
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
    door_update_signal: str
    virtual_auto_lock_signal: str
    virtual_auto_lock_time_signal: str
    virtual_auto_lock: bool = False
    is_door_open: bool | None = None
    has_door_sensor: bool = False
    unknown_state_action: str = DEFAULT_UNKNOWN_STATE_ACTION
    transition_timeout: int = 60
    auto_lock_delay_fallback: int = DEFAULT_AUTO_LOCK_DELAY_FALLBACK


@dataclass
class GimdowBLECategoryInfo:
    products: dict[str, GimdowBLEProductInfo]


devices_database: dict[str, GimdowBLECategoryInfo] = {
    "jtmspro": GimdowBLECategoryInfo(
        products={
            "rlyxv7pe":  # Gimdow device product_id
            GimdowBLEProductInfo(
                name="A1 PRO MAX",
                # Gimdow identity
                is_lock=True,
            ),
        },
    ),
}


def get_product_info_by_ids(
    category: str, product_id: str
) -> GimdowBLEProductInfo | None:
    category_info = devices_database.get(category)
    if category_info is not None:
        return category_info.products.get(product_id)
    else:
        return None


def get_device_product_info(device: GimdowBLEDevice) -> GimdowBLEProductInfo | None:
    return get_product_info_by_ids(device.category, device.product_id)


def get_short_address(address: str) -> str:
    return address.replace("-", ":").upper().replace(":", "")[-6:]


async def get_device_readable_name(
    discovery_info: BluetoothServiceInfoBleak,
    manager: AbstractGimdowBLEDeviceManager | None,
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
        return f"{product_info.name} {short_address}"
    if credentials:
        return f"{credentials.device_name} {short_address}"
    return f"{discovery_info.device.name} {short_address}"


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
        model=device.product_model or product_name,
        model_id=device.product_id,
        name=f"{product_name} {get_short_address(device.address)}",
        sw_version=f"{device.device_version} (protocol {device.protocol_version})",
    )
    return result


# ---------------------------------------------------------------------------
# Generic platform mapping helpers
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


@dataclass
class GimdowBLECategoryMapping(Generic[_T]):
    """Generic category → (product → mapping) lookup table.

    Replaces per-platform GimdowBLECategoryXMapping dataclasses.
    """

    products: dict[str, list[_T]] | None = None
    mapping: list[_T] | None = None


def get_platform_mapping(
    table: dict[str, GimdowBLECategoryMapping[_T]],
    device: GimdowBLEDevice,
) -> list[_T]:
    """Two-level lookup: category → product_id → fallback to category default."""
    cat = table.get(device.category)
    if cat is None:
        return []
    if cat.products:
        result = cat.products.get(device.product_id)
        if result is not None:
            return result
    return cat.mapping or []
