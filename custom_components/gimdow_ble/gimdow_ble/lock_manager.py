"""Gimdow BLE lock manager — lock state machine, auto-lock timer, and pending intent.

``GimdowBLELockManager`` owns all lock business logic. The HA ``GimdowBLELock``
entity is a thin delegate: it calls into this class for every lock/unlock
operation and reads properties back for HA state.

No direct HA entity code lives here. The only HA dependency is:
  - ``homeassistant.helpers.event.async_call_later`` (auto-lock timer)
  - ``homeassistant.core.HomeAssistant`` (for async task creation)
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..const import (
    ACTION_SOURCE_AUTO,
    ACTION_SOURCE_HA,
    UNKNOWN_STATE_ACTION_SKIP,
    UNKNOWN_STATE_ACTION_FORCE_LOCK,
    UNKNOWN_STATE_ACTION_RESOLVE,
)

if TYPE_CHECKING:
    from .device import GimdowBLEDevice
    from ..devices import GimdowBLEData

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Blocked-reason model
# ---------------------------------------------------------------------------

class LockBlockedReason(Enum):
    """Why the lock cannot currently execute a lock command."""

    NONE              = "none"
    DOOR_OPEN_PENDING = "door_open_pending"  # HA asked to lock while door open → waiting
    DOOR_OPEN_LOCKED  = "door_open_locked"   # Lock engaged but door is physically open


class PendingLockIntent:
    """Encapsulates a deferred lock request.

    Every state transition automatically calls ``on_state_change`` so the
    HA entity never has to call ``async_write_ha_state()`` manually after
    set/clear. ``async_write_ha_state`` is passed as ``on_state_change``.
    """

    def __init__(self, on_state_change: Callable[[], None]) -> None:
        self._reason: LockBlockedReason = LockBlockedReason.NONE
        self._on_state_change = on_state_change

    # --- Public interface ---------------------------------------------------

    @property
    def active(self) -> bool:
        return self._reason != LockBlockedReason.NONE

    @property
    def reason(self) -> LockBlockedReason:
        return self._reason

    def set(self, reason: LockBlockedReason) -> None:
        """Activate with reason and notify HA."""
        self._reason = reason
        self._on_state_change()

    def clear(self) -> None:
        """Clear and notify HA (no-op if already clear)."""
        if self.active:
            self._reason = LockBlockedReason.NONE
            self._on_state_change()

    def should_auto_execute(self, auto_lock_active: bool) -> bool:
        """True if the door-close callback must fire a manual lock.

        Returns False when auto-lock is on — auto-lock will handle it,
        preventing a race condition where both paths call async_lock().
        """
        return self.active and not auto_lock_active

    def __bool__(self) -> bool:
        return self.active

    def __repr__(self) -> str:
        return f"PendingLockIntent({self._reason.value})"


# ---------------------------------------------------------------------------
# Lock manager
# ---------------------------------------------------------------------------

class GimdowBLELockManager:
    """Manages the Gimdow BLE lock state machine.

    Responsibilities:
    - Execute lock / unlock commands (with unknown-state handling)
    - Manage auto-lock timer
    - Track pending lock intent and blocked reason
    - Coordinate door-sensor changes with lock decisions
    - Track attribution (changed_by)
    """

    def __init__(
        self,
        device: GimdowBLEDevice,
        hass: HomeAssistant,
        data: GimdowBLEData,
        mapping,  # GimdowBLELockMapping — imported at call site to avoid circular
        on_state_change: Callable[[], None],
    ) -> None:
        self._device = device
        self._hass = hass
        self._data = data
        self._mapping = mapping
        self._on_state_change = on_state_change

        # Transition flags read by the HA entity for is_locking / is_unlocking
        self._is_locking: bool = False
        self._is_unlocking: bool = False
        self._is_timeout_unknown: bool = False

        # Deferred lock intent
        self._pending = PendingLockIntent(on_state_change)

        # Auto-lock timer handle
        self._auto_lock_timer = None
        
        # Transition timeout handle
        self._transition_timeout_task: asyncio.Task | None = None

        # Door state mirror (updated by the HA entity via on_door_changed)
        self._is_door_open: bool = False

        # Attribution tracking
        self._pending_action_source: str | None = None

        # Background resolution task
        self._resolution_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Properties consumed by the HA entity
    # ------------------------------------------------------------------

    @property
    def is_locking(self) -> bool:
        return self._is_locking

    @property
    def is_unlocking(self) -> bool:
        return self._is_unlocking

    @property
    def is_timeout_unknown(self) -> bool:
        return self._is_timeout_unknown

    @property
    def pending(self) -> PendingLockIntent:
        return self._pending

    @property
    def is_door_open(self) -> bool:
        return self._is_door_open

    @property
    def pending_action_source(self) -> str | None:
        return self._pending_action_source

    # ------------------------------------------------------------------
    # External event hooks (called by HA entity dispatchers)
    # ------------------------------------------------------------------

    def on_door_changed(self, is_open: bool) -> None:
        """Door sensor state changed."""
        prev = self._is_door_open
        self._is_door_open = is_open
        _LOGGER.debug(
            "[%s] Door: %s → %s. pending=%s auto_lock=%s",
            self._device.address, prev, is_open,
            self._pending, self._data.virtual_auto_lock,
        )

        if not is_open:
            if self._pending.active:
                _LOGGER.debug(
                    "[%s] Door closed — clearing pending intent and locking.",
                    self._device.address, self._data.virtual_auto_lock,
                )
                self._hass.async_create_task(self.lock())
                self._pending.clear()  # always clear so HA UI shows 'Unlocked', not 'jammed'

        self.start_auto_lock_timer()

    def on_auto_lock_setting_changed(self) -> None:
        """Virtual auto-lock switch toggled."""
        _LOGGER.debug("[%s] Auto-lock setting changed: %s", self._device.address, self._data.virtual_auto_lock)
        self.start_auto_lock_timer()

    def on_auto_lock_time_changed(self) -> None:
        """Auto-lock delay (DP 36) changed."""
        _LOGGER.debug("[%s] Auto-lock delay changed.", self._device.address)
        self.start_auto_lock_timer()

    def on_coordinator_update(self, is_locked: bool | None) -> None:
        """Called by the HA entity after every coordinator update."""
        if is_locked is False:
            self._is_unlocking = False
            self._is_locking = False
            self._is_timeout_unknown = False
            self._cancel_transition_timeout()
        if is_locked is True:
            self._is_locking = False
            self._is_unlocking = False
            self._is_timeout_unknown = False
            self._cancel_transition_timeout()

        # Safety net: if unlocked with no timer and auto-lock is on, restart timer
        if (
            self._data.virtual_auto_lock
            and not is_locked
            and not self._is_door_open
            and self._auto_lock_timer is None
        ):
            _LOGGER.debug("[%s] Coordinator: unlocked without timer → restarting.", self._device.address)
            self.start_auto_lock_timer()

    def update_attribution(
        self, current_is_locked: bool | None, last_is_locked: bool | None
    ) -> str | None:
        """Return the new changed_by string when a state transition occurs, else None."""
        if last_is_locked is not None and current_is_locked != last_is_locked:
            if self._pending_action_source == ACTION_SOURCE_AUTO:
                changed_by: str | None = "Auto Lock"
            elif self._pending_action_source == ACTION_SOURCE_HA:
                changed_by = None
            else:
                changed_by = "Manual"
            _LOGGER.debug(
                "[%s] Attribution: %s → %s. source=%s. changed_by=%s",
                self._device.address, last_is_locked, current_is_locked,
                self._pending_action_source, changed_by,
            )
            self._pending_action_source = None
            return changed_by
        return None  # no transition

    # ------------------------------------------------------------------
    # Main lock / unlock operations
    # ------------------------------------------------------------------

    async def lock(self) -> None:
        """Execute a lock command through the full state machine."""
        self.stop_auto_lock_timer()
        is_locked = self._device.get_lock_state(self._mapping.state_dp_id)

        _LOGGER.debug(
            "[%s] lock() called. is_locked=%s door_open=%s auto_lock=%s pending=%s",
            self._device.address, is_locked, self._is_door_open,
            self._data.virtual_auto_lock, self._pending,
        )

        # --- Unknown state ---
        if is_locked is None:
            await self._handle_unknown_state(target_lock=True)
            return

        # --- Door open ---
        if self._is_door_open:
            _LOGGER.debug(
                "[%s] Door open → setting pending lock intent.",
                self._device.address,
            )
            self._pending.set(LockBlockedReason.DOOR_OPEN_PENDING)
            return

        # --- Normal lock ---
        self._pending.clear()
        self._is_locking = True
        self._is_timeout_unknown = False
        self._start_transition_timeout()
        self._on_state_change()
        try:
            dp = await self._device.send_control_datapoint(
                self._mapping.lock_dp_id, self._mapping.lock_value
            )
            if dp and self._pending_action_source != ACTION_SOURCE_AUTO:
                self._pending_action_source = ACTION_SOURCE_HA
        except Exception as e:
            _LOGGER.error(
                "[%s] lock() failed: %s. pending=%s locking=%s door=%s",
                self._device.address, e, self._pending, self._is_locking, self._is_door_open,
            )
            self._is_locking = False
            self._cancel_transition_timeout()
            self._on_state_change()

    async def unlock(self) -> None:
        """Execute an unlock command."""
        is_locked = self._device.get_lock_state(self._mapping.state_dp_id)

        _LOGGER.debug(
            "[%s] unlock() called. is_locked=%s pending=%s",
            self._device.address, is_locked, self._pending,
        )

        # If pending intent is waiting → cancelling unlock = clear the intent
        if self._pending:
            _LOGGER.debug("[%s] Unlock clears pending intent.", self._device.address)
            self._pending.clear()
            return

        # --- Unknown state ---
        if is_locked is None:
            await self._handle_unknown_state(target_lock=False)
            return

        # --- Normal unlock ---
        self._is_unlocking = True
        self._is_timeout_unknown = False
        self._start_transition_timeout()
        self._on_state_change()
        try:
            dp = await self._device.send_control_datapoint(
                self._mapping.unlock_dp_id, self._mapping.unlock_value
            )
            if dp:
                self._pending_action_source = ACTION_SOURCE_HA
                self.start_auto_lock_timer()
        except Exception as e:
            _LOGGER.error(
                "[%s] unlock() failed: %s. unlocking=%s", self._device.address, e, self._is_unlocking
            )
            self._is_unlocking = False
            self._cancel_transition_timeout()
            self._on_state_change()

    # ------------------------------------------------------------------
    # Unknown state handling
    # ------------------------------------------------------------------

    async def _handle_unknown_state(self, target_lock: bool) -> None:
        action = self._data.unknown_state_action

        if action == UNKNOWN_STATE_ACTION_SKIP:
            _LOGGER.warning(
                "[%s] State=Unknown + unknown_state_action=skip. Waiting for next poll.",
                self._device.address,
            )
            return

        if action == UNKNOWN_STATE_ACTION_FORCE_LOCK and target_lock:
            _LOGGER.warning(
                "[%s] State=Unknown + unknown_state_action=force_lock. Sending lock DP directly.",
                self._device.address,
            )
            self._is_locking = True
            self._is_timeout_unknown = False
            self._start_transition_timeout()
            self._on_state_change()
            try:
                await self._device.send_control_datapoint(
                    self._mapping.lock_dp_id, self._mapping.lock_value
                )
            except Exception as e:
                _LOGGER.error(
                    "[%s] force_lock failed: %s", self._device.address, e
                )
                self._is_locking = False
                self._cancel_transition_timeout()
                self._on_state_change()
            return

        # Default: "resolve"
        if self._resolution_task and not self._resolution_task.done():
            _LOGGER.debug("[%s] Resolution already in progress. Ignoring.", self._device.address)
            return

        _LOGGER.warning(
            "[%s] State=Unknown → starting background resolution. target=%s",
            self._device.address, "LOCK" if target_lock else "UNLOCK",
        )
        self._resolution_task = self._hass.async_create_task(
            self._resolve_unknown_state(target_lock)
        )

    async def _resolve_unknown_state(self, target_lock: bool) -> None:
        """Background task: brute-force cycle to reach a known state."""
        if target_lock and self._is_door_open:
            _LOGGER.warning(
                "[%s] Resolution: door open during LOCK target → unlocking and setting pending.",
                self._device.address,
            )
            await self.unlock()
            self._pending.set(LockBlockedReason.DOOR_OPEN_PENDING)
            return

        self._is_locking = target_lock
        self._is_unlocking = not target_lock
        self._on_state_change()

        try:
            await self._device.resolve_unknown_state(
                unlock_dp_id=self._mapping.unlock_dp_id,
                unlock_value=self._mapping.unlock_value,
                state_dp_id=self._mapping.state_dp_id,
                lock_dp_id=self._mapping.lock_dp_id if target_lock else None,
                lock_value=self._mapping.lock_value if target_lock else None,
                target_lock=target_lock,
            )
        except asyncio.CancelledError:
            _LOGGER.debug("[%s] Resolution task cancelled.", self._device.address)
            raise
        except Exception as e:
            _LOGGER.error("[%s] Resolution failed: %s", self._device.address, e)
        finally:
            self._is_locking = False
            self._is_unlocking = False
            if not target_lock:
                self.start_auto_lock_timer()
            self._on_state_change()

    # ------------------------------------------------------------------
    # Auto-lock timer
    # ------------------------------------------------------------------

    def start_auto_lock_timer(self) -> None:
        """Start or restart the auto-lock countdown if conditions are met."""
        self.stop_auto_lock_timer()

        is_locked = self._device.get_lock_state(self._mapping.state_dp_id)
        _LOGGER.debug(
            "[%s] Auto-lock eval: enabled=%s door_open=%s is_locked=%s",
            self._device.address, self._data.virtual_auto_lock,
            self._is_door_open, is_locked,
        )

        if not self._data.virtual_auto_lock:
            return
        if is_locked:
            _LOGGER.debug("[%s] Auto-lock: already locked — timer not started.", self._device.address)
            return

        delay = 10
        dp36 = self._device.datapoints[36]
        if dp36 and dp36.value:
            delay = int(dp36.value)

        _LOGGER.debug("[%s] Auto-lock: starting timer for %ss.", self._device.address, delay)
        self._auto_lock_timer = async_call_later(
            self._hass, delay, self._auto_lock_callback
        )

    def stop_auto_lock_timer(self) -> None:
        """Cancel any running auto-lock timer."""
        if self._auto_lock_timer:
            self._auto_lock_timer()
            self._auto_lock_timer = None
            _LOGGER.debug("[%s] Auto-lock: timer cancelled.", self._device.address)

    async def _auto_lock_callback(self, _now) -> None:
        """Fired when the auto-lock timer expires."""
        self._auto_lock_timer = None
        is_locked = self._device.get_lock_state(self._mapping.state_dp_id)
        if is_locked:
            _LOGGER.debug("[%s] Auto-lock: already locked — skipping.", self._device.address)
            return
        _LOGGER.debug("[%s] Auto-lock: timer expired → locking.", self._device.address)
        self._pending_action_source = ACTION_SOURCE_AUTO
        await self.lock()

    # ------------------------------------------------------------------
    # Transition Timeout Timer
    # ------------------------------------------------------------------

    def _start_transition_timeout(self) -> None:
        self._cancel_transition_timeout()
        timeout = self._data.transition_timeout
        if timeout <= 0:
            return

        async def _timeout_task():
            await asyncio.sleep(timeout)
            _LOGGER.warning("[%s] Transition timeout reached. Marking state as Unknown.", self._device.address)
            self._is_locking = False
            self._is_unlocking = False
            self._is_timeout_unknown = True
            self._on_state_change()

        self._transition_timeout_task = self._hass.async_create_task(_timeout_task())

    def _cancel_transition_timeout(self) -> None:
        if self._transition_timeout_task:
            self._transition_timeout_task.cancel()
            self._transition_timeout_task = None

