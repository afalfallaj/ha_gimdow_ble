"""Tests for GimdowBLEConnection — constants, init, callbacks, lifecycle, reconnect, ensure_connected."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak_retry_connector import BleakError, BleakNotFoundError

from custom_components.gimdow_ble.gimdow_ble.connection import GimdowBLEConnection
from custom_components.gimdow_ble.gimdow_ble.const import GimdowBLECode


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubConnection(GimdowBLEConnection):
    """Minimal stub for init/callback/lifecycle tests."""

    address = "AA:BB:CC:DD:EE:FF"
    rssi = None
    _ble_device = MagicMock()
    _is_paired = False

    def _build_pairing_request(self) -> bytes:
        return b""

    def _notification_handler(self, _sender, _data) -> None:
        pass

    async def _reset_seq_num(self) -> None:
        pass


class _FullStub(GimdowBLEConnection):
    """Full stub with seq-num state for send/ensure/reconnect tests."""

    address = "AA:BB:CC:DD:EE:FF"
    rssi = -60
    _ble_device = MagicMock()
    _is_paired = False

    def _build_pairing_request(self) -> bytes:
        return b"\x00" * 44

    def _notification_handler(self, _sender, _data) -> None:
        pass

    async def _reset_seq_num(self) -> None:
        async with self._seq_num_lock:
            self._current_seq_num = 1


def _make_full_stub() -> _FullStub:
    conn = _FullStub()
    conn._init_connection()
    conn._seq_num_lock = asyncio.Lock()
    conn._current_seq_num = 1
    conn._schedule_keep_alive = MagicMock()
    conn._fire_connected_callbacks = MagicMock()
    conn._create_safe_task = MagicMock(side_effect=lambda coro, **_kw: coro.close())
    conn._cancel_keep_alive = MagicMock()
    return conn


def _make_ble_client(*, is_connected: bool = True) -> AsyncMock:
    client = AsyncMock()
    client.is_connected = is_connected
    client.start_notify = AsyncMock()
    client.disconnect = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# TestConnectionConstants
# ---------------------------------------------------------------------------


class TestConnectionConstants:
    def test_max_command_attempts_is_3(self) -> None:
        assert GimdowBLEConnection._MAX_COMMAND_ATTEMPTS == 3

    def test_proxy_clear_delay_is_2(self) -> None:
        assert GimdowBLEConnection._PROXY_CLEAR_DELAY == 2.0

    def test_reconnect_backoff_max_is_60s(self) -> None:
        assert GimdowBLEConnection._RECONNECT_BACKOFF_MAX == 60

    def test_constants_accessible_on_instance(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        assert conn._MAX_COMMAND_ATTEMPTS == 3
        assert conn._PROXY_CLEAR_DELAY == 2.0


# ---------------------------------------------------------------------------
# TestInitConnection
# ---------------------------------------------------------------------------


class TestInitConnection:
    def test_keep_alive_timer_initialized_none(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        assert conn._keep_alive_timer is None

    def test_connect_lock_is_asyncio_lock(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        assert isinstance(conn._connect_lock, asyncio.Lock)

    def test_expected_disconnect_false(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        assert conn._expected_disconnect is False

    def test_is_paired_false(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        assert conn._is_paired is False

    def test_callbacks_empty(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        assert conn._connected_callbacks == []
        assert conn._callbacks == []
        assert conn._disconnected_callbacks == []

    def test_client_none(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        assert conn._client is None


# ---------------------------------------------------------------------------
# TestCallbackRegistration
# ---------------------------------------------------------------------------


class TestCallbackRegistration:
    def test_register_connected_callback(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        cb = MagicMock()
        unregister = conn.register_connected_callback(cb)
        assert cb in conn._connected_callbacks
        unregister()
        assert cb not in conn._connected_callbacks

    def test_multiple_connected_callbacks_independent(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        cb1, cb2 = MagicMock(), MagicMock()
        conn.register_connected_callback(cb1)
        conn.register_connected_callback(cb2)
        assert len(conn._connected_callbacks) == 2

    def test_fire_connected_calls_all_callbacks(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        cb1, cb2 = MagicMock(), MagicMock()
        conn.register_connected_callback(cb1)
        conn.register_connected_callback(cb2)
        conn._fire_connected_callbacks()
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_register_datapoint_callback(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        cb = MagicMock()
        unregister = conn.register_callback(cb)
        assert cb in conn._callbacks
        unregister()
        assert cb not in conn._callbacks

    def test_fire_callbacks_passes_datapoints(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        received: list = []
        conn.register_callback(lambda dps: received.extend(dps))
        fake_dp = MagicMock()
        conn._fire_callbacks([fake_dp])
        assert fake_dp in received

    def test_register_disconnected_callback(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        cb = MagicMock()
        unregister = conn.register_disconnected_callback(cb)
        assert cb in conn._disconnected_callbacks
        unregister()
        assert cb not in conn._disconnected_callbacks

    def test_fire_disconnected_calls_all_callbacks(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        cb1, cb2 = MagicMock(), MagicMock()
        conn.register_disconnected_callback(cb1)
        conn.register_disconnected_callback(cb2)
        conn._fire_disconnected_callbacks()
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_unregistered_disconnected_callback_not_fired(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        cb = MagicMock()
        unregister = conn.register_disconnected_callback(cb)
        unregister()
        conn._fire_disconnected_callbacks()
        cb.assert_not_called()


# ---------------------------------------------------------------------------
# TestDisconnectedCallback
# ---------------------------------------------------------------------------


class TestDisconnectedCallback:
    def test_fires_disconnected_callbacks(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        cb1, cb2 = MagicMock(), MagicMock()
        conn.register_disconnected_callback(cb1)
        conn.register_disconnected_callback(cb2)
        conn._disconnected(MagicMock())
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_clears_is_paired(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._is_paired = True
        conn._disconnected(MagicMock())
        assert conn._is_paired is False

    def test_expected_disconnect_returns_early(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._expected_disconnect = True
        mock_client = MagicMock()
        conn._client = mock_client
        conn._disconnected(mock_client)
        assert conn._client is mock_client

    def test_unexpected_disconnect_nulls_client(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        mock_client = MagicMock()
        conn._client = mock_client
        conn._is_paired = False
        conn._disconnected(mock_client)
        assert conn._client is None

    def test_unpaired_unexpected_no_reconnect(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._is_paired = False
        conn._create_safe_task = MagicMock()
        conn._disconnected(MagicMock())
        conn._create_safe_task.assert_not_called()

    async def test_was_paired_unexpected_schedules_reconnect(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._is_paired = True
        conn._reconnect = AsyncMock()
        conn._create_safe_task = MagicMock()
        conn._disconnected(MagicMock())
        await asyncio.sleep(0)
        conn._create_safe_task.assert_called_once()


# ---------------------------------------------------------------------------
# TestKeepAlive
# ---------------------------------------------------------------------------


class TestKeepAlive:
    def test_keep_alive_fired_noop_when_client_none(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._create_safe_task = MagicMock()
        conn._keep_alive_fired()
        conn._create_safe_task.assert_not_called()

    def test_keep_alive_fired_noop_when_not_paired(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._is_paired = False
        mock_client = MagicMock()
        mock_client.is_connected = True
        conn._client = mock_client
        conn._create_safe_task = MagicMock()
        conn._keep_alive_fired()
        conn._create_safe_task.assert_not_called()

    def test_keep_alive_fired_noop_when_client_disconnected(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._is_paired = True
        mock_client = MagicMock()
        mock_client.is_connected = False
        conn._client = mock_client
        conn._create_safe_task = MagicMock()
        conn._keep_alive_fired()
        conn._create_safe_task.assert_not_called()

    def test_keep_alive_fired_creates_task_when_connected_and_paired(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._is_paired = True
        mock_client = MagicMock()
        mock_client.is_connected = True
        conn._client = mock_client
        conn._keep_alive_ping = AsyncMock()
        conn._create_safe_task = MagicMock()
        conn._keep_alive_fired()
        conn._create_safe_task.assert_called_once()

    def test_cancel_keep_alive_noop_when_none(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._cancel_keep_alive()

    def test_cancel_keep_alive_cancels_timer_and_clears(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        fake_timer = MagicMock()
        conn._keep_alive_timer = fake_timer
        conn._cancel_keep_alive()
        fake_timer.cancel.assert_called_once()
        assert conn._keep_alive_timer is None

    async def test_schedule_keep_alive_sets_timer(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._schedule_keep_alive()
        assert conn._keep_alive_timer is not None
        conn._cancel_keep_alive()

    async def test_schedule_cancels_existing_before_setting_new(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._schedule_keep_alive()
        first_timer = conn._keep_alive_timer
        conn._schedule_keep_alive()
        assert first_timer is not conn._keep_alive_timer
        conn._cancel_keep_alive()


# ---------------------------------------------------------------------------
# TestSendPacketsLocked
# ---------------------------------------------------------------------------


class TestSendPacketsLocked:
    async def test_bleak_exception_raises_without_spawning_background_task_when_paired(
        self,
    ) -> None:
        conn = _make_full_stub()
        conn._is_paired = True
        conn._int_send_packets_locked = AsyncMock(side_effect=OSError("write fail"))
        with pytest.raises(OSError):
            await conn._send_packets_locked([b"\x00"])
        conn._create_safe_task.assert_not_called()

    async def test_bleak_exception_raises_without_spawning_background_task_when_not_paired(
        self,
    ) -> None:
        conn = _make_full_stub()
        conn._is_paired = False
        conn._int_send_packets_locked = AsyncMock(side_effect=OSError("write fail"))
        with pytest.raises(OSError):
            await conn._send_packets_locked([b"\x00"])
        conn._create_safe_task.assert_not_called()


# ---------------------------------------------------------------------------
# TestSendPacketRetry
# ---------------------------------------------------------------------------


class TestSendPacketRetry:
    async def test_expected_disconnect_returns_immediately(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._expected_disconnect = True
        conn._ensure_connected = AsyncMock()
        await conn._send_packet(GimdowBLECode.FUN_SENDER_DPS, b"")
        conn._ensure_connected.assert_not_called()

    async def test_bleak_not_found_raises_without_retry(self) -> None:
        conn = _StubConnection()
        conn._init_connection()

        async def fake_ensure():
            conn._client = MagicMock()
            conn._client.is_connected = True

        conn._ensure_connected = fake_ensure
        conn._send_packet_while_connected = AsyncMock(side_effect=BleakNotFoundError())
        with pytest.raises(BleakNotFoundError):
            await conn._send_packet(GimdowBLECode.FUN_SENDER_DPS, b"")
        assert conn._send_packet_while_connected.call_count == 1

    async def test_retries_on_bleak_error_up_to_max_attempts(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._PROXY_CLEAR_DELAY = 0
        call_count = 0

        async def fake_ensure():
            conn._client = MagicMock()
            conn._client.is_connected = True

        async def always_fail(*args):
            nonlocal call_count
            call_count += 1
            raise OSError("fail")

        conn._ensure_connected = fake_ensure
        conn._send_packet_while_connected = always_fail
        with pytest.raises(OSError):
            await conn._send_packet(GimdowBLECode.FUN_SENDER_DPS, b"")
        assert call_count == GimdowBLEConnection._MAX_COMMAND_ATTEMPTS

    async def test_succeeds_on_second_attempt(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._PROXY_CLEAR_DELAY = 0
        call_count = 0

        async def fake_ensure():
            conn._client = MagicMock()
            conn._client.is_connected = True

        async def fail_once(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("transient")

        conn._ensure_connected = fake_ensure
        conn._send_packet_while_connected = fail_once
        await conn._send_packet(GimdowBLECode.FUN_SENDER_DPS, b"")
        assert call_count == 2

    async def test_not_connected_after_ensure_raises_bleak_error(self) -> None:
        conn = _StubConnection()
        conn._init_connection()
        conn._PROXY_CLEAR_DELAY = 0
        sent_count = 0

        async def fake_ensure():
            pass

        async def count_send(*args):
            nonlocal sent_count
            sent_count += 1

        conn._ensure_connected = fake_ensure
        conn._send_packet_while_connected = count_send
        with pytest.raises(BleakError):
            await conn._send_packet(GimdowBLECode.FUN_SENDER_DPS, b"")
        assert sent_count == 0


# ---------------------------------------------------------------------------
# TestEnsureConnectedEarlyReturn
# ---------------------------------------------------------------------------


class TestEnsureConnectedEarlyReturn:
    async def test_returns_immediately_when_expected_disconnect(self) -> None:
        conn = _make_full_stub()
        conn._expected_disconnect = True
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection"
        ) as mock_ec:
            await conn._ensure_connected()
        mock_ec.assert_not_called()

    async def test_returns_immediately_when_already_connected_and_paired(self) -> None:
        conn = _make_full_stub()
        conn._client = _make_ble_client()
        conn._is_paired = True
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection"
        ) as mock_ec:
            await conn._ensure_connected()
        mock_ec.assert_not_called()


# ---------------------------------------------------------------------------
# TestEnsureConnectedBLEFailures
# ---------------------------------------------------------------------------


class TestEnsureConnectedBLEFailures:
    async def test_bleak_exception_returns_silently(self) -> None:
        conn = _make_full_stub()
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            side_effect=OSError("BLE unavailable"),
        ):
            await conn._ensure_connected()
        assert conn._client is None

    async def test_cancelled_error_propagates(self) -> None:
        conn = _make_full_stub()
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            side_effect=asyncio.CancelledError,
        ):
            with pytest.raises(asyncio.CancelledError):
                await conn._ensure_connected()

    async def test_unexpected_exception_returns_silently(self) -> None:
        conn = _make_full_stub()
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            side_effect=RuntimeError("unexpected"),
        ):
            await conn._ensure_connected()
        assert conn._client is None

    async def test_client_not_connected_after_establish_returns(self) -> None:
        conn = _make_full_stub()
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=_make_ble_client(is_connected=False),
        ):
            await conn._ensure_connected()
        assert conn._client is None


# ---------------------------------------------------------------------------
# TestEnsureConnectedStartNotify
# ---------------------------------------------------------------------------


class TestEnsureConnectedStartNotify:
    async def test_start_notify_failure_schedules_reconnect(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        mock_client.start_notify.side_effect = OSError("notify failed")
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            await conn._ensure_connected()
        assert conn._client is None
        conn._create_safe_task.assert_called_once()

    async def test_start_notify_failure_does_not_proceed_to_handshake(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        mock_client.start_notify.side_effect = OSError("notify failed")
        conn._send_packet_while_connected = AsyncMock(return_value=True)
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            await conn._ensure_connected()
        conn._send_packet_while_connected.assert_not_called()


# ---------------------------------------------------------------------------
# TestEnsureConnectedHandshake
# ---------------------------------------------------------------------------


def _patch_success(conn: _FullStub, mock_client: AsyncMock) -> None:
    async def _handshake(code, data, response_to, wait):
        if code == GimdowBLECode.FUN_SENDER_PAIR:
            conn._is_paired = True
        return True

    conn._send_packet_while_connected = AsyncMock(side_effect=_handshake)


class TestEnsureConnectedHandshake:
    async def test_device_info_returns_false_disconnects(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            conn._send_packet_while_connected = AsyncMock(return_value=False)
            await conn._ensure_connected()
        assert conn._client is None
        mock_client.disconnect.assert_awaited_once()

    async def test_device_info_raises_disconnects(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            conn._send_packet_while_connected = AsyncMock(
                side_effect=OSError("write fail")
            )
            await conn._ensure_connected()
        assert conn._client is None
        mock_client.disconnect.assert_awaited_once()

    async def test_pair_returns_false_disconnects(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        call_count = 0

        async def _side(code, data, response_to, wait):
            nonlocal call_count
            call_count += 1
            return call_count == 1

        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            conn._send_packet_while_connected = AsyncMock(side_effect=_side)
            await conn._ensure_connected()
        assert conn._client is None
        mock_client.disconnect.assert_awaited()

    async def test_pair_raises_disconnects(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        call_count = 0

        async def _side(code, data, response_to, wait):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return True
            raise OSError("pair write fail")

        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            conn._send_packet_while_connected = AsyncMock(side_effect=_side)
            await conn._ensure_connected()
        assert conn._client is None
        mock_client.disconnect.assert_awaited()


# ---------------------------------------------------------------------------
# TestEnsureConnectedSuccess
# ---------------------------------------------------------------------------


class TestEnsureConnectedSuccess:
    async def test_success_fires_connected_callbacks(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        _patch_success(conn, mock_client)
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            await conn._ensure_connected()
        conn._fire_connected_callbacks.assert_called_once()

    async def test_success_schedules_keep_alive(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        _patch_success(conn, mock_client)
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            await conn._ensure_connected()
        conn._schedule_keep_alive.assert_called_once()

    async def test_success_sets_client(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        _patch_success(conn, mock_client)
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            await conn._ensure_connected()
        assert conn._client is mock_client

    async def test_not_paired_after_handshake_no_callbacks(self) -> None:
        conn = _make_full_stub()
        mock_client = _make_ble_client()
        conn._send_packet_while_connected = AsyncMock(return_value=True)
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.connection.establish_connection",
            return_value=mock_client,
        ):
            await conn._ensure_connected()
        conn._fire_connected_callbacks.assert_not_called()


# ---------------------------------------------------------------------------
# TestReconnectEarlyAbort
# ---------------------------------------------------------------------------


class TestReconnectEarlyAbort:
    async def test_expected_disconnect_exits_before_loop(self) -> None:
        conn = _make_full_stub()
        conn._expected_disconnect = True
        conn._ensure_connected = AsyncMock()
        await conn._reconnect()
        conn._ensure_connected.assert_not_awaited()

    async def test_expected_disconnect_mid_loop_exits(self) -> None:
        conn = _make_full_stub()
        call_count = 0

        async def _ec():
            nonlocal call_count
            call_count += 1
            conn._expected_disconnect = True
            conn._client = AsyncMock()
            conn._client.is_connected = True

        conn._ensure_connected = AsyncMock(side_effect=_ec)
        await conn._reconnect()
        assert call_count == 1


# ---------------------------------------------------------------------------
# TestReconnectRetries
# ---------------------------------------------------------------------------


class TestReconnectRetries:
    async def test_succeeds_on_first_attempt(self) -> None:
        conn = _make_full_stub()
        mock_client = AsyncMock()
        mock_client.is_connected = True

        async def _ec():
            conn._client = mock_client

        conn._ensure_connected = AsyncMock(side_effect=_ec)
        with patch("asyncio.sleep"):
            await conn._reconnect()
        assert conn._ensure_connected.await_count == 1

    async def test_retries_on_bleak_exception_then_succeeds(self) -> None:
        conn = _make_full_stub()
        mock_client = AsyncMock()
        mock_client.is_connected = True
        attempts = []

        async def _ec():
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError("transient")
            conn._client = mock_client

        conn._ensure_connected = AsyncMock(side_effect=_ec)
        with patch("asyncio.sleep"):
            await conn._reconnect()
        assert len(attempts) == 3

    async def test_unexpected_exception_also_retries(self) -> None:
        conn = _make_full_stub()
        mock_client = AsyncMock()
        mock_client.is_connected = True
        attempts = []

        async def _ec():
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("unexpected")
            conn._client = mock_client

        conn._ensure_connected = AsyncMock(side_effect=_ec)
        with patch("asyncio.sleep"):
            await conn._reconnect()
        assert len(attempts) == 2

    async def test_loop_exits_only_when_expected_disconnect_set(self) -> None:
        conn = _make_full_stub()
        attempt_count = 0

        async def _ec():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count >= 5:
                conn._expected_disconnect = True
            raise OSError("transient")

        conn._ensure_connected = AsyncMock(side_effect=_ec)
        with patch("asyncio.sleep"):
            await conn._reconnect()
        assert attempt_count == 5

    async def test_resets_seq_num_at_start(self) -> None:
        conn = _make_full_stub()
        conn._current_seq_num = 99
        mock_client = AsyncMock()
        mock_client.is_connected = True

        async def _ec():
            conn._client = mock_client

        conn._ensure_connected = AsyncMock(side_effect=_ec)
        with patch("asyncio.sleep"):
            await conn._reconnect()
        assert conn._current_seq_num == 1


# ---------------------------------------------------------------------------
# TestReconnectBackoff
# ---------------------------------------------------------------------------


class TestReconnectBackoff:
    def _make_terminating_stub(self, fail_count: int) -> _FullStub:
        conn = _make_full_stub()
        attempt_count = 0

        async def _ec():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count >= fail_count:
                conn._expected_disconnect = True
            raise OSError("fail")

        conn._ensure_connected = AsyncMock(side_effect=_ec)
        return conn

    async def test_backoff_capped_at_reconnect_backoff_max(self) -> None:
        conn = self._make_terminating_stub(15)
        sleep_args: list[float] = []

        async def _fake_sleep(seconds):
            sleep_args.append(seconds)

        with patch("asyncio.sleep", side_effect=_fake_sleep):
            await conn._reconnect()
        assert all(s <= conn._RECONNECT_BACKOFF_MAX for s in sleep_args)

    async def test_bleak_exception_uses_exponential_backoff(self) -> None:
        conn = self._make_terminating_stub(10)
        sleep_args: list[float] = []

        async def _fake_sleep(seconds):
            sleep_args.append(seconds)

        with patch("asyncio.sleep", side_effect=_fake_sleep):
            await conn._reconnect()
        non_capped = [s for s in sleep_args if s < conn._RECONNECT_BACKOFF_MAX]
        if len(non_capped) > 1:
            for i in range(1, len(non_capped)):
                assert non_capped[i] >= non_capped[i - 1]


# ---------------------------------------------------------------------------
# TestExecuteForcedDisconnect
# ---------------------------------------------------------------------------


class TestExecuteForcedDisconnect:
    async def test_clears_client_and_is_paired(self) -> None:
        conn = _make_full_stub()
        mock_client = AsyncMock()
        mock_client.is_connected = True
        conn._client = mock_client
        conn._is_paired = True
        await conn._execute_forced_disconnect()
        assert conn._client is None
        assert conn._is_paired is False

    async def test_resets_seq_num(self) -> None:
        conn = _make_full_stub()
        conn._current_seq_num = 42
        mock_client = AsyncMock()
        mock_client.is_connected = True
        conn._client = mock_client
        await conn._execute_forced_disconnect()
        assert conn._current_seq_num == 1

    async def test_stops_notify_and_disconnects_client(self) -> None:
        conn = _make_full_stub()
        mock_client = AsyncMock()
        mock_client.is_connected = True
        conn._client = mock_client
        await conn._execute_forced_disconnect()
        mock_client.stop_notify.assert_awaited_once()
        mock_client.disconnect.assert_awaited_once()

    async def test_bleak_exception_during_disconnect_is_swallowed(self) -> None:
        conn = _make_full_stub()
        mock_client = AsyncMock()
        mock_client.is_connected = True
        mock_client.disconnect.side_effect = OSError("disconnect error")
        conn._client = mock_client
        await conn._execute_forced_disconnect()
        assert conn._client is None
