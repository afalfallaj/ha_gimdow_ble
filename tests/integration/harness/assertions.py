from __future__ import annotations

import asyncio
import dataclasses
import enum
import textwrap
import time
from typing import Any

from custom_components.gimdow_ble.gimdow_ble.device import GimdowBLEDevice

from . import state
from .ble import BLEEvent, format_event, drain_queue


@dataclasses.dataclass
class AssertionResult:
    passed: bool
    reason: str
    delta_t: float | None
    events: list[BLEEvent]
    hardware_limit: bool = (
        False  # True = known hardware limitation, not an integration failure
    )


def make_hardware_limit(
    reason: str, events: list[BLEEvent] | None = None
) -> AssertionResult:
    """Create a result that marks a known hardware limitation."""
    return AssertionResult(
        passed=True,
        reason=reason,
        delta_t=None,
        events=events or [],
        hardware_limit=True,
    )


async def assert_dp(
    dp_id: int,
    expected_value: Any,
    timeout: float,
    trigger_time: float,
) -> AssertionResult:
    """
    Wait for a specific DP event. Returns PASS when dp_id arrives with expected_value
    (or any value if expected_value is None), FAIL on timeout or wrong value.

    Reads from state._assert_queue (an independent copy of every event) so it never
    races with the event_printer background task. Sets state._assert_active=True while
    running so event_printer suppresses its duplicate prints.

    Only prints events that arrived after assert_dp was called — events buffered during
    a preceding user_gate() were already printed by event_printer.
    """
    state._assert_active = True
    deadline = trigger_time + timeout
    collected: list[BLEEvent] = []
    assert_started_at = time.monotonic()
    try:
        while True:
            # Drain events already in the queue without blocking first.
            # This handles events buffered during ensure_precondition / user_gate.
            try:
                ev = state._assert_queue.get_nowait()
            except asyncio.QueueEmpty:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return AssertionResult(
                        passed=False,
                        reason=f"TIMEOUT — no DP{dp_id} response within {timeout:.0f}s",
                        delta_t=None,
                        events=collected,
                    )
                try:
                    ev = await asyncio.wait_for(
                        state._assert_queue.get(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    continue

            collected.append(ev)
            # Only print events that arrived after assert_dp started; earlier events
            # were already printed live by event_printer during the preceding gate/prompt.
            if ev.timestamp >= assert_started_at:
                print(format_event(ev))
            if ev.kind == "dp" and ev.dp_id == dp_id:
                delta = ev.timestamp - trigger_time
                if expected_value is None or ev.dp_value == expected_value:
                    return AssertionResult(
                        passed=True,
                        reason=f"DP{dp_id} = {ev.dp_value!r} in {delta:.2f}s",
                        delta_t=delta,
                        events=collected,
                    )
                else:
                    return AssertionResult(
                        passed=False,
                        reason=(
                            f"DP{dp_id} arrived in {delta:.2f}s "
                            f"but value={ev.dp_value!r}, expected={expected_value!r}"
                        ),
                        delta_t=delta,
                        events=collected,
                    )
    finally:
        state._assert_active = False


async def user_gate(instruction: str) -> float:
    """Display a boxed instruction, wait for Enter, return time when gate was opened.

    The timestamp is captured before input() so that BLE events which arrive while
    the user reads the prompt and operates the device still produce a positive delta_t
    when the return value is passed as trigger_time to assert_dp.
    """
    width = 57
    print()
    print("+" + "-" * width + "+")
    lines = textwrap.wrap(instruction, width - 6)
    for line in lines:
        print(f"|  >> {line:<{width - 6}}|")
    print(f"|     BLE events print below as they arrive.{' ' * (width - 44)}|")
    print(f"|     Press ENTER when done.{' ' * (width - 28)}|")
    print("+" + "-" * width + "+")
    gate_open = time.monotonic()
    await asyncio.to_thread(input, "")
    return gate_open


class CrossRefAnswer(enum.Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    SKIP = "skip"


async def cross_ref(ble_report: str, question: str) -> tuple[CrossRefAnswer, str]:
    """
    Physical cross-reference — runs AFTER assert_dp. Informational only.
    Never changes PASS/FAIL.
    """
    print()
    print("  -- Physical cross-reference (informational only) --")
    print(f"  BLE reports: {ble_report}")
    print(f"  {question}")
    print("    [1] Yes, matches BLE   [2] No, different   [3] Skip")
    choice = (await asyncio.to_thread(input, "  Choice [3]: ")).strip()
    if choice == "1":
        return CrossRefAnswer.MATCH, ""
    elif choice == "2":
        desc = (
            await asyncio.to_thread(input, "  Describe what you physically see: ")
        ).strip()
        print("  ** MISMATCH recorded — this is a finding, not a verdict flip.")
        return CrossRefAnswer.MISMATCH, desc
    else:
        return CrossRefAnswer.SKIP, ""


def print_verdict(result: AssertionResult) -> None:
    if result.hardware_limit:
        print(f"\n  [~~] HARDWARE_LIMIT — {result.reason}")
    else:
        mark = "[OK] PASS" if result.passed else "[!!] FAIL"
        print(f"\n  {mark} — {result.reason}")


async def ensure_precondition(
    required_is_locked: bool,
    device: GimdowBLEDevice,
) -> None:
    """Ensure device is in required state before a scenario step."""
    current = device.get_lock_state(47)
    if current is None:
        print("  Polling device for current state…")
        t0 = time.monotonic()
        await device.update()
        await assert_dp(47, expected_value=None, timeout=10.0, trigger_time=t0)
        current = device.get_lock_state(47)

    target_str = "LOCKED" if required_is_locked else "UNLOCKED"
    if current == required_is_locked:
        print(f"  Precondition met: device is {target_str}")
        return

    gate_time = await user_gate(
        f"Device must be {target_str}. Please set it to that state, then press Enter."
    )
    # DP47 raw: True=UNLOCKED, False=LOCKED.  is_locked=True → dp47_raw=False
    expected_dp47 = not required_is_locked
    r = await assert_dp(47, expected_dp47, timeout=15.0, trigger_time=gate_time)
    if not r.passed:
        print("  ** Could not confirm precondition via BLE. Proceeding cautiously.")
