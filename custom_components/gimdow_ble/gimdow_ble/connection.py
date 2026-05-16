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
import random
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

from .const import (
    CHARACTERISTIC_NOTIFY,
    CHARACTERISTIC_WRITE,
    RESPONSE_WAIT_TIMEOUT,
    GimdowBLECode,
)
from .datapoints import GimdowBLEDataPoint

_LOGGER = logging.getLogger(__name__)

BLEAK_EXCEPTIONS = (*BLEAK_RETRY_EXCEPTIONS, OSError)


class GimdowBLEConnection:
    """Mixin: BLE lifecycle management, packet send pipeline, and event callbacks."""

    # Tuning constants
    _KEEP_ALIVE_INTERVAL = 45.0  # seconds; renews the device's 60s session at 45s
    _MAX_COMMAND_ATTEMPTS = 3  # per-send retries before propagating the error
    _PROXY_CLEAR_DELAY = 2.0  # seconds between command-send retries
    _RECONNECT_BACKOFF_MAX = 60  # 60-second ceiling on exponential backoff

    # ------------------------------------------------------------------
    # Connection state initialiser — call from GimdowBLEDevice.__init__
    # ------------------------------------------------------------------

    def _init_connection(self) -> None:
        assert hasattr(self, "address"), (
            f"{type(self).__name__} must provide 'address' (str)"
        )
        assert hasattr(self, "rssi"), (
            f"{type(self).__name__} must provide 'rssi' (int | None)"
        )
        assert hasattr(self, "_ble_device"), (
            f"{type(self).__name__} must provide '_ble_device' (BLEDevice)"
        )
        assert callable(getattr(self, "_build_pairing_request", None)), (
            f"{type(self).__name__} must implement '_build_pairing_request() -> bytes'"
        )
        assert callable(getattr(self, "_notification_handler", None)), (
            f"{type(self).__name__} must implement '_notification_handler(sender, data)'"
        )
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect: bool = False
        self._connected_callbacks: list[Callable[[], None]] = []
        self._callbacks: list[Callable[[list[GimdowBLEDataPoint]], None]] = []
        self._disconnected_callbacks: list[Callable[[], None]] = []
        self._is_paired: bool = False
        self._keep_alive_timer = None

    # ------------------------------------------------------------------
    # Public callback registration
    # ------------------------------------------------------------------

    def register_connected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
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

    def register_disconnected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
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
                task.get_name(),
                exc,
                exc_info=exc,
            )

    # ------------------------------------------------------------------
    # Lifecycle — start / stop
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Disconnect and stop the device."""
        _LOGGER.debug("%s: Stopping", self.address)
        await self._execute_disconnect()

    # ------------------------------------------------------------------
    # Disconnect helpers
    # ------------------------------------------------------------------

    def _disconnect(self) -> None:
        """Schedule a disconnect (non-blocking)."""
        self._create_safe_task(self._execute_disconnect())

    async def _execute_disconnect(self) -> None:
        _LOGGER.debug("%s: Disconnecting", self.address)
        async with self._connect_lock:
            client = self._client
            self._expected_disconnect = True
            self._client = None
            if client and client.is_connected:
                await client.stop_notify(CHARACTERISTIC_NOTIFY)
                await client.disconnect()
        await self._reset_seq_num()

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

    async def _execute_forced_disconnect(self) -> None:
        """Force disconnect for error recovery. The _disconnected callback
        will trigger _reconnect to restore the persistent connection."""
        _LOGGER.debug("%s: Executing forced disconnect", self.address)
        self._cancel_keep_alive()
        async with self._connect_lock:
            client = self._client
            self._client = None
            self._is_paired = False
            if client:
                try:
                    if client.is_connected:
                        await client.stop_notify(CHARACTERISTIC_NOTIFY)
                        await client.disconnect()
                except BLEAK_EXCEPTIONS as ex:
                    _LOGGER.debug(
                        "%s: Error during forced disconnect: %s", self.address, ex
                    )
        await self._reset_seq_num()

    def _schedule_keep_alive(self) -> None:
        """Schedule or reschedule the keep-alive timer."""
        self._cancel_keep_alive()
        if self._expected_disconnect:
            return
        jitter = random.uniform(-5.0, 5.0)
        self._keep_alive_timer = asyncio.get_running_loop().call_later(
            self._KEEP_ALIVE_INTERVAL + jitter, self._keep_alive_fired
        )

    def _cancel_keep_alive(self) -> None:
        if self._keep_alive_timer:
            self._keep_alive_timer.cancel()
            self._keep_alive_timer = None

    def _keep_alive_fired(self) -> None:
        """Renew the BLE session to prevent the device's 60s hard timeout."""
        if self._client and self._client.is_connected and self._is_paired:
            _LOGGER.debug("%s: Keep-alive session renewal", self.address)
            self._create_safe_task(self._keep_alive_ping())

    async def _keep_alive_ping(self) -> None:
        try:
            # Re-pair renews the 60s session; device replies with result=2
            # ("already paired") which the protocol handler treats as success.
            await self._send_packet_while_connected(
                GimdowBLECode.FUN_SENDER_PAIR, self._build_pairing_request(), 0, True
            )
            _LOGGER.debug("%s: Session renewed", self.address)
        except asyncio.CancelledError:
            raise  # do not reschedule on shutdown/task-cancel
        except BLEAK_EXCEPTIONS:
            _LOGGER.warning(
                "%s: Keep-alive session renewal failed — device may drop connection within 60s",
                self.address,
                exc_info=True,
            )
        except Exception:
            _LOGGER.warning(
                "%s: Unexpected error in keep-alive ping",
                self.address,
                exc_info=True,
            )
        finally:
            if not self._expected_disconnect:
                self._schedule_keep_alive()

    # ------------------------------------------------------------------
    # Connect / reconnect
    # ------------------------------------------------------------------

    async def _ensure_connected(self) -> None:
        """Make sure a paired BLE connection exists; perform handshake if not."""
        if self._expected_disconnect:
            return
        if self._connect_lock.locked():
            _LOGGER.debug(
                "%s: Connection already in progress, waiting; RSSI: %s",
                self.address,
                self.rssi,
            )
        if self._client and self._client.is_connected and self._is_paired:
            return

        async with self._connect_lock:
            await asyncio.sleep(0.01)
            if self._client and self._client.is_connected and self._is_paired:
                return

            client = await self._establish_ble_connection()
            if not client:
                return
            if not await self._subscribe_notifications(client):
                return
            if not await self._do_device_info_handshake(client):
                return
            if not await self._do_pair_handshake(client):
                return
            self._finalize_connection()

    async def _establish_ble_connection(self) -> BleakClientWithServiceCache | None:
        """Call establish_connection and return the client, or None on failure."""
        try:
            _LOGGER.debug("%s: Connecting; RSSI: %s", self.address, self.rssi)
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.address,
                self._disconnected,
                use_services_cache=True,
                ble_device_callback=lambda: self._ble_device,
            )
        except asyncio.CancelledError:
            raise
        except BLEAK_EXCEPTIONS as ex:
            _LOGGER.error("%s: BLE connection failed: %s", self.address, ex)
            return None
        except Exception as ex:
            _LOGGER.error(
                "%s: Unexpected error during connect: %s",
                self.address,
                ex,
                exc_info=True,
            )
            return None

        if not (client and client.is_connected):
            _LOGGER.error(
                "%s: establish_connection returned but client is not connected",
                self.address,
            )
            return None

        _LOGGER.debug("%s: Connected; RSSI: %s", self.address, self.rssi)
        self._client = client
        return client

    async def _subscribe_notifications(
        self, client: BleakClientWithServiceCache
    ) -> bool:
        """Subscribe to BLE notifications. Returns False and schedules reconnect on failure.

        When start_notify fails, the peripheral has already dropped the connection.
        We must wait for the ESPHome proxy to exit CONNECTING state before retrying
        (otherwise it rejects establish_connection with "state: CONNECTING").
        """
        try:
            await client.start_notify(CHARACTERISTIC_NOTIFY, self._notification_handler)
            return True
        except Exception as ex:
            _LOGGER.warning(
                "%s: start_notify failed: %s — scheduling reconnect",
                self.address,
                ex,
            )
            self._client = None
            self._create_safe_task(self._reconnect())
            return False

    async def _do_device_info_handshake(
        self, client: BleakClientWithServiceCache
    ) -> bool:
        """Send DEVICE_INFO request. Returns False and disconnects on failure."""
        _LOGGER.debug("%s: Sending device info request", self.address)
        try:
            if not await self._send_packet_while_connected(
                GimdowBLECode.FUN_SENDER_DEVICE_INFO, bytes(0), 0, True
            ):
                _LOGGER.error("%s: Device info request failed", self.address)
                self._client = None
                await client.disconnect()
                return False
        except Exception as ex:
            _LOGGER.error(
                "%s: Device info request failed: %s",
                self.address,
                ex,
                exc_info=True,
            )
            self._client = None
            await client.disconnect()
            return False
        return True

    async def _do_pair_handshake(self, client: BleakClientWithServiceCache) -> bool:
        """Send PAIR request. Returns False and disconnects on failure."""
        _LOGGER.debug("%s: Sending pairing request", self.address)
        try:
            if not await self._send_packet_while_connected(
                GimdowBLECode.FUN_SENDER_PAIR,
                self._build_pairing_request(),
                0,
                True,
            ):
                _LOGGER.error("%s: Pairing request failed", self.address)
                self._client = None
                await client.disconnect()
                return False
        except Exception as ex:
            _LOGGER.error(
                "%s: Pairing request failed: %s",
                self.address,
                ex,
                exc_info=True,
            )
            self._client = None
            await client.disconnect()
            return False
        return True

    def _finalize_connection(self) -> None:
        """Log the outcome and fire callbacks / schedule keep-alive on success."""
        if self._client and self._client.is_connected:
            if self._is_paired:
                _LOGGER.info(
                    "%s: Connected and paired successfully; RSSI: %s",
                    self.address,
                    self.rssi,
                )
                self._schedule_keep_alive()
                self._fire_connected_callbacks()
            else:
                _LOGGER.warning(
                    "%s: Connected but pairing failed — will retry on next command",
                    self.address,
                )
        else:
            _LOGGER.warning(
                "%s: Handshake incomplete — no valid connection; RSSI: %s",
                self.address,
                self.rssi,
            )

    async def _reconnect(self, initial_delay: float = 0.0) -> None:
        """Attempt reconnect with exponential backoff, retrying indefinitely.

        Exits only when ``_expected_disconnect`` is set (user-initiated stop).
        For a lock, giving up after N attempts is more dangerous than retrying
        forever — a locked-out device cannot be operated until HA restarts.

        Args:
            initial_delay: seconds to wait before the first attempt.
                Use _PROXY_CLEAR_DELAY after a failed handshake so the ESPHome
                proxy has time to exit its CONNECTING state.
        """
        _LOGGER.info(
            "%s: Reconnecting… (initial_delay=%.1fs); RSSI: %s",
            self.address,
            initial_delay,
            self.rssi,
        )
        await self._reset_seq_num()

        attempt = 0
        while not self._expected_disconnect:
            attempt += 1
            _LOGGER.debug("%s: Reconnect attempt %s", self.address, attempt)
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
                _LOGGER.warning(
                    "%s: Reconnect attempt %s failed — backing off %.1fs; RSSI: %s",
                    self.address,
                    attempt,
                    backoff,
                    self.rssi,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
            except Exception:
                _LOGGER.exception(
                    "%s: Unexpected error during reconnect attempt %s",
                    self.address,
                    attempt,
                )
                await asyncio.sleep(BLEAK_BACKOFF_TIME)

    # ------------------------------------------------------------------
    # Packet send pipeline
    # ------------------------------------------------------------------

    async def _send_packet(
        self,
        code: GimdowBLECode,
        data: bytes,
        wait_for_response: bool = True,
    ) -> None:
        """Ensure connection, then send a packet with retry."""
        last_error: Exception | None = None
        for attempt in range(1, self._MAX_COMMAND_ATTEMPTS + 1):
            if self._expected_disconnect:
                return
            try:
                await self._ensure_connected()
                if self._expected_disconnect:
                    return
                if not (self._client and self._client.is_connected):
                    _LOGGER.debug("%s: Not connected — skipping send", self.address)
                    raise BleakError(
                        f"{self.address}: Not connected after _ensure_connected"
                    )
                await self._send_packet_while_connected(
                    code, data, 0, wait_for_response
                )
                return  # Success
            except BleakNotFoundError:
                raise  # Device gone — don't retry
            except BLEAK_EXCEPTIONS as ex:
                last_error = ex
                if attempt < self._MAX_COMMAND_ATTEMPTS:
                    backoff = self._PROXY_CLEAR_DELAY
                    _LOGGER.debug(
                        "%s: Command attempt %s/%s failed: %s — retrying in %.1fs",
                        self.address,
                        attempt,
                        self._MAX_COMMAND_ATTEMPTS,
                        ex,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    _LOGGER.error(
                        "%s: Command failed after %s attempts",
                        self.address,
                        self._MAX_COMMAND_ATTEMPTS,
                        exc_info=True,
                    )
        if last_error:
            raise last_error

    async def _send_response(
        self, code: GimdowBLECode, data: bytes, response_to: int
    ) -> None:
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
            _LOGGER.debug(
                "%s: Sending #%s %s → #%s",
                self.address,
                seq_num,
                code.name,
                response_to,
            )
        else:
            _LOGGER.debug("%s: Sending #%s %s", self.address, seq_num, code.name)

        packets = self._build_packets(seq_num, code, data, response_to)
        await self._int_send_packet_while_connected(packets)

        if future:
            try:
                await asyncio.wait_for(future, RESPONSE_WAIT_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "%s: Timeout waiting for response; RSSI: %s",
                    self.address,
                    self.rssi,
                )
                result = False
            finally:
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
                    "%s: Device not found / poor RSSI: %s",
                    self.address,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except BLEAK_EXCEPTIONS:
                _LOGGER.error("%s: Communication failed", self.address, exc_info=True)
                raise

    async def _send_packets_locked(self, packets: list[bytes]) -> None:
        try:
            await self._int_send_packets_locked(packets)
        except BleakDBusError as ex:
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.warning(
                "%s: DBus error during send (backoff %ss): %s; RSSI: %s — forcing disconnect",
                self.address,
                BLEAK_BACKOFF_TIME,
                ex,
                self.rssi,
            )
            await self._execute_forced_disconnect()
            raise
        except BLEAK_EXCEPTIONS as ex:
            _LOGGER.warning(
                "%s: Send error during packet transmission: %s; RSSI: %s — forcing disconnect",
                self.address,
                ex,
                self.rssi,
            )
            raise

    async def _int_send_packets_locked(self, packets: list[bytes]) -> None:
        """Write raw GATT packets to the device."""
        for packet in packets:
            if self._client:
                try:
                    await self._client.write_gatt_char(
                        CHARACTERISTIC_WRITE, packet, False
                    )
                except Exception:
                    _LOGGER.warning(
                        "%s: write_gatt_char failed — nulling client to force reconnect",
                        self.address,
                        exc_info=True,
                    )
                    # Null the client so _ensure_connected doesn't short-circuit on
                    # a stale is_connected=True when the proxy dropped the link silently.
                    self._client = None
                    raise BleakError()
            else:
                _LOGGER.warning(
                    "%s: Client is None during send — connection was lost", self.address
                )
                raise BleakError()
