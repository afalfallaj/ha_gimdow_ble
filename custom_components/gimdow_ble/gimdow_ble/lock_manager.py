"""Gimdow BLE lock manager — lock state machine, auto-lock timer, and pending intent.

``GimdowBLELockManager`` owns all lock business logic. The HA ``GimdowBLELock``
entity is a thin delegate: it calls into this class for every lock/unlock
operation and reads properties back for HA state.

No direct HA entity code lives here. The only HA dependency is:
  - ``homeassistant.helpers.event.async_call_later`` (auto-lock timer)
  - ``homeassistant.components.persistent_notification`` (misconfiguration alerts)
  - ``homeassistant.core.HomeAssistant`` (for async task creation)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
    async_dismiss as pn_async_dismiss,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..const import (
    ACTION_SOURCE_AUTO,
    ACTION_SOURCE_HA,
    UNKNOWN_STATE_ACTION_CONFIRM_LAST,
    UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION,
    UNKNOWN_STATE_ACTION_FORCE_LOCK_TWICE,
)

from .const import DP_AUTO_LOCK_TIME

if TYPE_CHECKING:
    from .device import GimdowBLEDevice
    from ..devices import GimdowBLEData

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Blocked-reason model
# ---------------------------------------------------------------------------


class LockBlockedReason(Enum):
    """Why the lock cannot currently execute a lock command."""

    NONE = "none"
    DOOR_OPEN_PENDING = (
        "door_open_pending"  # HA asked to lock while door open → waiting
    )
    DOOR_OPEN_LOCKED = "door_open_locked"  # Lock engaged but door is physically open


class LockTransitionState(Enum):
    """Active lock/unlock transition state.

    Replaces three independent booleans (_is_locking, _is_unlocking,
    _is_timeout_unknown) with a single four-value FSM, eliminating the
    invalid combinations that the three-bool model allowed.
    """

    IDLE = "idle"
    LOCKING = "locking"
    UNLOCKING = "unlocking"
    TIMEOUT_UNKNOWN = "timeout_unknown"


@dataclass(frozen=True)
class LockManagerConfig:
    """Static + dynamic configuration for GimdowBLELockManager.

    All mapping IDs and static options are frozen at construction time.
    ``get_auto_lock`` is a callable because ``virtual_auto_lock`` is a live
    HA switch entity that can change at runtime.
    """

    unknown_state_action: str
    transition_timeout: int
    auto_lock_delay_fallback: int
    lock_dp_id: int
    unlock_dp_id: int
    state_dp_id: int
    lock_value: bool
    unlock_value: bool
    get_auto_lock: Callable[[], bool]
    has_door_sensor: bool = False


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
        config: LockManagerConfig,
        on_state_change: Callable[[], None],
    ) -> None:
        self._device = device
        self._hass = hass
        self._config = config
        self._on_state_change = on_state_change

        self._transition_state: LockTransitionState = LockTransitionState.IDLE

        # Deferred lock intent
        self._pending = PendingLockIntent(on_state_change)

        # Auto-lock timer handle
        self._auto_lock_timer = None

        # Transition timeout handle
        self._transition_timeout_task: asyncio.Task | None = None

        # Door state mirror (updated by the HA entity via on_door_changed)
        self._is_door_open: bool = False
        self._door_state_known: bool = False

        # Attribution tracking
        self._pending_action_source: str | None = None

        # Background resolution task
        self._resolution_task: asyncio.Task | None = None

        # Task created when door closes with a pending lock intent
        self._door_close_lock_task: asyncio.Task | None = None

        # Last state HA had for this lock (updated on every coordinator update)
        self._last_known_state: bool | None = None

    # ------------------------------------------------------------------
    # Properties consumed by the HA entity
    # ------------------------------------------------------------------

    @property
    def is_locking(self) -> bool:
        return self._transition_state == LockTransitionState.LOCKING

    @property
    def is_unlocking(self) -> bool:
        return self._transition_state == LockTransitionState.UNLOCKING

    @property
    def is_timeout_unknown(self) -> bool:
        return self._transition_state == LockTransitionState.TIMEOUT_UNKNOWN

    @property
    def pending(self) -> PendingLockIntent:
        return self._pending

    @property
    def is_door_open(self) -> bool:
        return self._is_door_open

    @property
    def pending_action_source(self) -> str | None:
        return self._pending_action_source

    @property
    def auto_lock_timer_active(self) -> bool:
        return self._auto_lock_timer is not None

    @property
    def _resolution_active(self) -> bool:
        return bool(self._resolution_task and not self._resolution_task.done())

    # ------------------------------------------------------------------
    # External event hooks (called by HA entity dispatchers)
    # ------------------------------------------------------------------

    def on_door_changed(self, is_open: bool) -> None:
        """Door sensor state changed."""
        prev = self._is_door_open
        self._is_door_open = is_open
        self._door_state_known = True
        pn_async_dismiss(
            self._hass,
            f"gimdow_ble_autolock_no_sensor_{self._device.address}",
        )
        _LOGGER.debug(
            "[%s] Door: %s → %s. pending=%s auto_lock=%s",
            self._device.address,
            prev,
            is_open,
            self._pending,
            self._config.get_auto_lock(),
        )

        if not is_open:
            if self._pending.active:
                _LOGGER.debug(
                    "[%s] Door closed — pending lock intent active; issuing lock command.",
                    self._device.address,
                )
                is_locked = self._device.get_lock_state(self._config.state_dp_id)
                if is_locked:
                    # Already locked (e.g. manually locked while door was open);
                    # just clear pending — no motor command needed, no timeout to set.
                    self._pending.clear()
                    return
                # Set LOCKING before clearing PENDING so the UI shows "Locking" not "Unlocked"
                self._transition_state = LockTransitionState.LOCKING
                self._start_transition_timeout()
                self._pending.clear()
                self._door_close_lock_task = self._hass.async_create_task(
                    self.lock(), name="gimdow_door_close_lock"
                )
                # Skip auto-lock timer — the lock task above is already in flight.
                return

        # When the door opens, start_auto_lock_timer() is called so that if the timer
        # fires while the door is still open, lock() sets DOOR_OPEN_PENDING and the
        # bolt fires the instant the door closes. The timer is also restarted on
        # door-close (below path), so the net UX effect is: auto-lock countdown
        # runs from the last door-close event. This is intentional — do not remove.
        self.start_auto_lock_timer()

    def on_auto_lock_setting_changed(self) -> None:
        """Virtual auto-lock switch toggled."""
        _LOGGER.debug(
            "[%s] Auto-lock setting changed: %s",
            self._device.address,
            self._config.get_auto_lock(),
        )
        self.start_auto_lock_timer()

    def on_auto_lock_time_changed(self) -> None:
        """Auto-lock delay (DP 36) changed."""
        _LOGGER.debug("[%s] Auto-lock delay changed.", self._device.address)
        self.start_auto_lock_timer()

    def on_connected(self) -> None:
        """Reset transition state on BLE reconnect, then auto-start if configured."""
        _LOGGER.debug(
            "[%s] on_connected: clearing transition state.", self._device.address
        )
        # Skip reset if a resolution task is already running (e.g. double-fire when
        # both the BLE callback and the coordinator grace-expiry path call on_connected).
        if not self._resolution_active:
            self._transition_state = LockTransitionState.IDLE
            self._cancel_transition_timeout()

        action = self._config.unknown_state_action

        if action == UNKNOWN_STATE_ACTION_CONFIRM_LAST:
            if self._last_known_state is None:
                _LOGGER.debug(
                    "[%s] confirm_last: no prior state known — staying unknown.",
                    self._device.address,
                )
                return
            _LOGGER.warning(
                "[%s] on_connected: confirm_last → confirming last known state=%s.",
                self._device.address,
                "LOCK" if self._last_known_state else "UNLOCK",
            )
            if not self._resolution_active:
                self._resolution_task = self._hass.async_create_task(
                    self._double_command_to_state(self._last_known_state),
                    name="gimdow_confirm_last_resolution",
                )

        elif action == UNKNOWN_STATE_ACTION_FORCE_LOCK_TWICE:
            if self._last_known_state is None:
                _LOGGER.debug(
                    "[%s] force_lock_twice: no prior state — skipping first-boot lock.",
                    self._device.address,
                )
                return
            _LOGGER.warning(
                "[%s] on_connected: force_lock_twice → locking twice.",
                self._device.address,
            )
            if not self._resolution_active:
                self._resolution_task = self._hass.async_create_task(
                    self._double_command_to_state(True, force_two_attempts=True),
                    name="gimdow_force_lock_twice_resolution",
                )

    def restore_last_known_state(self, is_locked: bool) -> None:
        """Seed _last_known_state from HA's RestoreEntity after a restart.

        Only applied when there is no live DP reading yet — a fresh BLE
        notification always takes precedence over the restored value.
        """
        if self._last_known_state is None:
            self._last_known_state = is_locked
            _LOGGER.debug(
                "[%s] Restored last known state: %s",
                self._device.address,
                "LOCKED" if is_locked else "UNLOCKED",
            )

    def on_coordinator_update(self, is_locked: bool | None) -> None:
        """Called by the HA entity after every coordinator update.

        Only clear the transition flag whose *target* state has been reached:
        - is_locked=False  → unlock completed, clear _is_unlocking
        - is_locked=True   → lock completed,   clear _is_locking

        The opposite flag (e.g. _is_locking while still unlocked) is left
        alone so the UI keeps showing "locking" until the device confirms
        or the transition timeout fires.
        """
        if is_locked is not None:
            self._last_known_state = is_locked
            target_reached = (
                (
                    is_locked is False
                    and self._transition_state == LockTransitionState.UNLOCKING
                )
                or (
                    is_locked is True
                    and self._transition_state == LockTransitionState.LOCKING
                )
                or self._transition_state == LockTransitionState.TIMEOUT_UNKNOWN
            )
            if target_reached:
                self._transition_state = LockTransitionState.IDLE
                self._cancel_transition_timeout()

        # Safety net: if unlocked with no timer and auto-lock is on, restart timer
        if (
            self._config.get_auto_lock()
            and is_locked is False
            and not self._is_door_open
            and self._auto_lock_timer is None
        ):
            _LOGGER.debug(
                "[%s] Coordinator: unlocked without timer → restarting.",
                self._device.address,
            )
            self.start_auto_lock_timer()

    def update_attribution(
        self, current_is_locked: bool | None, last_is_locked: bool | None
    ) -> tuple[bool, str | None]:
        """Return (True, changed_by) when a state transition occurs, else (False, None)."""
        if current_is_locked is None:
            return False, None
        if last_is_locked is not None and current_is_locked != last_is_locked:
            if self._pending_action_source == ACTION_SOURCE_AUTO:
                changed_by: str | None = "Auto Lock"
            elif self._pending_action_source == ACTION_SOURCE_HA:
                changed_by = None
            else:
                changed_by = "Manual"
            _LOGGER.debug(
                "[%s] Attribution: %s → %s. source=%s. changed_by=%s",
                self._device.address,
                last_is_locked,
                current_is_locked,
                self._pending_action_source,
                changed_by,
            )
            self._pending_action_source = None
            return True, changed_by
        return False, None  # no transition

    # ------------------------------------------------------------------
    # Main lock / unlock operations
    # ------------------------------------------------------------------

    async def lock(self) -> None:
        """Execute a lock command through the full state machine."""
        is_locked = self._device.get_lock_state(self._config.state_dp_id)

        _LOGGER.debug(
            "[%s] lock() called. is_locked=%s door_open=%s auto_lock=%s pending=%s",
            self._device.address,
            is_locked,
            self._is_door_open,
            self._config.get_auto_lock(),
            self._pending,
        )

        # --- Already locked ---
        if is_locked is True:
            _LOGGER.debug(
                "[%s] Already locked — no command sent.", self._device.address
            )
            return

        # --- Unknown state ---
        if is_locked is None:
            await self._handle_unknown_state(
                target_lock=True,
                user_initiated=(self._pending_action_source != ACTION_SOURCE_AUTO),
            )
            return

        # --- Door open (or door sensor configured but state not yet received) ---
        if self._config.has_door_sensor and (
            not self._door_state_known or self._is_door_open
        ):
            _LOGGER.debug(
                "[%s] Door open → setting pending lock intent.",
                self._device.address,
            )
            self._pending.set(LockBlockedReason.DOOR_OPEN_PENDING)
            return

        # Cancel the auto-lock timer only now that we're definitely sending a command.
        # Cancelling before the door-open check would lose the timer on blocked calls.
        self.stop_auto_lock_timer()

        # --- Normal lock ---
        self._pending.clear()
        if self._transition_state != LockTransitionState.LOCKING:
            self._transition_state = LockTransitionState.LOCKING
            self._start_transition_timeout()
            self._on_state_change()
        try:
            dp = await self._device.send_control_datapoint(
                self._config.lock_dp_id, self._config.lock_value
            )
            if dp and self._pending_action_source != ACTION_SOURCE_AUTO:
                self._pending_action_source = ACTION_SOURCE_HA
        except Exception as e:
            _LOGGER.error(
                "[%s] lock() failed: %s. pending=%s locking=%s door=%s",
                self._device.address,
                e,
                self._pending,
                self.is_locking,
                self._is_door_open,
            )
            self._transition_state = LockTransitionState.IDLE
            self._cancel_transition_timeout()
            self._pending_action_source = None
            self._on_state_change()
            raise

    async def unlock(self) -> None:
        """Execute an unlock command."""
        is_locked = self._device.get_lock_state(self._config.state_dp_id)

        _LOGGER.debug(
            "[%s] unlock() called. is_locked=%s pending=%s",
            self._device.address,
            is_locked,
            self._pending,
        )

        # Clear any pending lock intent (e.g. door-open-blocked lock request).
        if self._pending:
            _LOGGER.debug("[%s] Unlock clears pending intent.", self._device.address)
            self._pending.clear()

        # --- Unknown state ---
        if is_locked is None:
            await self._handle_unknown_state(target_lock=False, user_initiated=True)
            return

        # --- Already unlocked ---
        if is_locked is False:
            _LOGGER.debug(
                "[%s] Already unlocked — no command sent.", self._device.address
            )
            return

        # --- Normal unlock ---
        self._transition_state = LockTransitionState.UNLOCKING
        self._start_transition_timeout()
        self._on_state_change()
        try:
            dp = await self._device.send_control_datapoint(
                self._config.unlock_dp_id, self._config.unlock_value
            )
            if dp:
                self._pending_action_source = ACTION_SOURCE_HA
                self.start_auto_lock_timer()
        except Exception as e:
            _LOGGER.error(
                "[%s] unlock() failed: %s. unlocking=%s",
                self._device.address,
                e,
                self.is_unlocking,
            )
            self._transition_state = LockTransitionState.IDLE
            self._cancel_transition_timeout()
            self._pending_action_source = None
            self._on_state_change()
            raise

    # ------------------------------------------------------------------
    # Unknown state handling
    # ------------------------------------------------------------------

    async def _handle_unknown_state(
        self, target_lock: bool, user_initiated: bool = False
    ) -> None:
        if self._resolution_active:
            if user_initiated:
                _LOGGER.warning(
                    "[%s] Unknown-state resolution already in progress — user %s command discarded.",
                    self._device.address,
                    "LOCK" if target_lock else "UNLOCK",
                )
            else:
                _LOGGER.debug(
                    "[%s] Unknown-state task already in progress. Ignoring.",
                    self._device.address,
                )
            return

        action = self._config.unknown_state_action

        # An explicit lock request (door-close pending or auto-lock) must never be
        # silently dropped, regardless of strategy. The double-command covers both
        # starting states and will re-set DOOR_OPEN_PENDING if the door is still open.
        is_explicit_lock = target_lock and (
            self._pending.active or self._pending_action_source == ACTION_SOURCE_AUTO
        )

        if action == UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION or is_explicit_lock:
            _LOGGER.warning(
                "[%s] State=Unknown → double-command to %s (action=%s explicit=%s).",
                self._device.address,
                "LOCK" if target_lock else "UNLOCK",
                action,
                is_explicit_lock,
            )
            self._resolution_task = self._hass.async_create_task(
                self._double_command_to_state(target_lock),
                name="gimdow_double_on_action_resolution",
            )
        else:
            _LOGGER.debug(
                "[%s] %s — ignoring command in unknown state (reconnect resolution handles it).",
                self._device.address,
                action,
            )

    async def _double_command_to_state(
        self, target_lock: bool, *, force_two_attempts: bool = False
    ) -> None:
        """Drive the lock to target_lock using up to two DP47-echo-driven sends.

        Each attempt uses send_command_wait_state_echo so we know the motor cycle
        finished before issuing the next command — no fixed sleep guards needed.
        The device reliably pushes DP47 after every motor movement, so waiting for
        the echo is both faster and more robust than a fixed delay.

        force_two_attempts: always run both motor cycles even if attempt 1 already
        reaches the target state. Required for force_lock_twice where the second
        cycle physically re-engages the deadbolt regardless of reported state.

        Flow (force_two_attempts=False):
          attempt 1: send → wait DP47 echo
            → echo + correct state  → done (fast path, ~1-2 s real-world)
            → echo + wrong state    → attempt 2
            → no echo (timeout)     → attempt 2
          attempt 2: send → wait DP47 echo
            → echo  → done
            → no echo → TIMEOUT_UNKNOWN

        Flow (force_two_attempts=True):
          attempt 1: send → wait DP47 echo (always continues to attempt 2)
          attempt 2: send → wait DP47 echo
            → echo  → done
            → no echo → TIMEOUT_UNKNOWN
        """
        if target_lock and self._is_door_open:
            _LOGGER.warning(
                "[%s] double_command_to_state: door open + target=LOCK → setting pending.",
                self._device.address,
            )
            self._pending.set(LockBlockedReason.DOOR_OPEN_PENDING)
            return

        self.stop_auto_lock_timer()

        dp_id = self._config.lock_dp_id if target_lock else self._config.unlock_dp_id
        value = self._config.lock_value if target_lock else self._config.unlock_value
        timeout = (
            float(self._config.transition_timeout)
            if self._config.transition_timeout
            else 60.0
        )

        self._transition_state = (
            LockTransitionState.LOCKING
            if target_lock
            else LockTransitionState.UNLOCKING
        )
        self._on_state_change()

        try:
            for attempt in range(1, 3):
                _LOGGER.debug(
                    "[%s] double_command: attempt %d of 2 (target=%s).",
                    self._device.address,
                    attempt,
                    "LOCK" if target_lock else "UNLOCK",
                )
                echo = await self._device.send_command_wait_state_echo(
                    dp_id, value, self._config.state_dp_id, timeout=timeout
                )
                if echo:
                    current = self._device.get_lock_state(self._config.state_dp_id)
                    _LOGGER.debug(
                        "[%s] double_command: attempt %d echo received, state=%s (want %s).",
                        self._device.address,
                        attempt,
                        current,
                        target_lock,
                    )
                    if current == target_lock and not (force_two_attempts and attempt == 1):
                        if not target_lock:
                            self.start_auto_lock_timer()
                        return
                    if current != target_lock:
                        # Echo arrived but wrong state — send again
                        _LOGGER.debug(
                            "[%s] double_command: attempt %d wrong state after echo — retrying.",
                            self._device.address,
                            attempt,
                        )
                    # else: force_two_attempts on attempt 1 — continue to attempt 2
                else:
                    _LOGGER.debug(
                        "[%s] double_command: attempt %d — no DP47 echo within %ss.",
                        self._device.address,
                        attempt,
                        timeout,
                    )

            _LOGGER.warning(
                "[%s] double_command: no DP47 echo after 2 attempts — marking timeout unknown.",
                self._device.address,
            )
            self._transition_state = LockTransitionState.TIMEOUT_UNKNOWN

        except asyncio.CancelledError:
            _LOGGER.debug("[%s] double_command task cancelled.", self._device.address)
            raise
        except Exception as e:
            _LOGGER.error("[%s] double_command failed: %s", self._device.address, e)
        finally:
            # Preserve TIMEOUT_UNKNOWN so the HA entity can report it;
            # clear any other in-progress transition state.
            if self._transition_state != LockTransitionState.TIMEOUT_UNKNOWN:
                self._transition_state = LockTransitionState.IDLE
            self._on_state_change()

    # ------------------------------------------------------------------
    # Auto-lock timer
    # ------------------------------------------------------------------

    def start_auto_lock_timer(self) -> None:
        """Start or restart the auto-lock countdown if conditions are met."""
        self.stop_auto_lock_timer()

        if not self._door_state_known:
            if self._config.get_auto_lock():
                _LOGGER.warning(
                    "[%s] Auto-lock is enabled but no door sensor is configured — "
                    "the auto-lock timer will not start. Add a door sensor in the "
                    "integration options to enable this feature.",
                    self._device.address,
                )
                pn_async_create(
                    self._hass,
                    message=(
                        f"Auto-lock is enabled for the Gimdow lock at "
                        f"{self._device.address} but no door sensor is configured. "
                        "The auto-lock timer will not start until a door sensor is "
                        "added in the integration options (Settings → Devices & "
                        "Services → Gimdow BLE → Configure)."
                    ),
                    title="Gimdow Auto-Lock Misconfigured",
                    notification_id=f"gimdow_ble_autolock_no_sensor_{self._device.address}",
                )
            else:
                _LOGGER.debug(
                    "[%s] Auto-lock: door state unknown — timer not started.",
                    self._device.address,
                )
            return

        is_locked = self._device.get_lock_state(self._config.state_dp_id)
        _LOGGER.debug(
            "[%s] Auto-lock eval: enabled=%s door_open=%s is_locked=%s",
            self._device.address,
            self._config.get_auto_lock(),
            self._is_door_open,
            is_locked,
        )

        if not self._config.get_auto_lock():
            return
        if is_locked is None:
            return
        if is_locked:
            _LOGGER.debug(
                "[%s] Auto-lock: already locked — timer not started.",
                self._device.address,
            )
            return
        if self._transition_state in (
            LockTransitionState.LOCKING,
            LockTransitionState.TIMEOUT_UNKNOWN,
        ):
            _LOGGER.debug(
                "[%s] Auto-lock: transition in progress (%s) — timer not started.",
                self._device.address,
                self._transition_state.value,
            )
            return

        delay = self._config.auto_lock_delay_fallback
        dp_auto_lock_time = self._device.datapoints[DP_AUTO_LOCK_TIME]
        if dp_auto_lock_time and dp_auto_lock_time.value:
            delay = int(dp_auto_lock_time.value)
        else:
            _LOGGER.debug(
                "[%s] Auto-lock: DP%s absent or zero — using fallback delay of %ss.",
                self._device.address,
                DP_AUTO_LOCK_TIME,
                delay,
            )

        _LOGGER.debug(
            "[%s] Auto-lock: starting timer for %ss.", self._device.address, delay
        )
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
        is_locked = self._device.get_lock_state(self._config.state_dp_id)
        if is_locked:
            _LOGGER.debug(
                "[%s] Auto-lock: already locked — skipping.", self._device.address
            )
            return
        _LOGGER.debug("[%s] Auto-lock: timer expired → locking.", self._device.address)
        self._pending_action_source = ACTION_SOURCE_AUTO
        await self.lock()

    # ------------------------------------------------------------------
    # Transition Timeout Timer
    # ------------------------------------------------------------------

    def _start_transition_timeout(self) -> None:
        self._cancel_transition_timeout()
        timeout = self._config.transition_timeout
        if timeout <= 0:
            return

        async def _timeout_task():
            await asyncio.sleep(timeout)
            _LOGGER.warning(
                "[%s] Transition timeout reached. Marking state as Unknown.",
                self._device.address,
            )
            self._transition_state = LockTransitionState.TIMEOUT_UNKNOWN
            self._on_state_change()
            self._attempt_timeout_recovery()

        self._transition_timeout_task = self._hass.async_create_task(
            _timeout_task(), name="gimdow_transition_timeout"
        )

    def _attempt_timeout_recovery(self) -> None:
        """Attempt in-session recovery after entering TIMEOUT_UNKNOWN.

        DP47 is push-only and won't arrive from a status poll, so a scheduled
        status update cannot resolve the unknown state.  Instead, re-run the
        same strategy logic used in on_connected so the lock can self-heal
        while still connected.  double_on_action intentionally stays unknown.
        """
        action = self._config.unknown_state_action
        if action == UNKNOWN_STATE_ACTION_DOUBLE_ON_ACTION:
            return
        if self._last_known_state is None:
            return
        if self._resolution_active:
            return
        target = (
            self._last_known_state
            if action == UNKNOWN_STATE_ACTION_CONFIRM_LAST
            else True  # force_lock_twice
        )
        _LOGGER.warning(
            "[%s] TIMEOUT_UNKNOWN recovery: running %s → %s.",
            self._device.address,
            action,
            "LOCK" if target else "UNLOCK",
        )
        self._resolution_task = self._hass.async_create_task(
            self._double_command_to_state(
                target,
                force_two_attempts=(action == UNKNOWN_STATE_ACTION_FORCE_LOCK_TWICE),
            ),
            name="gimdow_timeout_recovery_resolution",
        )

    def _cancel_transition_timeout(self) -> None:
        if self._transition_timeout_task:
            self._transition_timeout_task.cancel()
            self._transition_timeout_task = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Cancel all background tasks. Call from async_will_remove_from_hass."""
        self.stop_auto_lock_timer()
        self._cancel_transition_timeout()
        if self._resolution_active:
            self._resolution_task.cancel()
            self._resolution_task = None
        if self._door_close_lock_task and not self._door_close_lock_task.done():
            self._door_close_lock_task.cancel()
            self._door_close_lock_task = None
