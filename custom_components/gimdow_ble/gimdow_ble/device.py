"""Gimdow BLE device — thin orchestrator combining protocol and connection mixins.

``GimdowBLEDevice`` inherits from :class:`GimdowBLEConnection` and
:class:`GimdowBLEProtocol`. Its responsibility is device-specific concerns:
credentials, advertisement decoding, function/status mappings, and the
diagnostic snapshot helper.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .connection import GimdowBLEConnection
from .const import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    GimdowBLEDataPointType,
)
from .datapoints import (
    GimdowBLEDataPoint,
    GimdowBLEDataPoints,
    GimdowBLEDeviceFunction,
    GimdowBLEEntityDescription,
)
from .manager import AbstractGimdowBLEDeviceManager, GimdowBLEDeviceCredentials
from .protocol import GimdowBLEProtocol

_LOGGER = logging.getLogger(__name__)


class GimdowBLEDevice(GimdowBLEConnection, GimdowBLEProtocol):
    """Gimdow BLE device — manages credentials, state, and protocol/connection delegation."""

    def __init__(
        self,
        device_manager: AbstractGimdowBLEDeviceManager,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData | None = None,
    ) -> None:
        # --- Device identity (must be set before _init_connection checks address) ---
        self._device_manager = device_manager
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data

        # --- Mixin initialisers ---
        self._init_connection()
        self._init_protocol()
        self._device_info: GimdowBLEDeviceCredentials | None = None

        # --- Protocol keys (set by _update_device_info / _handle_command_or_response) ---
        self._local_key: bytes | None = None

        # --- Firmware / protocol metadata ---
        self._is_bound: bool = False
        self._flags: int = 0
        self._protocol_version: int = 2
        self._device_version: str = ""
        self._protocol_version_str: str = ""
        self._hardware_version: str = ""

        # --- Datapoints collection — sends via _send_datapoints ---
        self._datapoints = GimdowBLEDataPoints(send_callback=self._send_datapoints)

        # --- Cloud schema ---
        self._function: dict = {}
        self._status_range: dict = {}

    # ------------------------------------------------------------------
    # Identity & advertisement
    # ------------------------------------------------------------------

    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
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
        if len(result) > 44:
            _LOGGER.error(
                "%s: Pairing request payload too long (%d bytes > 44) — truncating",
                self.address,
                len(result),
            )
            result = result[:44]
        else:
            result += b"\x00" * (44 - len(result))
        return bytes(result)

    async def pair(self) -> None:
        from .const import GimdowBLECode

        await self._send_packet(
            GimdowBLECode.FUN_SENDER_PAIR, self._build_pairing_request()
        )

    async def update(self) -> None:
        from .const import GimdowBLECode

        _LOGGER.debug("%s: Requesting status update", self.address)
        await self._send_packet(GimdowBLECode.FUN_SENDER_DEVICE_STATUS, bytes())

    def schedule_update(self, *, name: str = "scheduled-update") -> None:
        """Schedule a non-blocking status update via the event loop."""
        self._create_safe_task(self.update(), name=name)

    async def _update_device_info(self) -> bool:
        if self._device_info is None:
            if self._device_manager:
                self._device_info = await self._device_manager.get_device_credentials(
                    self._ble_device.address, False
                )
            if self._device_info:
                self._local_key = self._device_info.local_key[:6].encode()
                self._login_key = hashlib.md5(self._local_key).digest()
                self.append_functions(
                    self._device_info.functions, self._device_info.status_range
                )
        return self._device_info is not None

    def append_functions(self, function: list[dict], status_range: list[dict]) -> None:
        if function:
            for f in function:
                if code := f.get("code"):
                    self._function[code] = GimdowBLEDeviceFunction(**f)
        if status_range:
            for f in status_range:
                if code := f.get("code"):
                    self._status_range[code] = GimdowBLEDeviceFunction(**f)

    def update_description(
        self, description: GimdowBLEEntityDescription | None
    ) -> None:
        if not description:
            return
        self.append_functions(
            description.function or [], description.status_range or []
        )
        if description.values_overrides:
            for key, values in description.values_overrides.items():
                if f := self._function.get(key):
                    f.values = values
                if f := self._status_range.get(key):
                    f.values = values
        if description.values_defaults:
            for key, values in description.values_defaults.items():
                if (f := self._function.get(key)) and not f.values:
                    f.values = values
                if (f := self._status_range.get(key)) and not f.values:
                    f.values = values

    def _decode_advertisement_data(self) -> None:
        raw_product_id: bytes | None = None
        if not self._advertisement_data:
            return
        if self._advertisement_data.service_data:
            service_data = self._advertisement_data.service_data.get(SERVICE_UUID)
            if service_data and len(service_data) > 1 and service_data[0] == 0:
                raw_product_id = service_data[1:]
        if self._advertisement_data.manufacturer_data:
            manufacturer_data = self._advertisement_data.manufacturer_data.get(
                MANUFACTURER_DATA_ID
            )
            if manufacturer_data and len(manufacturer_data) > 6:
                self._is_bound = (manufacturer_data[0] & 0x80) != 0
                self._protocol_version = manufacturer_data[1]

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def name(self) -> str:
        if self._device_info:
            return self._device_info.device_name
        return self._ble_device.name or self._ble_device.address

    @property
    def rssi(self) -> int | None:
        if self._advertisement_data:
            return self._advertisement_data.rssi
        return None

    @property
    def uuid(self) -> str:
        return self._device_info.uuid if self._device_info else ""

    @property
    def local_key(self) -> str:
        return self._device_info.local_key if self._device_info else ""

    @property
    def category(self) -> str:
        return self._device_info.category if self._device_info else ""

    @property
    def device_id(self) -> str:
        return self._device_info.device_id if self._device_info else ""

    @property
    def product_id(self) -> str:
        return self._device_info.product_id if self._device_info else ""

    @property
    def product_model(self) -> str:
        return self._device_info.product_model if self._device_info else ""

    @property
    def product_name(self) -> str:
        return self._device_info.product_name if self._device_info else ""

    @property
    def function(self) -> dict:
        return self._function

    @property
    def status_range(self) -> dict:
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
        return self._datapoints

    @property
    def is_paired(self) -> bool:
        return self._is_paired

    @property
    def status(self) -> dict[str, Any]:
        result = {}
        for functions in (self._status_range, self._function):
            for dpcode, f in functions.items():
                v = self._datapoints[f.dp_id]
                if v:
                    result[dpcode] = v.value
        return result

    # ------------------------------------------------------------------
    # Datapoint helpers
    # ------------------------------------------------------------------

    def get_or_create_datapoint(
        self, id: int, type: GimdowBLEDataPointType, value: Any = None
    ) -> GimdowBLEDataPoint:
        return self._datapoints.get_or_create(id, type, value)

    async def send_control_datapoint(
        self, dp_id: int, value: Any
    ) -> GimdowBLEDataPoint:
        """Send a control datapoint (e.g. lock/unlock command)."""
        datapoint = self.get_or_create_datapoint(
            dp_id, GimdowBLEDataPointType.DT_BOOL, value
        )
        await datapoint.set_value(value)
        return datapoint

    async def send_command_wait_state_echo(
        self,
        cmd_dp_id: int,
        value: Any,
        state_dp_id: int,
        timeout: float = 25.0,
    ) -> bool:
        """Send a control DP and wait for any notification on state_dp_id.

        Returns True if state_dp_id pushed back within timeout, False on timeout.
        Timeout is not failure — the device may already be in the target state.
        """
        future = asyncio.get_running_loop().create_future()

        def _cb(datapoints: list[GimdowBLEDataPoint]) -> None:
            for dp in datapoints:
                if dp.id == state_dp_id and not future.done():
                    future.set_result(True)

        remove_cb = self.register_callback(_cb)
        try:
            await self.send_control_datapoint(cmd_dp_id, value)
            await asyncio.wait_for(future, timeout=timeout)
            return True
        except asyncio.TimeoutError:
            _LOGGER.debug(
                "%s: send_command_wait_state_echo — no DP%s echo in %ss.",
                self.address,
                state_dp_id,
                timeout,
            )
            return False
        except Exception:
            return False
        finally:
            remove_cb()

    def get_lock_state(self, state_dp_id: int) -> bool | None:
        """Return True=Locked, False=Unlocked, None=Unknown."""
        dp = self._datapoints[state_dp_id]
        if dp:
            return not bool(dp.value)  # DP True = Unlocked → is_locked = False
        return None
