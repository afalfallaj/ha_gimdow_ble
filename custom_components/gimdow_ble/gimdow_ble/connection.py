"""Gimdow BLE connection mixin — BLE lifecycle, send pipeline, and callbacks.

``GimdowBLEConnection`` is a mixin class. It must be combined with
``GimdowBLEProtocol`` (for :meth:`_build_packets`, :meth:`_notification_handler`)
and a concrete class that provides:

  - ``self.address`` (str)
  - ``self.rssi`` (int | None)
  - ``self._ble_device`` (BLEDevice)
  - ``self._is_paired`` (bool)  — shared with protocol mixin
  - ``self._build_pairing_request()`` (bytes)  — on the device class
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from bleak.backends.device import BLEDevice
from bleak.exc import BleakDBusError
from bleak_retry_connector import BLEAK_BACKOFF_TIME, BLEAK_RETRY_EXCEPTIONS
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakError,
    BleakNotFoundError,
    establish_connection,
)

from .const import CHARACTERISTIC_NOTIFY, CHARACTERISTIC_WRITE, RESPONSE_WAIT_TIMEOUT, GimdowBLECode
from .datapoints import GimdowBLEDataPoint

_LOGGER = logging.getLogger(__name__)

BLEAK_EXCEPTIONS = (*BLEAK_RETRY_EXCEPTIONS, OSError)

# Global lock prevents parallel BLE establish_connection calls that can
# interfere with each other on some platforms.
global_connect_lock = asyncio.Lock()


class GimdowBLEConnection:
    """Mixin: BLE lifecycle management, packet send pipeline, and event callbacks."""

    # ------------------------------------------------------------------
    # Connection state initialiser — call from GimdowBLEDevice.__init__
    # ------------------------------------------------------------------

    def _init_connection(self) -> None:
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect: bool = False
        self._connected_callbacks: list[Callable[[], None]] = []
        self._callbacks: list[Callable[[list[GimdowBLEDataPoint]], None]] = []
        self._disconnected_callbacks: list[Callable[[], None]] = []
        self._is_paired: bool = False

    # ------------------------------------------------------------------
    # Public callback registration
    # ------------------------------------------------------------------

    def register_connected_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired when a connection + handshake succeeds."""
        def unregister() -> None:
            self._connected_callbacks.remove(callback)
        self._connected_callbacks.append(callback)
        return unregister

    def register_callback(
        self, callback: Callable[[list[GimdowBLEDataPoint]], None]
    ) -> Callable[[], None]:
        """Register a callback fired on datapoint updates."""
        def unregister() -> None:
            self._callbacks.remove(callback)
        self._callbacks.append(callback)
        return unregister

    def register_disconnected_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired when the device disconnects."""
        def unregister() -> None:
            self._disconnected_callbacks.remove(callback)
        self._disconnected_callbacks.append(callback)
        return unregister

    # ------------------------------------------------------------------
    # Internal callback firing
    # ------------------------------------------------------------------

    def _fire_connected_callbacks(self) -> None:
        for cb in self._connected_callbacks:
            cb()

    def _fire_callbacks(self, datapoints: list[GimdowBLEDataPoint]) -> None:
        for cb in self._callbacks:
            cb(datapoints)

    def _fire_disconnected_callbacks(self) -> None:
        for cb in self._disconnected_callbacks:
            cb()

    def _create_safe_task(self, coro, *, name: str | None = None) -> asyncio.Task:
        """Create a task with exception logging (avoids silent swallow)."""
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(self._handle_task_exception)
        return task

    @staticmethod
    def _handle_task_exception(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        if exc := task.exception():
            _LOGGER.error(
                "Unhandled exception in background task %s: %s",
                task.get_name(), exc, exc_info=exc,
            )

    # ------------------------------------------------------------------
    # Lifecycle — start / stop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the device (reserved for future use)."""
        _LOGGER.debug("%s: Starting…", self.address)

    async def stop(self) -> None:
        """Disconnect and stop the device."""
        _LOGGER.debug("%s: Stopping", self.address)
        await self._execute_disconnect()

    # ------------------------------------------------------------------
    # Disconnect helpers
    # ------------------------------------------------------------------

    def _disconnect(self) -> None:
        """Schedule a timed disconnect (non-blocking)."""
        self._create_safe_task(self._execute_timed_disconnect())

    async def _execute_timed_disconnect(self) -> None:
        _LOGGER.debug("%s: Disconnecting", self.address)
        await self._execute_disconnect()

    async def _execute_disconnect(self) -> None:
        _LOGGER.debug("%s: Executing disconnect", self.address)
        async with self._connect_lock:
            client = self._client
            self._expected_disconnect = True
            self._client = None
            if client and client.is_connected:
                await client.stop_notify(CHARACTERISTIC_NOTIFY)
                await client.disconnect()
        async with self._seq_num_lock:
            self._current_seq_num = 1

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """BLE disconnected callback (called by bleak).

        Note: bleak_retry_connector >=4.0 marshals the disconnect callback
        to the event loop, so create_task() is safe here.  We add a
        defensive try/get_running_loop to handle edge cases where a
        foreign thread invokes this callback directly.
        """
        was_paired = self._is_paired
        self._is_paired = False
        self._fire_disconnected_callbacks()
        if self._expected_disconnect:
            _LOGGER.debug("%s: Expected disconnect; RSSI: %s", self.address, self.rssi)
            return
        self._client = None
        _LOGGER.debug("%s: Unexpected disconnect; RSSI: %s", self.address, self.rssi)
        if was_paired:
            _LOGGER.debug("%s: Scheduling reconnect", self.address)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                _LOGGER.warning(
                    "%s: No running event loop in _disconnected — cannot schedule reconnect",
                    self.address,
                )
                return
            loop.call_soon_threadsafe(self._create_safe_task, self._reconnect())

    # ------------------------------------------------------------------
    # Connect / reconnect
    # ------------------------------------------------------------------

    async def _ensure_connected(self) -> None:
        """Make sure a paired BLE connection exists; perform handshake if not."""
        global global_connect_lock
        if self._expected_disconnect:
            return
        if self._connect_lock.locked():
            _LOGGER.debug(
                "%s: Connection already in progress, waiting; RSSI: %s",
                self.address, self.rssi,
            )
        if self._client and self._client.is_connected and self._is_paired:
            return

        async with self._connect_lock:
            await asyncio.sleep(0.01)
            if self._client and self._client.is_connected and self._is_paired:
                return

            # --- Establish BLE connection ---
            try:
                async with global_connect_lock:
                    _LOGGER.debug("%s: Connecting; RSSI: %s", self.address, self.rssi)
                    client = await establish_connection(
                        BleakClientWithServiceCache,
                        self._ble_device,
                        self.address,
                        self._disconnected,
                        use_services_cache=True,
                        ble_device_callback=lambda: self._ble_device,
                    )
            except BLEAK_EXCEPTIONS as ex:
                _LOGGER.error("%s: BLE connection failed: %s", self.address, ex)
                return
            except Exception as ex:
                _LOGGER.error("%s: Unexpected error during connect: %s", self.address, ex, exc_info=True)
                return

            if not (client and client.is_connected):
                _LOGGER.debug("%s: Failed to connect", self.address)
                return

            _LOGGER.debug("%s: Connected; RSSI: %s", self.address, self.rssi)
            self._client = client

            # --- Subscribe to notifications ---
            try:
                await self._client.start_notify(CHARACTERISTIC_NOTIFY, self._notification_handler)
            except Exception as ex:
                # When start_notify fails, the peripheral already dropped the connection.
                # The _disconnected callback has fired and set self._client = None.
                # We need a full reconnect cycle — but we must wait for the ESPHome
                # proxy to exit its CONNECTING state before attempting again, otherwise
                # it will reject the next establish_connection with:
                #   "Connection request ignored, state: CONNECTING"
                _LOGGER.warning("%s: start_notify failed: %s — scheduling reconnect", self.address, ex)
                self._client = None
                self._create_safe_task(self._reconnect(initial_delay=self._PROXY_CLEAR_DELAY))
                return


            # --- Handshake: device info ---
            if self._client and self._client.is_connected:
                _LOGGER.debug("%s: Sending device info request", self.address)
                try:
                    if not await self._send_packet_while_connected(
                        GimdowBLECode.FUN_SENDER_DEVICE_INFO, bytes(0), 0, True
                    ):
                        _LOGGER.error("%s: Device info request failed", self.address)
                        self._client = None
                        await client.disconnect()
                        return
                except Exception as ex:
                    _LOGGER.error("%s: Device info request failed: %s", self.address, ex, exc_info=True)
                    self._client = None
                    await client.disconnect()
                    return

            # --- Handshake: pair ---
            if self._client and self._client.is_connected:
                _LOGGER.debug("%s: Sending pairing request", self.address)
                try:
                    if not await self._send_packet_while_connected(
                        GimdowBLECode.FUN_SENDER_PAIR, self._build_pairing_request(), 0, True
                    ):
                        _LOGGER.error("%s: Pairing request failed", self.address)
                        self._client = None
                        await client.disconnect()
                        return
                except Exception as ex:
                    _LOGGER.error("%s: Pairing request failed: %s", self.address, ex, exc_info=True)
                    self._client = None
                    await client.disconnect()
                    return

            # --- Final state assessment ---
            if self._client and self._client.is_connected:
                if self._is_paired:
                    _LOGGER.debug("%s: Connected and paired successfully", self.address)
                    self._fire_connected_callbacks()
                else:
                    _LOGGER.error("%s: Connected but pairing failed", self.address)
            else:
                _LOGGER.error("%s: Handshake incomplete — no valid connection", self.address)

    _MAX_RECONNECT_ATTEMPTS = 10
    _RECONNECT_BACKOFF_MAX = 300  # 5-minute ceiling
    # How long to wait before the first reconnect after a failed handshake.
    # The ESPHome BLE proxy needs this time to exit its CONNECTING state;
    # without it the next establish_connection call is rejected immediately.
    _PROXY_CLEAR_DELAY = 5.0

    async def _reconnect(self, initial_delay: float = 0.0) -> None:
        """Attempt reconnect with exponential backoff and max retries.

        Args:
            initial_delay: seconds to wait before the first connection attempt.
                Use _PROXY_CLEAR_DELAY when reconnecting after a failed handshake
                so the ESPHome proxy has time to clear its CONNECTING state.
        """
        _LOGGER.debug("%s: Reconnecting… (initial_delay=%.1fs)", self.address, initial_delay)
        async with self._seq_num_lock:
            self._current_seq_num = 1

        if initial_delay > 0:
            await asyncio.sleep(initial_delay)

        for attempt in range(1, self._MAX_RECONNECT_ATTEMPTS + 1):
            if self._expected_disconnect:
                return
            _LOGGER.debug(
                "%s: Reconnect attempt %s/%s",
                self.address, attempt, self._MAX_RECONNECT_ATTEMPTS,
            )
            try:
                await self._ensure_connected()
                if self._expected_disconnect:
                    return
                if not self._client or not self._client.is_connected:
                    raise BleakError("Failed to ensure connection")
                _LOGGER.debug("%s: Reconnect succeeded", self.address)
                return
            except BLEAK_EXCEPTIONS:
                backoff = min(
                    BLEAK_BACKOFF_TIME * (2 ** (attempt - 1)),
                    self._RECONNECT_BACKOFF_MAX,
                )
                _LOGGER.debug(
                    "%s: Reconnect attempt %s failed — backing off %ss",
                    self.address, attempt, backoff, exc_info=True,
                )
                await asyncio.sleep(backoff)

        _LOGGER.error(
            "%s: Reconnect failed after %s attempts — giving up",
            self.address, self._MAX_RECONNECT_ATTEMPTS,
        )

    # ------------------------------------------------------------------
    # Packet send pipeline
    # ------------------------------------------------------------------

    async def _send_packet(
        self,
        code: GimdowBLECode,
        data: bytes,
        wait_for_response: bool = True,
    ) -> None:
        """Ensure connection, then send a packet."""
        if self._expected_disconnect:
            return
        await self._ensure_connected()
        if self._expected_disconnect:
            return
        if not (self._client and self._client.is_connected):
            _LOGGER.debug("%s: Not connected — skipping send", self.address)
            raise BleakError(f"{self.address}: Not connected after _ensure_connected")
        await self._send_packet_while_connected(code, data, 0, wait_for_response)

    async def _send_response(self, code: GimdowBLECode, data: bytes, response_to: int) -> None:
        """Send a protocol response (no reconnect)."""
        if self._client and self._client.is_connected:
            await self._send_packet_while_connected(code, data, response_to, False)

    async def _send_packet_while_connected(
        self,
        code: GimdowBLECode,
        data: bytes,
        response_to: int,
        wait_for_response: bool,
    ) -> bool:
        result = True
        future: asyncio.Future | None = None
        seq_num = await self._get_seq_num()
        if wait_for_response:
            future = asyncio.Future()
            self._input_expected_responses[seq_num] = future

        if response_to > 0:
            _LOGGER.debug("%s: Sending #%s %s → #%s", self.address, seq_num, code.name, response_to)
        else:
            _LOGGER.debug("%s: Sending #%s %s", self.address, seq_num, code.name)

        packets = self._build_packets(seq_num, code, data, response_to)
        await self._int_send_packet_while_connected(packets)

        if future:
            try:
                await asyncio.wait_for(future, RESPONSE_WAIT_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.debug("%s: Timeout waiting for response; RSSI: %s", self.address, self.rssi)
                result = False
            self._input_expected_responses.pop(seq_num, None)

        return result

    async def _int_send_packet_while_connected(self, packets: list[bytes]) -> None:
        if self._operation_lock.locked():
            _LOGGER.debug(
                "%s: Operation in progress, waiting; RSSI: %s", self.address, self.rssi
            )
        async with self._operation_lock:
            try:
                await self._send_packets_locked(packets)
            except BleakNotFoundError:
                _LOGGER.error(
                    "%s: Device not found / poor RSSI: %s", self.address, self.rssi, exc_info=True
                )
                raise
            except BLEAK_EXCEPTIONS:
                _LOGGER.error("%s: Communication failed", self.address, exc_info=True)
                raise

    async def _resend_packets(self, packets: list[bytes], initial_delay: float = 0.0) -> None:
        if self._expected_disconnect:
            return
        if initial_delay > 0:
            _LOGGER.debug("%s: Waiting %.1fs before retry (proxy clear delay)", self.address, initial_delay)
            await asyncio.sleep(initial_delay)
        if self._expected_disconnect:
            return
        await self._ensure_connected()
        if self._expected_disconnect:
            return
        await self._int_send_packet_while_connected(packets)

    async def _send_packets_locked(self, packets: list[bytes]) -> None:
        try:
            await self._int_send_packets_locked(packets)
        except BleakDBusError as ex:
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug(
                "%s: Backing off %ss after DBus error: %s; RSSI: %s",
                self.address, BLEAK_BACKOFF_TIME, ex, self.rssi,
            )
            if self._is_paired:
                self._create_safe_task(self._resend_packets(packets, initial_delay=self._PROXY_CLEAR_DELAY))
            else:
                self._create_safe_task(self._reconnect(initial_delay=self._PROXY_CLEAR_DELAY))
        except BleakError as ex:
            _LOGGER.debug(
                "%s: BleakError during send: %s; RSSI: %s", self.address, ex, self.rssi
            )
            if self._is_paired:
                self._create_safe_task(self._resend_packets(packets, initial_delay=self._PROXY_CLEAR_DELAY))
            else:
                self._create_safe_task(self._reconnect(initial_delay=self._PROXY_CLEAR_DELAY))

    async def _int_send_packets_locked(self, packets: list[bytes]) -> None:
        """Write raw GATT packets to the device."""
        for packet in packets:
            if self._client:
                try:
                    await self._client.write_gatt_char(CHARACTERISTIC_WRITE, packet, False)
                except Exception:
                    _LOGGER.error("%s: Error sending packet", self.address, exc_info=True)
                    # Null the client so _ensure_connected doesn't short-circuit on
                    # a stale is_connected=True when the proxy dropped the link silently.
                    self._client = None
                    raise BleakError()
            else:
                _LOGGER.error("%s: Client disconnected during send", self.address)
                raise BleakError()
