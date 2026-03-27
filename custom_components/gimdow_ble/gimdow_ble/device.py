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
from Crypto.Cipher import AES

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
from .diagnostics import GimdowBLEDiagContext
from .exceptions import (
    GimdowBLEEchoTimeoutError,
    GimdowBLEResolutionAbortedError,
    GimdowBLEStateTimeoutError,
)
from .manager import AbstaractGimdowBLEDeviceManager, GimdowBLEDeviceCredentials
from .protocol import GimdowBLEProtocol

_LOGGER = logging.getLogger(__name__)


class GimdowBLEDevice(GimdowBLEConnection, GimdowBLEProtocol):
    """Gimdow BLE device — manages credentials, state, and protocol/connection delegation."""

    def __init__(
        self,
        device_manager: AbstaractGimdowBLEDeviceManager,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData | None = None,
    ) -> None:
        # --- Mixin initialisers ---
        self._init_connection()
        self._init_protocol()

        # --- Device identity ---
        self._device_manager = device_manager
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
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

        # --- Lock resolution flag (used by get_lock_state) ---
        self._is_resolving: bool = False

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
        for _ in range(44 - len(result)):
            result += b"\x00"
        return result

    async def pair(self) -> None:
        from .const import GimdowBLECode
        await self._send_packet(GimdowBLECode.FUN_SENDER_PAIR, self._build_pairing_request())

    async def update(self) -> None:
        from .const import GimdowBLECode
        _LOGGER.debug("%s: Requesting status update", self.address)
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

    def update_description(self, description: GimdowBLEEntityDescription | None) -> None:
        if not description:
            return
        self.append_functions(description.function or [], description.status_range or [])
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
            manufacturer_data = self._advertisement_data.manufacturer_data.get(MANUFACTURER_DATA_ID)
            if manufacturer_data and len(manufacturer_data) > 6:
                self._is_bound = (manufacturer_data[0] & 0x80) != 0
                self._protocol_version = manufacturer_data[1]
                raw_uuid = manufacturer_data[6:]
                if raw_product_id:
                    key = hashlib.md5(raw_product_id).digest()
                    cipher = AES.new(key, AES.MODE_CBC, key)
                    raw_uuid = cipher.decrypt(raw_uuid)
                    self._uuid = raw_uuid.decode("utf-8")

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
    def is_resolving(self) -> bool:
        return self._is_resolving

    @property
    def status(self) -> dict[str, Any]:
        result = {}
        dps = self._datapoints._datapoints
        for functions in (self._status_range, self._function):
            for dpcode, f in functions.items():
                v = dps.get(f.dp_id)
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

    async def send_control_datapoint(self, dp_id: int, value: Any) -> GimdowBLEDataPoint:
        """Send a control datapoint (e.g. lock/unlock command)."""
        datapoint = self.get_or_create_datapoint(dp_id, GimdowBLEDataPointType.DT_BOOL, value)
        await datapoint.set_value(value)
        return datapoint

    def get_lock_state(self, state_dp_id: int) -> bool | None:
        """Return True=Locked, False=Unlocked, None=Unknown."""
        if self._is_resolving:
            return None
        dp = self._datapoints[state_dp_id]
        if dp:
            return not bool(dp.value)  # DP True = Unlocked → is_locked = False
        return None

    # ------------------------------------------------------------------
    # Lock resolution (lives on device — requires BLE access)
    # ------------------------------------------------------------------

    async def _send_control_datapoint_wait_for_echo(
        self, dp_id: int, value: Any, timeout: float = 10.0
    ) -> bool:
        """Send a control DP and block until the device echoes it back."""
        future_echo = asyncio.get_running_loop().create_future()

        def _echo_cb(datapoints: list[GimdowBLEDataPoint]) -> None:
            for dp in datapoints:
                if dp.id == dp_id and not future_echo.done():
                    future_echo.set_result(True)

        remove_cb = self.register_callback(_echo_cb)
        try:
            await self.send_control_datapoint(dp_id, value)
            await asyncio.wait_for(future_echo, timeout=timeout)
            _LOGGER.debug("%s: DP %s echoed.", self.address, dp_id)
            return True
        except asyncio.TimeoutError:
            err = GimdowBLEEchoTimeoutError(dp_id=dp_id, timeout=timeout)
            ctx = self._diag_context("_send_control_datapoint_wait_for_echo", error=str(err), dp_id=dp_id)
            ctx.log(_LOGGER, logging.WARNING)
            await self._execute_disconnect()
            return False
        except Exception as e:
            ctx = self._diag_context("_send_control_datapoint_wait_for_echo", error=str(e), dp_id=dp_id)
            ctx.log(_LOGGER)
            return False
        finally:
            remove_cb()

    async def resolve_unknown_state(
        self,
        unlock_dp_id: int,
        unlock_value: Any,
        state_dp_id: int,
        lock_dp_id: int | None = None,
        lock_value: Any | None = None,
        target_lock: bool = False,
    ) -> None:
        """5-phase brute-force lock state resolution cycle."""
        if self._is_resolving:
            _LOGGER.debug("%s: resolve_unknown_state already running. Ignoring.", self.address)
            return

        self._is_resolving = True
        target_label = "LOCK" if target_lock else "UNLOCK"
        _LOGGER.warning("%s: resolve_unknown_state started. target=%s", self.address, target_label)

        try:
            # Phase 1 — first unlock + echo
            _LOGGER.debug("%s: [Ph1] Sending first unlock (DP %s).", self.address, unlock_dp_id)
            if not await self._send_control_datapoint_wait_for_echo(unlock_dp_id, unlock_value):
                err = GimdowBLEResolutionAbortedError("No echo for Phase 1 unlock")
                ctx = self._diag_context("resolve/phase1", error=str(err), target=target_label)
                ctx.log(_LOGGER, logging.WARNING)
                return

            # Phase 2 — wait for Unlocked state
            _LOGGER.debug("%s: [Ph2] Waiting for DP %s Unlocked.", self.address, state_dp_id)
            future_unlock = asyncio.get_running_loop().create_future()

            def _state_cb(dps: list[GimdowBLEDataPoint]) -> None:
                for dp in dps:
                    if dp.id == state_dp_id and bool(dp.value) and not future_unlock.done():
                        future_unlock.set_result(True)

            remove_state_cb = self.register_callback(_state_cb)
            try:
                current = self._datapoints[state_dp_id]
                if current and bool(current.value):
                    future_unlock.set_result(True)
                await asyncio.wait_for(future_unlock, timeout=60)
                _LOGGER.debug("%s: [Ph2] Unlocked confirmed.", self.address)
            except asyncio.TimeoutError:
                err = GimdowBLEStateTimeoutError("Unlocked", 60)
                ctx = self._diag_context("resolve/phase2", error=str(err), target=target_label)
                ctx.log(_LOGGER, logging.WARNING)
            except Exception as e:
                ctx = self._diag_context("resolve/phase2", error=str(e), target=target_label)
                ctx.log(_LOGGER)
            finally:
                remove_state_cb()

            # Phase 3 — second unlock + echo (mechanical confirmation)
            _LOGGER.debug("%s: [Ph3] Second unlock.", self.address)
            if not await self._send_control_datapoint_wait_for_echo(unlock_dp_id, unlock_value):
                _LOGGER.warning("%s: [Ph3] No echo for second unlock — proceeding.", self.address)

            # Phase 4 — mechanical settle
            _LOGGER.debug("%s: [Ph4] Waiting 10s for mechanical settle.", self.address)
            await asyncio.sleep(10)

            # Phase 5 — lock if requested
            if target_lock and lock_dp_id is not None:
                _LOGGER.debug("%s: [Ph5] Sending lock (DP %s).", self.address, lock_dp_id)
                future_lock = asyncio.get_running_loop().create_future()

                def _lock_cb(dps: list[GimdowBLEDataPoint]) -> None:
                    for dp in dps:
                        if dp.id == state_dp_id and not bool(dp.value) and not future_lock.done():
                            future_lock.set_result(True)

                remove_lock_cb = self.register_callback(_lock_cb)
                try:
                    await self.send_control_datapoint(lock_dp_id, lock_value)
                    await asyncio.wait_for(future_lock, timeout=75)
                    _LOGGER.debug("%s: [Ph5] Locked confirmed.", self.address)
                except asyncio.TimeoutError:
                    err = GimdowBLEStateTimeoutError("Locked", 75)
                    ctx = self._diag_context("resolve/phase5", error=str(err), target=target_label)
                    ctx.log(_LOGGER, logging.WARNING)
                except Exception as e:
                    ctx = self._diag_context("resolve/phase5", error=str(e), target=target_label)
                    ctx.log(_LOGGER)
                finally:
                    remove_lock_cb()
        finally:
            self._is_resolving = False
            _LOGGER.debug("%s: resolve_unknown_state done. target=%s", self.address, target_label)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def _diag_context(self, action: str, error: str | None = None, **extra) -> GimdowBLEDiagContext:
        return GimdowBLEDiagContext(
            timestamp=time.time(),
            address=self.address,
            is_connected=bool(self._client and self._client.is_connected),
            is_paired=self._is_paired,
            is_resolving=self._is_resolving,
            dp_state={
                f"dp{dp_id}": dp.value
                for dp_id, dp in self._datapoints._datapoints.items()
            },
            action=action,
            error=error,
            extra={
                "protocol_version": self._protocol_version,
                "is_bound": self._is_bound,
                **extra,
            },
        )

