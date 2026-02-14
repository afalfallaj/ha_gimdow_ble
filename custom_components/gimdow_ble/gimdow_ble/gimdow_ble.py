from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from collections.abc import Callable
from struct import pack, unpack
from dataclasses import dataclass
from typing import Any

import json

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakDBusError
from bleak_retry_connector import BLEAK_BACKOFF_TIME
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakError,
    BleakNotFoundError,
    establish_connection,
)
from Crypto.Cipher import AES

from .const import (
    CHARACTERISTIC_NOTIFY,
    CHARACTERISTIC_WRITE,
    GATT_MTU,
    MANUFACTURER_DATA_ID,
    RESPONSE_WAIT_TIMEOUT,
    SERVICE_UUID,
    GimdowBLECode,
    GimdowBLEDataPointType,
)

from ..const import (
    DPType,
)

from .exceptions import (
    GimdowBLEDataCRCError,
    GimdowBLEDataFormatError,
    GimdowBLEDataLengthError,
    GimdowBLEDeviceError,
    GimdowBLEEnumValueError,
)
from .manager import AbstaractGimdowBLEDeviceManager, GimdowBLEDeviceCredentials

_LOGGER = logging.getLogger(__name__)


BLEAK_EXCEPTIONS = (*BLEAK_RETRY_EXCEPTIONS, OSError)

#@dataclass
class GimdowBLEEntityDescription:
    # Added to info that we get from the cloud
    function: list[dict[str, dict]]  | None = None
    status_range: list[dict[str, dict]]  | None = None

    # Replace the values that we got from the cloud
    values_overrides: dict[str, dict] | None = None

    # Values if nothing was set from the cloud
    values_defaults: dict[str, dict] | None = None


class GimdowBLEDataPoint:
    def __init__(
        self,
        owner: GimdowBLEDataPoints,
        id: int,
        timestamp: float,
        flags: int,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str,
    ) -> None:
        self._owner = owner
        self._id = id
        self._value = value
        self._changed_by_device = False
        self._update_from_device(timestamp, flags, type, value)

    def _update_from_device(
        self,
        timestamp: float,
        flags: int,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str,
    ) -> None:
        self._timestamp = timestamp
        self._flags = flags
        self._type = type
        self._changed_by_device = self._value != value
        self._value = value

    def _get_value(self) -> bytes:
        match self._type:
            case GimdowBLEDataPointType.DT_RAW | GimdowBLEDataPointType.DT_BITMAP:
                return self._value
            case GimdowBLEDataPointType.DT_BOOL:
                return pack(">B", 1 if self._value else 0)
            case GimdowBLEDataPointType.DT_VALUE:
                return pack(">i", self._value)
            case GimdowBLEDataPointType.DT_ENUM:
                if self._value > 0xFFFF:
                    return pack(">I", self._value)
                elif self._value > 0xFF:
                    return pack(">H", self._value)
                else:
                    return pack(">B", self._value)
            case GimdowBLEDataPointType.DT_STRING:
                return self._value.encode()

    @property
    def id(self) -> int:
        return self._id

    @property
    def timestamp(self) -> float:
        return self._timestamp

    @property
    def flags(self) -> int:
        return self._flags

    @property
    def type(self) -> GimdowBLEDataPointType:
        return self._type

    @property
    def value(self) -> bytes | bool | int | str:
        return self._value

    @property
    def changed_by_device(self) -> bool:
        return self._changed_by_device

    def __repr__(self): 
        return f"{{id:{self.id} type:{self.type} value:{self.value}}}"

    def __str__(self):
        return f"{self}"

    async def set_value(self, value: bytes | bool | int | str) -> None:
        match self._type:
            case GimdowBLEDataPointType.DT_RAW | GimdowBLEDataPointType.DT_BITMAP:
                self._value = bytes(value)
            case GimdowBLEDataPointType.DT_BOOL:
                self._value = bool(value)
            case GimdowBLEDataPointType.DT_VALUE:
                self._value = int(value)
            case GimdowBLEDataPointType.DT_ENUM:
                value = int(value)
                if value >= 0:
                    self._value = value
                else:
                    raise GimdowBLEEnumValueError()

            case GimdowBLEDataPointType.DT_STRING:
                self._value = str(value)

        self._changed_by_device = False
        await self._owner._update_from_user(self._id)


class GimdowBLEDataPoints:
    def __init__(self, owner: GimdowBLEDevice) -> None:
        self._owner = owner
        self._datapoints: dict[int, GimdowBLEDataPoint] = {}
        self._update_started: int = 0
        self._updated_datapoints: list[int] = []

    def __len__(self) -> int:
        return len(self._datapoints)

    def __getitem__(self, key: int) -> GimdowBLEDataPoint | None:
        return self._datapoints.get(key)

    def has_id(self, id: int, type: GimdowBLEDataPointType | None = None) -> bool:
        return (id in self._datapoints) and (
            (type is None) or (self._datapoints[id].type == type)
        )

    def get_or_create(
        self,
        id: int,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str | None = None,
    ) -> GimdowBLEDataPoint:
        datapoint = self._datapoints.get(id)
        if datapoint:
            return datapoint
        datapoint = GimdowBLEDataPoint(self, id, time.time(), 0, type, value)
        self._datapoints[id] = datapoint
        return datapoint

    def begin_update(self) -> None:
        self._update_started += 1

    async def end_update(self) -> None:
        if self._update_started > 0:
            self._update_started -= 1
            if self._update_started == 0 and len(self._updated_datapoints) > 0:
                await self._owner._send_datapoints(self._updated_datapoints)
                self._updated_datapoints = []

    def _update_from_device(
        self,
        dp_id: int,
        timestamp: float,
        flags: int,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str,
    ) -> None:
        dp = self._datapoints.get(dp_id)
        if dp:
            dp._update_from_device(timestamp, flags, type, value)
        else:
            self._datapoints[dp_id] = GimdowBLEDataPoint(
                self, dp_id, timestamp, flags, type, value
            )

    async def _update_from_user(self, dp_id: int) -> None:
        if self._update_started > 0:
            if dp_id in self._updated_datapoints:
                self._updated_datapoints.remove(dp_id)
            self._updated_datapoints.append(dp_id)
        else:
            await self._owner._send_datapoints([dp_id])


global_connect_lock = asyncio.Lock()

@dataclass
class GimdowBLEDeviceFunction:
    code: str
    dp_id: int 
    type: DPType
    values: str | dict | list | None

    def __setattr__(self, name:str, value:str | dict | list | None):
        if name == "values":
            # string values are JSON representations of the actual values
            if isinstance(value, str) and (v := json.loads(value)):
                value = v
        super().__setattr__(name, value)

class GimdowBLEDevice:
    def __init__(
        self,
        device_manager: AbstaractGimdowBLEDeviceManager,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData | None = None,
    ) -> None:
        """Init the GimdowBLE."""
        self._device_manager = device_manager
        self._device_info: GimdowBLEDeviceCredentials | None = None
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        self._operation_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect = False
        self._connected_callbacks: list[Callable[[], None]] = []
        self._callbacks: list[Callable[[list[GimdowBLEDataPoint]], None]] = []
        self._disconnected_callbacks: list[Callable[[], None]] = []
        self._current_seq_num = 1
        self._seq_num_lock = asyncio.Lock()

        self._is_bound = False
        self._flags = 0
        self._protocol_version = 2

        self._device_version: str = ""
        self._protocol_version_str: str = ""
        self._hardware_version: str = ""

        self._device_info: GimdowBLEDeviceCredentials | None = None

        self._auth_key: bytes | None = None
        self._local_key: bytes | None = None
        self._login_key: bytes | None = None
        self._session_key: bytes | None = None

        self._is_paired = False

        self._input_buffer: bytearray | None = None
        self._input_expected_packet_num = 0
        self._input_expected_length = 0
        self._input_expected_responses: dict[int,
                                             asyncio.Future[int] | None] = {}
        # self._input_future: asyncio.Future[int] | None = None

        self._datapoints = GimdowBLEDataPoints(self)

        self._function = {}
        self._status_range = {}
        self._is_resolving = False


    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Set the ble device."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data

    async def initialize(self) -> None:
        _LOGGER.debug("%s: Initializing", self.address)
        if await self._update_device_info():
            self._decode_advertisement_data()
            
    def _build_pairing_request(self) -> bytes:
        result = bytearray()

        result += self._device_info.uuid.encode()
        result += self._local_key
        result += self._device_info.device_id.encode()
        for _ in range(44 - len(result)):
            result += b"\x00"

        return result

    async def pair(self) -> None:
        """
        _LOGGER.debug("%s: Sending pairing request: %s",
            self.address, data.hex()
        )
        """
        await self._send_packet(
            GimdowBLECode.FUN_SENDER_PAIR, self._build_pairing_request()
        )

    async def update(self) -> None:
        _LOGGER.debug("%s: Updating", self.address)
        await self._send_packet(GimdowBLECode.FUN_SENDER_DEVICE_STATUS, bytes())

    async def _update_device_info(self) -> bool:
        if self._device_info is None:
            if self._device_manager:
                self._device_info = await self._device_manager.get_device_credentials(
                    self._ble_device.address, False
                )
            if self._device_info:
                self._local_key = self._device_info.local_key[:6].encode()
                self._login_key = hashlib.md5(self._local_key).digest()

                self.append_functions(self._device_info.functions, self._device_info.status_range)

        return self._device_info is not None

    def append_functions(self, function: list[dict], status_range: list[dict]) -> None:
        if function:
            for f in function:
                dpcode = f.get("code")
                if dpcode:
                    self.function[dpcode] = GimdowBLEDeviceFunction(**f)
                        
            for f in status_range:
                dpcode = f.get("code")
                if dpcode:
                    self.status_range[dpcode] = GimdowBLEDeviceFunction(**f)

    def update_description(self, description: GimdowBLEEntityDescription | None) -> None:
        if not description:
            return
        
        self.append_functions(description.function, description.status_range)

        if description.values_overrides:
            for key in description.values_overrides:
                values = description.values_overrides.values
                if f := self.function.get(key):
                    f.values = values

                if f := self.status_range.get(key):
                    f.values = values

        if description.values_defaults:
            for key in description.values_defaults:
                values = description.values_defaults.values
                if f := self.function.get(key) and not f.values:
                    f.values = values

                if f := self.status_range.get(key) and not f.values:
                    f.values = values

    def _decode_advertisement_data(self) -> None:
        raw_product_id: bytes | None = None
        # raw_product_key: bytes | None = None
        raw_uuid: bytes | None = None
        if self._advertisement_data:
            if self._advertisement_data.service_data:
                service_data = self._advertisement_data.service_data.get(
                    SERVICE_UUID)
                if service_data and len(service_data) > 1:
                    match service_data[0]:
                        case 0:
                            raw_product_id = service_data[1:]
                        # case 1:
                        #    raw_product_key = service_data[1:]

            if self._advertisement_data.manufacturer_data:
                manufacturer_data = self._advertisement_data.manufacturer_data.get(
                    MANUFACTURER_DATA_ID
                )
                if manufacturer_data and len(manufacturer_data) > 6:
                    self._is_bound = (manufacturer_data[0] & 0x80) != 0
                    self._protocol_version = manufacturer_data[1]
                    raw_uuid = manufacturer_data[6:]
                    if raw_product_id:
                        key = hashlib.md5(raw_product_id).digest()
                        cipher = AES.new(key, AES.MODE_CBC, key)
                        raw_uuid = cipher.decrypt(raw_uuid)
                        self._uuid = raw_uuid.decode("utf-8")

    @property
    def address(self) -> str:
        """Return the address."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Get the name of the device."""
        if self._device_info:
            return self._device_info.device_name
        else:
            return self._ble_device.name or self._ble_device.address

    @property
    def rssi(self) -> int | None:
        """Get the rssi of the device."""
        if self._advertisement_data:
            return self._advertisement_data.rssi
        return None

    @property
    def uuid(self) -> str:
        if self._device_info is not None:
            return self._device_info.uuid
        else:
            return ""

    @property
    def local_key(self) -> str:
        if self._device_info is not None:
            return self._device_info.local_key
        else:
            return ""

    @property
    def category(self) -> str:
        if self._device_info is not None:
            return self._device_info.category
        else:
            return ""

    @property
    def device_id(self) -> str:
        if self._device_info is not None:
            return self._device_info.device_id
        else:
            return ""

    @property
    def product_id(self) -> str:
        if self._device_info is not None:
            return self._device_info.product_id
        else:
            return ""

    @property
    def product_model(self) -> str:
        if self._device_info is not None:
            return self._device_info.product_model
        else:
            return ""

    @property
    def product_name(self) -> str:
        if self._device_info is not None:
            return self._device_info.product_name
        else:
            return ""

    @property
    def function(self) -> dict(str, dict):
        return self._function

    @property
    def status_range(self) -> dict(str, dict):
        return self._status_range

    @property
    def device_version(self) -> str:
        return self._device_version

    @property
    def hardware_version(self) -> str:
        return self._hardware_version

    @property
    def protocol_version(self) -> str:
        return self._protocol_version_str

    @property
    def datapoints(self) -> GimdowBLEDataPoints:
        """Get datapoints exposed by device."""
        return self._datapoints

    @property
    def status(self) -> dict[str, Any]:
        """Get current datapoints values."""

        result = {}
        dps = self.datapoints._datapoints
        if dps:
            order = [self.status_range, self.function]
            for functions in order:
                for dpcode in functions:
                    f = functions[dpcode]
                    dpid = f.dp_id
                    v = dps.get(dpid)
                    if v:
                        result[dpcode] = v.value
        return result

    def get_or_create_datapoint(
        self,
        id: int,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str | None = None,
    ) -> GimdowBLEDataPoint:
        """Get datapoints exposed by device."""
        return self._datapoints.get_or_create(id, type, value)

    async def send_control_datapoint(self, dp_id: int, value: Any) -> GimdowBLEDataPoint:
         """Send a control datapoint command (lock/unlock)."""
         datapoint = self.get_or_create_datapoint(
             dp_id,
             GimdowBLEDataPointType.DT_BOOL,
             value,
         )
         await datapoint.set_value(value)
         return datapoint

    def get_lock_state(self, state_dp_id: int) -> bool | None:
        """
        Get the current lock state.
        Returns True if Locked, False if Unlocked.
        Returns None if resolving unknown state or datapoint missing.
        """
        if self._is_resolving:
            return None

        datapoint = self._datapoints[state_dp_id]
        if datapoint:
            # Value True (1) -> Unlocked -> is_locked = False
            # Value False (0) -> Locked -> is_locked = True
            return not bool(datapoint.value)
        return None

    async def _send_control_datapoint_wait_for_echo(self, dp_id: int, value: Any, timeout: float = 10.0) -> bool:
        """Send a control datapoint and wait for the device to echo it back."""
        future_echo = asyncio.get_running_loop().create_future()
        
        def _echo_callback(datapoints: list[GimdowBLEDataPoint]):
                for dp in datapoints:
                    if dp.id == dp_id:
                        if not future_echo.done():
                            future_echo.set_result(True)
        
        remove_callback = self.register_callback(_echo_callback)
        try:
            await self.send_control_datapoint(dp_id, value)
            await asyncio.wait_for(future_echo, timeout=timeout)
            _LOGGER.debug(f"{self.address}: Control datapoint {dp_id} echoed by device.")
            return True
        except asyncio.TimeoutError:
                _LOGGER.warning(f"{self.address}: Timed out waiting for control datapoint {dp_id} echo. Disconnecting to force reconnect.")
                await self._execute_disconnect()
                return False
        except Exception as e:
                _LOGGER.error(f"{self.address}: Error waiting for control datapoint {dp_id} echo: {e}")
                return False
        finally:
                remove_callback()

    async def resolve_unknown_state(
        self,
        unlock_dp_id: int,
        unlock_value: Any,
        state_dp_id: int,
        lock_dp_id: int | None = None,
        lock_value: Any | None = None,
        target_lock: bool = False,
    ) -> None:
        """
        Handle unlocking sequence when state is unknown.
        Sequence: Unlock -> Wait for state update -> Unlock -> Lock (if target_lock)
        """
        if self._is_resolving:
             _LOGGER.debug(f"{self.address}: resolve_unknown_state already running. Ignoring duplicate request.")
             return

        self._is_resolving = True
        _LOGGER.debug(f"{self.address}: resolve_unknown_state started. Target: {'LOCK' if target_lock else 'UNLOCK'}")

        try:
            # 1. Send First Unlock
            # Wait for echo first to confirm receipt
            result = await self._send_control_datapoint_wait_for_echo(unlock_dp_id, unlock_value)
            if not result:
                 _LOGGER.warning(f"{self.address}: Missing echo for First Unlock. Aborting resolution sequence.")
                 return


            # 2. Wait for state to become Unlocked
            future = asyncio.get_running_loop().create_future()

            def _state_callback(datapoints: list[GimdowBLEDataPoint]):
                 for dp in datapoints:
                     if dp.id == state_dp_id:
                         # Unlocked means value is True (1)
                         if bool(dp.value): 
                             if not future.done():
                                 future.set_result(True)

            remove_callback = self.register_callback(_state_callback)
            
            try:
                # Check current value first
                current_dp = self._datapoints[state_dp_id]
                if current_dp and bool(current_dp.value):
                     _LOGGER.debug(f"{self.address}: State is already Unlocked. Future set immediately.")
                     future.set_result(True)
                
                await asyncio.wait_for(future, timeout=60)
                _LOGGER.debug(f"{self.address}: Unlocked state confirmed (Phase 1).")
            except asyncio.TimeoutError:
                 _LOGGER.warning(f"{self.address}: Timed out waiting for Unlocked state in Phase 1.")
            except Exception as e:
                 _LOGGER.error(f"{self.address}: Error waiting for state in Phase 1: {e}")
            finally:
                 remove_callback()

            # 3. Send Second Unlock
            # Wait for the device to echo the unlock command (confirmation)
            result = await self._send_control_datapoint_wait_for_echo(unlock_dp_id, unlock_value)
            if not result:
                  _LOGGER.warning(f"{self.address}: Proceeding despite missing echo for Second Unlock.")

            # Wait for mechanical cycle to complete
            _LOGGER.debug(f"{self.address}: Waiting for mechanical unlock cycle...")
            await asyncio.sleep(10)

            # 4. Lock if requested
            if target_lock and lock_dp_id is not None:
                 _LOGGER.debug(f"{self.address}: Sending Lock command.")
                 
                 # Prepare wait for Locked state
                 future_lock = asyncio.get_running_loop().create_future()

                 def _lock_state_callback(datapoints: list[GimdowBLEDataPoint]):
                      for dp in datapoints:
                          if dp.id == state_dp_id:
                              # Locked means value is False (0)
                              if not bool(dp.value):
                                  if not future_lock.done():
                                      future_lock.set_result(True)
                 
                 remove_lock_callback = self.register_callback(_lock_state_callback)

                 try:
                     await self.send_control_datapoint(lock_dp_id, lock_value)
                     
                     # We expect the state to change to Locked
                     await asyncio.wait_for(future_lock, timeout=75)
                     _LOGGER.debug(f"{self.address}: Locked state confirmed (Phase 2).")
                 except asyncio.TimeoutError:
                      _LOGGER.warning(f"{self.address}: Timed out waiting for Locked state in Phase 2.")
                 except Exception as e:
                      _LOGGER.error(f"{self.address}: Error waiting for state in Phase 2: {e}")
                 finally:
                      remove_lock_callback()

        finally:
            self._is_resolving = False
            _LOGGER.debug(f"{self.address}: resolve_unknown_state finished.")



    def _fire_connected_callbacks(self) -> None:
        """Fire the callbacks."""
        for callback in self._connected_callbacks:
            callback()

    def register_connected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when device disconnected."""

        def unregister_callback() -> None:
            self._connected_callbacks.remove(callback)

        self._connected_callbacks.append(callback)
        return unregister_callback

    def _fire_callbacks(self, datapoints: list[GimdowBLEDataPoint]) -> None:
        """Fire the callbacks."""
        for callback in self._callbacks:
            callback(datapoints)

    def register_callback(
        self,
        callback: Callable[[list[GimdowBLEDataPoint]], None],
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""

        def unregister_callback() -> None:
            self._callbacks.remove(callback)

        self._callbacks.append(callback)
        return unregister_callback

    def _fire_disconnected_callbacks(self) -> None:
        """Fire the callbacks."""
        for callback in self._disconnected_callbacks:
            callback()

    def register_disconnected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when device disconnected."""

        def unregister_callback() -> None:
            self._disconnected_callbacks.remove(callback)

        self._disconnected_callbacks.append(callback)
        return unregister_callback

    async def start(self):
        """Start the GimdowBLE."""
        _LOGGER.debug("%s: Starting...", self.address)
        # await self._send_packet()

    async def stop(self) -> None:
        """Stop the GimdowBLE."""
        _LOGGER.debug("%s: Stop", self.address)
        await self._execute_disconnect()

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Disconnected callback."""
        was_paired = self._is_paired
        self._is_paired = False
        self._fire_disconnected_callbacks()
        if self._expected_disconnect:
            _LOGGER.debug(
                "%s: Disconnected from device; RSSI: %s",
                self.address,
                self.rssi,
            )
            return
        self._client = None
        _LOGGER.debug(
            "%s: Device unexpectedly disconnected; RSSI: %s",
            self.address,
            self.rssi,
        )
        if was_paired:
            _LOGGER.debug(
                "%s: Scheduling reconnect; RSSI: %s",
                self.address,
                self.rssi,
            )
            asyncio.create_task(self._reconnect())

    def _disconnect(self) -> None:
        """Disconnect from device."""
        asyncio.create_task(self._execute_timed_disconnect())

    async def _execute_timed_disconnect(self) -> None:
        """Execute timed disconnection."""
        _LOGGER.debug(
            "%s: Disconnecting",
            self.address,
        )
        await self._execute_disconnect()

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        _LOGGER.debug(f"{self.address}: Executing disconnect.")
        async with self._connect_lock:
            client = self._client
            self._expected_disconnect = True
            self._client = None
            if client and client.is_connected:
                await client.stop_notify(CHARACTERISTIC_NOTIFY)
                await client.disconnect()
        async with self._seq_num_lock:
            self._current_seq_num = 1

    async def _ensure_connected(self) -> None:
        """Ensure connection to device is established."""
        global global_connect_lock
        if self._expected_disconnect:
            return
        if self._connect_lock.locked():
            _LOGGER.debug(
                "%s: Connection already in progress,"
                " waiting for it to complete; RSSI: %s",
                self.address,
                self.rssi,
            )
        if self._client and self._client.is_connected and self._is_paired:
            return
        async with self._connect_lock:
            # Check again while holding the lock
            await asyncio.sleep(0.01)
            if self._client and self._client.is_connected and self._is_paired:
                return

            try:
                async with global_connect_lock:
                    _LOGGER.debug(
                        "%s: Connecting; RSSI: %s", self.address, self.rssi
                    )
                    client = await establish_connection(
                        BleakClientWithServiceCache,
                        self._ble_device,
                        self.address,
                        self._disconnected,
                        use_services_cache=True,
                        ble_device_callback=lambda: self._ble_device,
                    )
            except BLEAK_EXCEPTIONS as ex:
                _LOGGER.error(
                    "%s: communication failed: %s", self.address, ex
                )
                return # Let the loop/caller handle retry if needed, or just fail this attempt
            except Exception as ex:
                _LOGGER.error("%s: unexpected error: %s",
                                self.address, ex, exc_info=True)
                return

            if client and client.is_connected:
                _LOGGER.debug("%s: Connected; RSSI: %s",
                                self.address, self.rssi)
                self._client = client
                try:
                    await self._client.start_notify(
                        CHARACTERISTIC_NOTIFY, self._notification_handler
                    )
                except Exception as ex:
                    self._client = None
                    _LOGGER.error("%s: starting notifications failed: %s",
                                    self.address, ex, exc_info=True)
                    await client.disconnect() 
                    return
            else:
                 _LOGGER.debug("%s: Failed to connect", self.address)
                 return

            # Connection established, now perform handshake
            if self._client and self._client.is_connected:
                _LOGGER.debug(
                    "%s: Sending device info request", self.address)
                try:
                    if not await self._send_packet_while_connected(
                        GimdowBLECode.FUN_SENDER_DEVICE_INFO,
                        bytes(0),
                        0,
                        True,
                    ):
                        self._client = None
                        _LOGGER.error(
                            "%s: Sending device info request failed",
                            self.address,
                        )
                        await client.disconnect()
                        return
                except Exception as ex:  
                    self._client = None
                    _LOGGER.error("%s: Sending device info request failed: %s",
                                    self.address, ex, exc_info=True)
                    await client.disconnect()
                    return

            if self._client and self._client.is_connected:
                _LOGGER.debug("%s: Sending pairing request", self.address)
                try:
                    if not await self._send_packet_while_connected(
                        GimdowBLECode.FUN_SENDER_PAIR,
                        self._build_pairing_request(),
                        0,
                        True,
                    ):
                        self._client = None
                        _LOGGER.error(
                            "%s: Sending pairing request failed",
                            self.address,
                        )
                        await client.disconnect()
                        return
                except Exception as ex:
                    self._client = None
                    _LOGGER.error("%s: Sending pairing request failed: %s",
                                    self.address, ex, exc_info=True)
                    await client.disconnect()
                    return

            if self._client:
                if self._client.is_connected:
                    if self._is_paired:
                        _LOGGER.debug("%s: Successfully connected", self.address)
                        self._fire_connected_callbacks()
                    else:
                        _LOGGER.error("%s: Connected but not paired", self.address)
                        # Optionally disconnect here if pairing failed?
                else:
                    _LOGGER.error("%s: Not connected after handshake attempts", self.address)
            else:
                _LOGGER.error("%s: No client device after handshake attempts", self.address)

    async def _reconnect(self) -> None:
        """Attempt a reconnect"""
        _LOGGER.debug("%s: Reconnect, ensuring connection", self.address)
        async with self._seq_num_lock:
            self._current_seq_num = 1
        try:
            if self._expected_disconnect:
                return
            await self._ensure_connected()
            if self._expected_disconnect:
                return
            if not self._client or not self._client.is_connected:
                raise BleakError("Failed to ensure connection")
            _LOGGER.debug("%s: Reconnect, connection ensured", self.address)
        except BLEAK_EXCEPTIONS:  # BleakNotFoundError:
            _LOGGER.debug(
                "%s: Reconnect, failed to ensure connection - backing off",
                self.address,
                exc_info=True,
            )
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug("%s: Reconnecting again", self.address)
            asyncio.create_task(self._reconnect())

    @staticmethod
    def _calc_crc16(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte & 255
            for _ in range(8):
                tmp = crc & 1
                crc >>= 1
                if tmp != 0:
                    crc ^= 0xA001
        return crc

    @staticmethod
    def _pack_int(value: int) -> bytearray:
        curr_byte: int
        result = bytearray()
        while True:
            curr_byte = value & 0x7F
            value >>= 7
            if value != 0:
                curr_byte |= 0x80
            result += pack(">B", curr_byte)
            if value == 0:
                break
        return result

    @staticmethod
    def _unpack_int(data: bytes, start_pos: int) -> tuple(int, int):
        result: int = 0
        offset: int = 0
        while offset < 5:
            pos: int = start_pos + offset
            if pos >= len(data):
                raise GimdowBLEDataFormatError()
            curr_byte: int = data[pos]
            result |= (curr_byte & 0x7F) << (offset * 7)
            offset += 1
            if (curr_byte & 0x80) == 0:
                break
        if offset > 4:
            raise GimdowBLEDataFormatError()
        else:
            return (result, start_pos + offset)

    def _build_packets(
        self,
        seq_num: int,
        code: GimdowBLECode,
        data: bytes,
        response_to: int = 0,
    ) -> list[bytes]:
        key: bytes
        iv = secrets.token_bytes(16)
        security_flag: bytes
        if code == GimdowBLECode.FUN_SENDER_DEVICE_INFO:
            key = self._login_key
            security_flag = b"\x04"
        else:
            key = self._session_key
            security_flag = b"\x05"

        raw = bytearray()
        raw += pack(">IIHH", seq_num, response_to, code.value, len(data))
        raw += data
        crc = self._calc_crc16(raw)
        raw += pack(">H", crc)
        while len(raw) % 16 != 0:
            raw += b"\x00"

        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = security_flag + iv + cipher.encrypt(raw)

        command = []
        packet_num = 0
        pos = 0
        length = len(encrypted)
        while pos < length:
            packet = bytearray()
            packet += self._pack_int(packet_num)

            if packet_num == 0:
                packet += self._pack_int(length)
                packet += pack(">B", self._protocol_version << 4)

            data_part = encrypted[
                pos:pos + GATT_MTU - len(packet)  # fmt: skip
            ]
            packet += data_part
            command.append(packet)

            pos += len(data_part)
            packet_num += 1

        return command

    async def _get_seq_num(self) -> int:
        async with self._seq_num_lock:
            result = self._current_seq_num
            self._current_seq_num += 1
        return result

    async def _send_packet(
        self,
        code: GimdowBLECode,
        data: bytes,
        wait_for_response: bool = True,
        # retry: int | None = None,
    ) -> None:
        """Send packet to device and optional read response."""
        if self._expected_disconnect:
            return
        await self._ensure_connected()
        if self._expected_disconnect:
            return
        if not (self._client and self._client.is_connected):
            _LOGGER.debug("%s: Not connected, skipping send packet", self.address)
            return
        await self._send_packet_while_connected(code, data, 0, wait_for_response)

    async def _send_response(
        self,
        code: GimdowBLECode,
        data: bytes,
        response_to: int,
    ) -> None:
        """Send response to received packet."""
        if self._client and self._client.is_connected:
            await self._send_packet_while_connected(code, data, response_to, False)

    async def _send_packet_while_connected(
        self,
        code: GimdowBLECode,
        data: bytes,
        response_to: int,
        wait_for_response: bool,
        # retry: int | None = None
    ) -> bool:
        """Send packet to device and optional read response."""
        result = True
        future: asyncio.Future | None = None
        seq_num = await self._get_seq_num()
        if wait_for_response:
            future = asyncio.Future()
            self._input_expected_responses[seq_num] = future

        if response_to > 0:
            _LOGGER.debug(
                "%s: Sending packet: #%s %s in response to #%s",
                self.address,
                seq_num,
                code.name,
                response_to,
            )
        else:
            _LOGGER.debug(
                "%s: Sending packet: #%s %s",
                self.address,
                seq_num,
                code.name,
            )
        packets: list[bytes] = self._build_packets(
            seq_num, code, data, response_to)
        await self._int_send_packet_while_connected(packets)
        if future:
            try:
                await asyncio.wait_for(future, RESPONSE_WAIT_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "%s: timeout receiving response, RSSI: %s",
                    self.address,
                    self.rssi,
                )
                result = False
            self._input_expected_responses.pop(seq_num, None)

        return result

    async def _int_send_packet_while_connected(
        self,
        packets: list[bytes],
    ) -> None:
        if self._operation_lock.locked():
            _LOGGER.debug(
                "%s: Operation already in progress, "
                "waiting for it to complete; RSSI: %s",
                self.address,
                self.rssi,
            )
        async with self._operation_lock:
            try:
                await self._send_packets_locked(packets)
            except BleakNotFoundError:
                _LOGGER.error(
                    "%s: device not found, no longer in range, or poor RSSI: %s",
                    self.address,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except BLEAK_EXCEPTIONS:
                _LOGGER.error(
                    "%s: communication failed",
                    self.address,
                    exc_info=True,
                )
                raise

    async def _resend_packets(self, packets: list[bytes]) -> None:
        if self._expected_disconnect:
            return
        await self._ensure_connected()
        if self._expected_disconnect:
            return
        await self._int_send_packet_while_connected(packets)

    async def _send_packets_locked(self, packets: list[bytes]) -> None:
        """Send command to device and read response."""
        try:
            await self._int_send_packets_locked(packets)
        except BleakDBusError as ex:
            # Disconnect so we can reset state and try again
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug(
                "%s: RSSI: %s; Backing off %ss; Disconnecting due to error: %s",
                self.address,
                self.rssi,
                BLEAK_BACKOFF_TIME,
                ex,
            )
            if self._is_paired:
                asyncio.create_task(self._resend_packets(packets))
            else:
                asyncio.create_task(self._reconnect())
            raise BleakError from ex
        except BleakError as ex:
            # Disconnect so we can reset state and try again
            _LOGGER.debug(
                "%s: RSSI: %s; Disconnecting due to error: %s",
                self.address,
                self.rssi,
                ex,
            )
            if self._is_paired:
                asyncio.create_task(self._resend_packets(packets))
            else:
                asyncio.create_task(self._reconnect())
            raise

    async def _int_send_packets_locked(self, packets: list[bytes]) -> None:
        """Execute command and read response."""
        for packet in packets:
            if self._client:
                try:
                    # _LOGGER.debug("%s: Sending packet: %s", self.address, packet.hex())
                    await self._client.write_gatt_char(
                        CHARACTERISTIC_WRITE,
                        packet,
                        False,
                    )
                except:
                    _LOGGER.error(
                        "%s: Error during sending packet",
                        self.address,
                        exc_info=True,
                    )
                    if self._client and self._client.is_connected:
                        self._disconnected(self._client)
                    raise BleakError()
            else:
                _LOGGER.error(
                    "%s: Client disconnected during sending packet",
                    self.address,
                    exc_info=True,
                )
                raise BleakError()

    def _get_key(self, security_flag: int) -> bytes:
        if security_flag == 1:
            return self._auth_key
        if security_flag == 4:
            return self._login_key
        elif security_flag == 5:
            return self._session_key
        else:
            pass

    def _parse_timestamp(self, data: bytes, start_pos: int) -> tuple(float, int):
        timestamp: float
        pos = start_pos
        if pos >= len(data):
            raise GimdowBLEDataLengthError()
        time_type = data[pos]
        pos += 1
        end_pos = pos
        match time_type:
            case 0:
                end_pos += 13
                if end_pos > len(data):
                    raise GimdowBLEDataLengthError()
                timestamp = int(data[pos:end_pos].decode()) / 1000
                pass
            case 1:
                end_pos += 4
                if end_pos > len(data):
                    raise GimdowBLEDataLengthError()
                timestamp = int.from_bytes(data[pos:end_pos], "big") * 1.0
                pass
            case _:
                raise GimdowBLEDataFormatError()

        _LOGGER.debug(
            "%s: Received timestamp: %s",
            self.address,
            time.ctime(timestamp),
        )
        return (timestamp, end_pos)

    def _parse_datapoints_v3(
        self, timestamp: float, flags: int, data: bytes, start_pos: int
    ) -> int:
        datapoints: list[GimdowBLEDataPoint] = []

        pos = start_pos
        while len(data) - pos >= 4:
            id: int = data[pos]
            pos += 1
            _type: int = data[pos]
            if _type > GimdowBLEDataPointType.DT_BITMAP.value:
                raise GimdowBLEDataFormatError()
            type: GimdowBLEDataPointType = GimdowBLEDataPointType(_type)
            pos += 1
            data_len: int = data[pos]
            pos += 1
            next_pos = pos + data_len
            if next_pos > len(data):
                raise GimdowBLEDataLengthError()
            raw_value = data[pos:next_pos]
            match type:
                case (GimdowBLEDataPointType.DT_RAW | GimdowBLEDataPointType.DT_BITMAP):
                    value = raw_value
                case GimdowBLEDataPointType.DT_BOOL:
                    value = int.from_bytes(raw_value, "big") != 0
                case (GimdowBLEDataPointType.DT_VALUE | GimdowBLEDataPointType.DT_ENUM):
                    value = int.from_bytes(raw_value, "big", signed=True)
                case GimdowBLEDataPointType.DT_STRING:
                    value = raw_value.decode()

            _LOGGER.debug(
                "%s: Received datapoint update, id: %s, type: %s: value: %s",
                self.address,
                id,
                type.name,
                value,
            )
            self._datapoints._update_from_device(
                id, timestamp, flags, type, value)
            datapoints.append(self._datapoints[id])
            pos = next_pos

        self._fire_callbacks(datapoints)

    def _handle_command_or_response(
        self, seq_num: int, response_to: int, code: GimdowBLECode, data: bytes
    ) -> None:
        result: int = 0
        
        _LOGGER.debug(
            "%s: Handling command/response code=%s data_len=%s", 
            self.address, code.name, len(data)
        )

        match code:
            case GimdowBLECode.FUN_SENDER_DEVICE_INFO:
                if len(data) < 46:
                    raise GimdowBLEDataLengthError()

                self._device_version = ("%s.%s") % (data[0], data[1])
                self._protocol_version_str = ("%s.%s") % (data[2], data[3])
                self._hardware_version = ("%s.%s") % (data[12], data[13])

                self._protocol_version = data[2]
                self._flags = data[4]
                self._is_bound = data[5] != 0

                srand = data[6:12]
                self._session_key = hashlib.md5(
                    self._local_key + srand).digest()
                self._auth_key = data[14:46]
                _LOGGER.info(
                    "%s: Device Info received. Session Key derived successfully.", 
                    self.address
                )

            case GimdowBLECode.FUN_SENDER_PAIR:
                if len(data) != 1:
                    raise GimdowBLEDataLengthError()
                result = data[0]
                if result == 2:
                    _LOGGER.debug(
                        "%s: Device is already paired",
                        self.address,
                    )
                    result = 0
                self._is_paired = result == 0
                _LOGGER.info("%s: Pairing Result: %s (Paired=%s)", self.address, result, self._is_paired)

            case GimdowBLECode.FUN_SENDER_DEVICE_STATUS:
                if len(data) != 1:
                    raise GimdowBLEDataLengthError()
                result = data[0]

            case GimdowBLECode.FUN_RECEIVE_TIME1_REQ:
                if len(data) != 0:
                    raise GimdowBLEDataLengthError()

                timestamp = int(time.time_ns() / 1000000)
                timezone = -int(time.timezone / 36)
                data = str(timestamp).encode() + pack(">h", timezone)
                asyncio.create_task(self._send_response(code, data, seq_num))

            case GimdowBLECode.FUN_RECEIVE_TIME2_REQ:
                if len(data) != 0:
                    raise GimdowBLEDataLengthError()

                time_str: time.struct_time = time.localtime()
                timezone = -int(time.timezone / 36)
                data = pack(
                    ">BBBBBBBh",
                    time_str.tm_year % 100,
                    time_str.tm_mon,
                    time_str.tm_mday,
                    time_str.tm_hour,
                    time_str.tm_min,
                    time_str.tm_sec,
                    time_str.tm_wday,
                    timezone,
                )
                asyncio.create_task(self._send_response(code, data, seq_num))

            case GimdowBLECode.FUN_RECEIVE_DP:
                self._parse_datapoints_v3(time.time(), 0, data, 0)
                asyncio.create_task(
                    self._send_response(code, bytes(0), seq_num))

            case GimdowBLECode.FUN_RECEIVE_SIGN_DP:
                dp_seq_num = int.from_bytes(data[:2], "big")
                flags = data[2]
                self._parse_datapoints_v3(time.time(), flags, data, 2)
                data = pack(">HBB", dp_seq_num, flags, 0)
                asyncio.create_task(self._send_response(code, data, seq_num))

            case GimdowBLECode.FUN_RECEIVE_TIME_DP:
                timestamp: float
                pos: int
                timestamp, pos = self._parse_timestamp(data, 0)
                self._parse_datapoints_v3(timestamp, 0, data, pos)
                asyncio.create_task(
                    self._send_response(code, bytes(0), seq_num))

            case GimdowBLECode.FUN_RECEIVE_SIGN_TIME_DP:
                timestamp: float
                pos: int
                dp_seq_num = int.from_bytes(data[:2], "big")
                flags = data[2]
                timestamp, pos = self._parse_timestamp(data, 3)
                self._parse_datapoints_v3(time.time(), flags, data, pos)
                data = pack(">HBB", dp_seq_num, flags, 0)
                asyncio.create_task(self._send_response(code, data, seq_num))

        if response_to != 0:
            future = self._input_expected_responses.pop(response_to, None)
            if future:
                _LOGGER.debug(
                    "%s: Received expected response to #%s, result: %s",
                    self.address,
                    response_to,
                    result,
                )
                if result == 0:
                    future.set_result(result)
                else:
                    future.set_exception(GimdowBLEDeviceError(result))

    def _clean_input(self) -> None:
        self._input_buffer = None
        self._input_expected_packet_num = 0
        self._input_expected_length = 0

    def _parse_input(self) -> None:
        security_flag = self._input_buffer[0]
        key = self._get_key(security_flag)
        iv = self._input_buffer[1:17]
        encrypted = self._input_buffer[17:]

        self._clean_input()

        cipher = AES.new(key, AES.MODE_CBC, iv)
        raw = cipher.decrypt(encrypted)

        seq_num: int
        response_to: int
        _code: int
        length: int
        seq_num, response_to, _code, length = unpack(">IIHH", raw[:12])

        data_end_pos = length + 12
        raw_length = len(raw)
        if raw_length < data_end_pos:
            raise GimdowBLEDataLengthError()
        if raw_length > data_end_pos:
            calc_crc = self._calc_crc16(raw[:data_end_pos])
            (data_crc,) = unpack(
                ">H",
                raw[data_end_pos:data_end_pos + 2]  # fmt: skip
            )
            if calc_crc != data_crc:
                raise GimdowBLEDataCRCError()
        data = raw[12:data_end_pos]

        code: GimdowBLECode
        try:
            code = GimdowBLECode(_code)
        except ValueError:
            _LOGGER.debug(
                "%s: Received unknown message: #%s %x, response to #%s, data %s",
                self.address,
                seq_num,
                _code,
                response_to,
                data.hex(),
            )
            return

        if response_to != 0:
            _LOGGER.debug(
                "%s: Received: #%s %s, response to #%s",
                self.address,
                seq_num,
                code.name,
                response_to,
            )
        else:
            _LOGGER.debug(
                "%s: Received: #%s %s",
                self.address,
                seq_num,
                code.name,
            )

        self._handle_command_or_response(seq_num, response_to, code, data)

    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle notification responses."""
        _LOGGER.debug("%s: Packet received: %s", self.address, data.hex())

        pos: int = 0
        packet_num: int

        packet_num, pos = self._unpack_int(data, pos)

        if packet_num < self._input_expected_packet_num:
            if packet_num == 0:
                _LOGGER.debug(
                    "%s: Received packet 0 while expecting %s, resetting input buffer",
                    self.address,
                    self._input_expected_packet_num,
                )
                self._clean_input()
            else:
                _LOGGER.error(
                    "%s: Unexpcted packet (number %s) in notifications, " "expected %s",
                    self.address,
                    packet_num,
                    self._input_expected_packet_num,
                )
                self._clean_input()

        if packet_num == self._input_expected_packet_num:
            if packet_num == 0:
                self._input_buffer = bytearray()
                self._input_expected_length, pos = self._unpack_int(data, pos)
                pos += 1
            self._input_buffer += data[pos:]
            self._input_expected_packet_num += 1
        else:
            _LOGGER.error(
                "%s: Missing packet (number %s) in notifications, received %s",
                self.address,
                self._input_expected_packet_num,
                packet_num,
            )
            self._clean_input()
            return

        if len(self._input_buffer) > self._input_expected_length:
            _LOGGER.error(
                "%s: Unexpcted length of data in notifications, "
                "received %s expected %s",
                self.address,
                len(self._input_buffer),
                self._input_expected_length,
            )
            self._clean_input()
            return
        elif len(self._input_buffer) == self._input_expected_length:
            self._parse_input()

    async def _send_datapoints_v3(self, datapoint_ids: list[int]) -> None:
        """Send new values of datapoints to the device."""
        data = bytearray()
        for dp_id in datapoint_ids:
            dp = self._datapoints[dp_id]
            value = dp._get_value()
            _LOGGER.debug(
                "%s: Sending datapoint update, id: %s, type: %s: value: %s",
                self.address,
                dp.id,
                dp.type.name,
                dp.value,
            )
            data += pack(">BBB", dp.id, int(dp.type.value), len(value))
            data += value

        await self._send_packet(GimdowBLECode.FUN_SENDER_DPS, data)

    async def _send_datapoints(self, datapoint_ids: list[int]) -> None:
        """Send new values of datapoints to the device."""
        if self._protocol_version in (2, 3):
            await self._send_datapoints_v3(datapoint_ids)
        else:
            raise GimdowBLEDeviceError(0)
