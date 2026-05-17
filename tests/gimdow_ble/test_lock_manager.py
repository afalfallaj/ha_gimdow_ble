"""Tests for GimdowBLELockManager — full state machine coverage.

Covers:
  lock/unlock       — normal path, door-open pending, exception, attribution, already-locked
  unknown state     — DOUBLE_ON_ACTION, CONFIRM_LAST, deduplication, unlock path
  transition timeout — zero no-op, positive task, fires unknown, cancel, coordinator cancel
  on_connected      — transition reset, first-boot None guard, dedup, all strategies
  cleanup (M5)      — cancels all background tasks
  auto-lock timer   — N4 property, N7 none-guard, start/stop lifecycle
  attribution (S7)  — update_attribution all sources; None-guard short-circuit
  double-command (S6) — echo timeout → TIMEOUT_UNKNOWN; CancelledError; exception
  coordinator update  — transition flag clearing; safety-net timer restart
  door changed        — pending intent, auto-lock timer, state tracking
  auto-lock callback  — already locked skip; not locked fires lock()
  setting callbacks   — on_auto_lock_setting_changed / on_auto_lock_time_changed
  PendingLockIntent   — set/clear/bool/should_auto_execute
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.gimdow_ble.gimdow_ble.lock_manager import (
    GimdowBLELockManager,
    LockBlockedReason,
    LockManagerConfig,
    LockTransitionState,
    PendingLockIntent,
)
from custom_components.gimdow_ble.const import (
    ACTION_SOURCE_AUTO,
    ACTION_SOURCE_HA,
    UNKNOWN_STATE_ACTION_CONFIRM_LAST,
    UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION,
    UNKNOWN_STATE_ACTION_FORCE_LOCK_TWICE,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_manager(
    *,
    last_known_state: bool | None = None,
    unknown_state_action: str = UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION,
    virtual_auto_lock: bool = False,
    is_locked_return: bool | None = None,
    transition_timeout: float = 0,
    has_door_sensor: bool = False,
) -> tuple[GimdowBLELockManager, MagicMock, MagicMock]:
    """Return (manager, hass_mock, device_mock)."""
    device = MagicMock()
    device.address = "AA:BB:CC:DD:EE:FF"
    device.get_lock_state.return_value = is_locked_return
    device.datapoints.__getitem__.return_value = None  # dp36 = None → delay=10
    device.send_control_datapoint = AsyncMock()
    device.send_command_wait_state_echo = AsyncMock(return_value=True)

    hass = MagicMock()
    hass.async_create_task.side_effect = asyncio.create_task

    config = LockManagerConfig(
        unknown_state_action=unknown_state_action,
        transition_timeout=transition_timeout,
        auto_lock_delay_fallback=30,
        lock_dp_id=46,
        unlock_dp_id=6,
        state_dp_id=47,
        lock_value=True,
        unlock_value=True,
        get_auto_lock=lambda: virtual_auto_lock,
        has_door_sensor=has_door_sensor,
    )

    mgr = GimdowBLELockManager(device, hass, config, MagicMock())
    mgr._last_known_state = last_known_state
    return mgr, hass, device


# ---------------------------------------------------------------------------
# TestOnConnected — transition reset, first-boot None guard, dedup, strategies
# ---------------------------------------------------------------------------


class TestOnConnected:
    def test_clears_transition_state_and_flags(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._transition_state = LockTransitionState.LOCKING

        mgr.on_connected()

        assert mgr._transition_state == LockTransitionState.IDLE
        assert mgr.is_locking is False
        assert mgr.is_unlocking is False
        assert mgr.is_timeout_unknown is False

    def test_confirm_last_no_prior_state_no_task(self) -> None:
        mgr, hass, _ = _make_manager(
            unknown_state_action=UNKNOWN_STATE_ACTION_CONFIRM_LAST,
            last_known_state=None,
        )
        mgr.on_connected()
        hass.async_create_task.assert_not_called()
        assert mgr._resolution_task is None

    def test_confirm_last_with_prior_state_creates_task(self) -> None:
        mgr, hass, _ = _make_manager(
            unknown_state_action=UNKNOWN_STATE_ACTION_CONFIRM_LAST,
            last_known_state=True,
        )
        hass.async_create_task.side_effect = lambda coro, **kw: coro.close()
        mgr.on_connected()
        hass.async_create_task.assert_called_once()

    def test_confirm_last_does_not_duplicate_running_task(self) -> None:
        mgr, hass, _ = _make_manager(
            unknown_state_action=UNKNOWN_STATE_ACTION_CONFIRM_LAST,
            last_known_state=True,
        )
        fake_task = MagicMock()
        fake_task.done.return_value = False
        mgr._resolution_task = fake_task

        mgr.on_connected()

        hass.async_create_task.assert_not_called()

    def test_force_lock_twice_no_prior_state_no_task(self) -> None:
        mgr, hass, _ = _make_manager(
            unknown_state_action=UNKNOWN_STATE_ACTION_FORCE_LOCK_TWICE,
            last_known_state=None,
        )
        mgr.on_connected()
        hass.async_create_task.assert_not_called()
        assert mgr._resolution_task is None

    def test_force_lock_twice_with_prior_state_creates_task(self) -> None:
        mgr, hass, _ = _make_manager(
            unknown_state_action=UNKNOWN_STATE_ACTION_FORCE_LOCK_TWICE,
            last_known_state=False,
        )
        hass.async_create_task.side_effect = lambda coro, **kw: coro.close()
        mgr.on_connected()
        hass.async_create_task.assert_called_once()

    def test_double_on_action_no_task_on_connected(self) -> None:
        mgr, hass, _ = _make_manager(
            unknown_state_action=UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION,
        )
        mgr.on_connected()
        hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# TestCleanup (M5)
# ---------------------------------------------------------------------------


class TestCleanup:
    async def test_cleanup_cancels_transition_timeout_task(self) -> None:
        mgr, _, _ = _make_manager()

        async def _noop():
            await asyncio.sleep(100)

        mgr._transition_timeout_task = asyncio.create_task(_noop())
        assert not mgr._transition_timeout_task.cancelled()

        mgr.cleanup()
        await asyncio.sleep(0)

        assert mgr._transition_timeout_task is None

    async def test_cleanup_cancels_resolution_task(self) -> None:
        mgr, _, _ = _make_manager()

        async def _noop():
            await asyncio.sleep(100)

        mgr._resolution_task = asyncio.create_task(_noop())
        assert not mgr._resolution_task.done()

        mgr.cleanup()
        await asyncio.sleep(0)

        assert mgr._resolution_task is None

    def test_cleanup_cancels_auto_lock_timer(self) -> None:
        mgr, _, _ = _make_manager()
        fake_cancel = MagicMock()
        mgr._auto_lock_timer = fake_cancel

        mgr.cleanup()

        fake_cancel.assert_called_once()
        assert mgr._auto_lock_timer is None

    def test_cleanup_safe_with_no_tasks(self) -> None:
        mgr, _, _ = _make_manager()
        mgr.cleanup()  # must not raise


# ---------------------------------------------------------------------------
# TestAutoLockTimer (N7 — start_auto_lock_timer guards)
# ---------------------------------------------------------------------------


class TestAutoLockTimer:
    def test_not_started_when_door_state_unknown(self) -> None:
        """start_auto_lock_timer skips when door state has never been reported (N7)."""
        mgr, _, _ = _make_manager(virtual_auto_lock=True, is_locked_return=False)
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ) as mock_acl:
            mgr.start_auto_lock_timer()
            mock_acl.assert_not_called()
        assert mgr._auto_lock_timer is None

    def test_not_started_when_is_locked_none(self) -> None:
        """start_auto_lock_timer skips when lock state is None (N7)."""
        mgr, _, _ = _make_manager(virtual_auto_lock=True, is_locked_return=None)
        mgr.on_door_changed(False)  # mark door state known
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ) as mock_acl:
            mgr.start_auto_lock_timer()
            mock_acl.assert_not_called()
        assert mgr._auto_lock_timer is None

    def test_not_started_when_already_locked(self) -> None:
        mgr, _, _ = _make_manager(virtual_auto_lock=True, is_locked_return=True)
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ) as mock_acl:
            mgr.start_auto_lock_timer()
            mock_acl.assert_not_called()
        assert mgr._auto_lock_timer is None

    def test_not_started_when_auto_lock_disabled(self) -> None:
        mgr, _, _ = _make_manager(virtual_auto_lock=False, is_locked_return=False)
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ) as mock_acl:
            mgr.start_auto_lock_timer()
            mock_acl.assert_not_called()

    def test_started_when_unlocked_and_door_known(self) -> None:
        mgr, _, _ = _make_manager(virtual_auto_lock=True, is_locked_return=False)
        mgr.on_door_changed(False)  # mark door state known
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ) as mock_acl:
            mock_acl.return_value = MagicMock()
            mgr.start_auto_lock_timer()
            mock_acl.assert_called_once()
        assert mgr._auto_lock_timer is not None


# ---------------------------------------------------------------------------
# TestAutoLockProperty (N4)
# ---------------------------------------------------------------------------


class TestAutoLockProperty:
    def test_false_with_no_timer(self) -> None:
        mgr, _, _ = _make_manager()
        assert mgr.auto_lock_timer_active is False

    def test_true_when_timer_set(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._auto_lock_timer = MagicMock()
        assert mgr.auto_lock_timer_active is True

    def test_false_after_stop(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._auto_lock_timer = MagicMock()
        mgr.stop_auto_lock_timer()
        assert mgr.auto_lock_timer_active is False


# ---------------------------------------------------------------------------
# TestAutoLockSettingChanged
# ---------------------------------------------------------------------------


class TestAutoLockSettingChanged:
    def test_calls_start_auto_lock_timer(self) -> None:
        """on_auto_lock_setting_changed must call start_auto_lock_timer."""
        mgr, _, _ = _make_manager()
        mgr.start_auto_lock_timer = MagicMock()

        mgr.on_auto_lock_setting_changed()

        mgr.start_auto_lock_timer.assert_called_once()


# ---------------------------------------------------------------------------
# TestAutoLockTimeChanged
# ---------------------------------------------------------------------------


class TestAutoLockTimeChanged:
    def test_calls_start_auto_lock_timer(self) -> None:
        """on_auto_lock_time_changed must call start_auto_lock_timer."""
        mgr, _, _ = _make_manager()
        mgr.start_auto_lock_timer = MagicMock()

        mgr.on_auto_lock_time_changed()

        mgr.start_auto_lock_timer.assert_called_once()


# ---------------------------------------------------------------------------
# TestUpdateAttribution (S7)
# ---------------------------------------------------------------------------


class TestUpdateAttribution:
    def test_none_current_returns_false_none(self) -> None:
        """update_attribution short-circuits when current_is_locked is None (S7)."""
        mgr, _, _ = _make_manager()
        changed, by = mgr.update_attribution(
            current_is_locked=None, last_is_locked=False
        )
        assert changed is False
        assert by is None

    def test_no_transition_when_state_same(self) -> None:
        mgr, _, _ = _make_manager()
        changed, by = mgr.update_attribution(
            current_is_locked=True, last_is_locked=True
        )
        assert changed is False
        assert by is None

    def test_no_transition_when_last_is_none(self) -> None:
        mgr, _, _ = _make_manager()
        changed, by = mgr.update_attribution(
            current_is_locked=True, last_is_locked=None
        )
        assert changed is False
        assert by is None

    def test_auto_source_returns_auto_lock_string(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._pending_action_source = ACTION_SOURCE_AUTO
        changed, by = mgr.update_attribution(
            current_is_locked=True, last_is_locked=False
        )
        assert changed is True
        assert by == "Auto Lock"

    def test_ha_source_returns_none(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._pending_action_source = ACTION_SOURCE_HA
        changed, by = mgr.update_attribution(
            current_is_locked=True, last_is_locked=False
        )
        assert changed is True
        assert by is None

    def test_manual_source_returns_manual_string(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._pending_action_source = None
        changed, by = mgr.update_attribution(
            current_is_locked=True, last_is_locked=False
        )
        assert changed is True
        assert by == "Manual"

    def test_clears_pending_source_after_transition(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._pending_action_source = ACTION_SOURCE_AUTO
        mgr.update_attribution(current_is_locked=False, last_is_locked=True)
        assert mgr._pending_action_source is None


# ---------------------------------------------------------------------------
# TestDoubleCommandToState (S6)
# ---------------------------------------------------------------------------


class TestDoubleCommandToState:
    async def test_echo_timeout_sets_timeout_unknown(self) -> None:
        """is_timeout_unknown=True when the single echo wait times out (S6)."""
        mgr, _, device = _make_manager(is_locked_return=None)
        device.send_command_wait_state_echo = AsyncMock(return_value=False)

        await mgr._double_command_to_state(True)

        assert mgr.is_timeout_unknown is True

    async def test_echo_success_clears_flags(self) -> None:
        """is_timeout_unknown=False and transition cleared when echo confirms target state."""
        mgr, _, device = _make_manager(
            is_locked_return=True
        )  # device is locked after echo
        device.send_command_wait_state_echo = AsyncMock(return_value=True)

        await mgr._double_command_to_state(True)

        assert mgr.is_timeout_unknown is False
        assert mgr.is_locking is False
        assert mgr.is_unlocking is False

    async def test_cancelled_error_raises_and_cleans_up(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=None)
        mgr._transition_state = LockTransitionState.LOCKING
        device.send_command_wait_state_echo = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with pytest.raises(asyncio.CancelledError):
            await mgr._double_command_to_state(True)

        assert mgr.is_locking is False
        assert mgr.is_unlocking is False

    async def test_exception_cleans_up_flags(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=None)
        device.send_command_wait_state_echo = AsyncMock(
            side_effect=RuntimeError("fail")
        )

        await mgr._double_command_to_state(True)  # must not raise

        assert mgr.is_locking is False
        assert mgr.is_unlocking is False

    async def test_unlock_starts_auto_lock_timer(self) -> None:
        mgr, _, device = _make_manager(virtual_auto_lock=True, is_locked_return=False)
        device.send_command_wait_state_echo = AsyncMock(return_value=True)
        mgr.start_auto_lock_timer = MagicMock()

        await mgr._double_command_to_state(target_lock=False)

        mgr.start_auto_lock_timer.assert_called()

    async def test_force_two_attempts_always_sends_second_command(self) -> None:
        """force_two_attempts=True must send both commands even when attempt 1 reaches target."""
        mgr, _, device = _make_manager(is_locked_return=True)
        device.send_command_wait_state_echo = AsyncMock(return_value=True)

        await mgr._double_command_to_state(True, force_two_attempts=True)

        assert device.send_command_wait_state_echo.call_count == 2

    async def test_without_force_two_attempts_exits_after_first_success(self) -> None:
        """Without force_two_attempts, successful attempt 1 exits early."""
        mgr, _, device = _make_manager(is_locked_return=True)
        device.send_command_wait_state_echo = AsyncMock(return_value=True)

        await mgr._double_command_to_state(True, force_two_attempts=False)

        assert device.send_command_wait_state_echo.call_count == 1


# ---------------------------------------------------------------------------
# TestOnCoordinatorUpdate
# ---------------------------------------------------------------------------


class TestOnCoordinatorUpdate:
    def test_locked_clears_is_locking(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._transition_state = LockTransitionState.LOCKING
        mgr.on_coordinator_update(True)
        assert mgr.is_locking is False

    def test_locked_does_not_clear_is_unlocking(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._transition_state = LockTransitionState.UNLOCKING
        mgr.on_coordinator_update(True)
        assert mgr.is_unlocking is True

    def test_unlocked_clears_is_unlocking(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._transition_state = LockTransitionState.UNLOCKING
        mgr.on_coordinator_update(False)
        assert mgr.is_unlocking is False

    def test_unlocked_does_not_clear_is_locking(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._transition_state = LockTransitionState.LOCKING
        mgr.on_coordinator_update(False)
        assert mgr.is_locking is True

    def test_none_leaves_flags_unchanged(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._transition_state = LockTransitionState.LOCKING
        mgr._last_known_state = True
        mgr.on_coordinator_update(None)
        assert mgr.is_locking is True
        assert mgr._last_known_state is True

    def test_updates_last_known_state(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._last_known_state = None
        mgr.on_coordinator_update(True)
        assert mgr._last_known_state is True

    def test_none_does_not_update_last_known_state(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._last_known_state = True
        mgr.on_coordinator_update(None)
        assert mgr._last_known_state is True

    def test_safety_net_restarts_timer_when_unlocked(self) -> None:
        mgr, _, _ = _make_manager(virtual_auto_lock=True)
        mgr._auto_lock_timer = None
        mgr._is_door_open = False
        mgr.start_auto_lock_timer = MagicMock()

        mgr.on_coordinator_update(is_locked=False)

        mgr.start_auto_lock_timer.assert_called()

    def test_safety_net_skipped_if_timer_exists(self) -> None:
        mgr, _, _ = _make_manager(virtual_auto_lock=True)
        mgr._auto_lock_timer = MagicMock()
        mgr.start_auto_lock_timer = MagicMock()

        mgr.on_coordinator_update(is_locked=False)

        mgr.start_auto_lock_timer.assert_not_called()

    def test_safety_net_skipped_if_locked(self) -> None:
        mgr, _, _ = _make_manager(virtual_auto_lock=True)
        mgr._auto_lock_timer = None
        mgr.start_auto_lock_timer = MagicMock()

        mgr.on_coordinator_update(is_locked=True)

        mgr.start_auto_lock_timer.assert_not_called()

    def test_safety_net_skipped_if_door_open(self) -> None:
        mgr, _, _ = _make_manager(virtual_auto_lock=True, has_door_sensor=True)
        mgr._auto_lock_timer = None
        mgr._is_door_open = True
        mgr.start_auto_lock_timer = MagicMock()

        mgr.on_coordinator_update(is_locked=False)

        mgr.start_auto_lock_timer.assert_not_called()


# ---------------------------------------------------------------------------
# TestOnDoorChanged
# ---------------------------------------------------------------------------


class TestOnDoorChanged:
    async def test_door_close_with_pending_creates_lock_task(self) -> None:
        """Door closes while pending → lock() task created, pending cleared."""
        mgr, hass, _ = _make_manager()
        mgr._pending.set(LockBlockedReason.DOOR_OPEN_PENDING)
        assert mgr._pending.active

        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ):
            mgr.on_door_changed(is_open=False)

        hass.async_create_task.assert_called_once()
        assert not mgr._pending.active

    def test_door_close_without_pending_no_lock_task(self) -> None:
        """Door closes with no pending intent → no lock() task created."""
        mgr, hass, _ = _make_manager()
        assert not mgr._pending.active

        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ):
            mgr.on_door_changed(is_open=False)

        hass.async_create_task.assert_not_called()

    def test_door_closed_calls_start_auto_lock_timer(self) -> None:
        mgr, _, _ = _make_manager()
        mgr.start_auto_lock_timer = MagicMock()

        mgr.on_door_changed(False)

        mgr.start_auto_lock_timer.assert_called_once()

    def test_door_open_with_pending_does_not_call_lock(self) -> None:
        mgr, hass, _ = _make_manager()
        mgr._pending.set(LockBlockedReason.DOOR_OPEN_PENDING)

        mgr.on_door_changed(True)

        hass.async_create_task.assert_not_called()

    def test_door_open_sets_door_state(self) -> None:
        mgr, _, _ = _make_manager()
        mgr.on_door_changed(is_open=True)
        assert mgr._is_door_open is True

    def test_door_close_sets_door_state(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._is_door_open = True
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ):
            mgr.on_door_changed(is_open=False)
        assert mgr._is_door_open is False


# ---------------------------------------------------------------------------
# TestAutoLockTimerLifecycle
# ---------------------------------------------------------------------------


class TestAutoLockTimerLifecycle:
    def test_stop_timer_safe_when_none(self) -> None:
        mgr, _, _ = _make_manager()
        mgr.stop_auto_lock_timer()  # must not raise

    def test_stop_cancels_running_timer(self) -> None:
        mgr, _, _ = _make_manager()
        cancel_fn = MagicMock()
        mgr._auto_lock_timer = cancel_fn
        mgr.stop_auto_lock_timer()
        cancel_fn.assert_called_once()
        assert mgr._auto_lock_timer is None

    def test_start_then_stop(self) -> None:
        mgr, _, _ = _make_manager(virtual_auto_lock=True, is_locked_return=False)
        mgr.on_door_changed(False)  # mark door state known
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ) as mock_acl:
            cancel_fn = MagicMock()
            mock_acl.return_value = cancel_fn
            mgr.start_auto_lock_timer()
            assert mgr._auto_lock_timer is not None

            mgr.stop_auto_lock_timer()
            cancel_fn.assert_called_once()
            assert mgr._auto_lock_timer is None


# ---------------------------------------------------------------------------
# TestAutoLockCallback
# ---------------------------------------------------------------------------


class TestAutoLockCallback:
    async def test_already_locked_skips_lock(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=True)
        device.send_command_wait_state_echo = AsyncMock()

        await mgr._auto_lock_callback(None)

        device.send_command_wait_state_echo.assert_not_awaited()

    async def test_not_locked_calls_lock(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False)
        device.send_control_datapoint = AsyncMock()

        await mgr._auto_lock_callback(None)

        device.send_control_datapoint.assert_awaited()

    async def test_resets_auto_lock_timer_reference(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=True)
        mgr._auto_lock_timer = MagicMock()

        await mgr._auto_lock_callback(None)

        assert mgr._auto_lock_timer is None


# ---------------------------------------------------------------------------
# TestPendingLockIntent
# ---------------------------------------------------------------------------


class TestPendingLockIntent:
    def test_initially_inactive(self) -> None:
        p = PendingLockIntent(MagicMock())
        assert not p.active
        assert not bool(p)

    def test_set_makes_active(self) -> None:
        cb = MagicMock()
        p = PendingLockIntent(cb)
        p.set(LockBlockedReason.DOOR_OPEN_PENDING)
        assert p.active
        assert p.reason == LockBlockedReason.DOOR_OPEN_PENDING
        cb.assert_called_once()

    def test_clear_makes_inactive(self) -> None:
        cb = MagicMock()
        p = PendingLockIntent(cb)
        p.set(LockBlockedReason.DOOR_OPEN_PENDING)
        p.clear()
        assert not p.active

    def test_clear_noop_when_already_clear(self) -> None:
        cb = MagicMock()
        p = PendingLockIntent(cb)
        p.clear()  # must not raise or call cb
        cb.assert_not_called()

    def test_should_auto_execute_false_when_auto_lock_active(self) -> None:
        p = PendingLockIntent(MagicMock())
        p.set(LockBlockedReason.DOOR_OPEN_PENDING)
        assert p.should_auto_execute(auto_lock_active=True) is False

    def test_should_auto_execute_true_when_auto_lock_inactive(self) -> None:
        p = PendingLockIntent(MagicMock())
        p.set(LockBlockedReason.DOOR_OPEN_PENDING)
        assert p.should_auto_execute(auto_lock_active=False) is True


# ---------------------------------------------------------------------------
# TestLockNormalPath
# ---------------------------------------------------------------------------


class TestLockNormalPath:
    async def test_lock_sets_is_locking_true(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False)
        await mgr.lock()
        assert mgr.is_locking is True

    async def test_lock_sends_control_datapoint(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False)
        await mgr.lock()
        device.send_control_datapoint.assert_awaited_once_with(46, True)

    async def test_lock_sets_action_source_ha(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False)
        await mgr.lock()
        assert mgr._pending_action_source == ACTION_SOURCE_HA

    async def test_lock_calls_stop_auto_lock_timer_first(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False)
        cancel_fn = MagicMock()
        mgr._auto_lock_timer = cancel_fn
        await mgr.lock()
        cancel_fn.assert_called_once()

    async def test_lock_door_open_sets_pending_not_locking(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False, has_door_sensor=True)
        mgr._is_door_open = True
        mgr._door_state_known = True
        await mgr.lock()
        assert mgr._pending.active
        assert mgr._pending.reason == LockBlockedReason.DOOR_OPEN_PENDING
        assert mgr.is_locking is False
        device.send_control_datapoint.assert_not_awaited()

    async def test_lock_exception_clears_is_locking(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False)
        device.send_control_datapoint = AsyncMock(side_effect=Exception("BLE error"))
        with pytest.raises(Exception, match="BLE error"):
            await mgr.lock()
        assert mgr.is_locking is False

    async def test_lock_already_locked_skips_command(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=True)
        await mgr.lock()
        device.send_control_datapoint.assert_not_awaited()
        assert mgr.is_locking is False


# ---------------------------------------------------------------------------
# TestUnlockNormalPath
# ---------------------------------------------------------------------------


class TestUnlockNormalPath:
    async def test_unlock_sets_is_unlocking_true(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=True)
        await mgr.unlock()
        assert mgr.is_unlocking is True

    async def test_unlock_sends_control_datapoint(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=True)
        await mgr.unlock()
        device.send_control_datapoint.assert_awaited_once_with(6, True)

    async def test_unlock_sets_action_source_ha(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=True)
        await mgr.unlock()
        assert mgr._pending_action_source == ACTION_SOURCE_HA

    async def test_unlock_with_active_pending_clears_pending_only(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False)
        mgr._pending.set(LockBlockedReason.DOOR_OPEN_PENDING)
        await mgr.unlock()
        assert not mgr._pending.active
        assert mgr.is_unlocking is False
        device.send_control_datapoint.assert_not_awaited()

    async def test_unlock_exception_clears_is_unlocking(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=True)
        device.send_control_datapoint = AsyncMock(side_effect=Exception("BLE error"))
        with pytest.raises(Exception, match="BLE error"):
            await mgr.unlock()
        assert mgr.is_unlocking is False

    async def test_unlock_starts_auto_lock_timer_on_success(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=True, virtual_auto_lock=True)
        mgr.on_door_changed(False)
        # First call: True (so unlock() sends the command); subsequent calls: False
        # (so start_auto_lock_timer() sees the device as unlocked and starts the timer).
        device.get_lock_state.side_effect = [True, False]
        with patch(
            "custom_components.gimdow_ble.gimdow_ble.lock_manager.async_call_later"
        ) as mock_acl:
            mock_acl.return_value = MagicMock()
            await mgr.unlock()
        mock_acl.assert_called_once()


# ---------------------------------------------------------------------------
# TestTransitionTimeout
# ---------------------------------------------------------------------------


class TestTransitionTimeout:
    def test_zero_timeout_creates_no_task(self) -> None:
        mgr, _, _ = _make_manager(transition_timeout=0)
        mgr._start_transition_timeout()
        assert mgr._transition_timeout_task is None

    async def test_positive_timeout_creates_task(self) -> None:
        mgr, _, _ = _make_manager(transition_timeout=1)
        mgr._start_transition_timeout()
        assert mgr._transition_timeout_task is not None
        mgr._cancel_transition_timeout()

    async def test_timeout_fires_sets_unknown_and_clears_flags(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False, transition_timeout=0.05)
        await mgr.lock()
        assert mgr.is_locking is True
        assert mgr._transition_timeout_task is not None
        await asyncio.sleep(0.15)
        assert mgr.is_timeout_unknown is True
        assert mgr.is_locking is False
        assert mgr.is_unlocking is False

    async def test_cancel_transition_timeout_safe_when_none(self) -> None:
        mgr, _, _ = _make_manager()
        mgr._cancel_transition_timeout()

    async def test_coordinator_update_cancels_timeout_task(self) -> None:
        mgr, _, device = _make_manager(is_locked_return=False, transition_timeout=60)
        await mgr.lock()
        assert mgr._transition_timeout_task is not None
        mgr.on_coordinator_update(True)
        assert mgr.is_locking is False
        assert mgr._transition_timeout_task is None


# ---------------------------------------------------------------------------
# TestLockUnknownState
# ---------------------------------------------------------------------------


class TestLockUnknownState:
    async def test_double_action_creates_resolution_task(self) -> None:
        mgr, _, device = _make_manager(
            is_locked_return=None,
            unknown_state_action=UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION,
        )
        device.send_command_wait_state_echo = AsyncMock(return_value=True)
        await mgr.lock()
        assert mgr._resolution_task is not None

    async def test_double_action_deduplicates_running_task(self) -> None:
        mgr, hass, device = _make_manager(
            is_locked_return=None,
            unknown_state_action=UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION,
        )
        device.send_command_wait_state_echo = AsyncMock(return_value=True)
        await mgr.lock()
        first_task = mgr._resolution_task
        device.get_lock_state.return_value = None
        await mgr.lock()
        assert mgr._resolution_task is first_task

    async def test_confirm_last_in_unknown_state_is_ignored(self) -> None:
        mgr, hass, device = _make_manager(
            is_locked_return=None,
            unknown_state_action=UNKNOWN_STATE_ACTION_CONFIRM_LAST,
        )
        await mgr.lock()
        assert mgr._resolution_task is None
        device.send_control_datapoint.assert_not_awaited()

    async def test_unlock_unknown_double_action_creates_resolution_task(self) -> None:
        mgr, _, device = _make_manager(
            is_locked_return=None,
            unknown_state_action=UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION,
        )
        device.send_command_wait_state_echo = AsyncMock(return_value=True)
        await mgr.unlock()
        assert mgr._resolution_task is not None
