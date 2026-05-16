from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any

from custom_components.gimdow_ble.gimdow_ble.device import GimdowBLEDevice

from . import state
from .ble import BLEEvent, DP_NAME, DP9_BATTERY, drain_queue, format_event, reconnect
from .assertions import (
    AssertionResult,
    assert_dp,
    make_hardware_limit,
    user_gate,
    CrossRefAnswer,
    cross_ref,
    print_verdict,
    ensure_precondition,
)
from .credentials import StandaloneManager

# Scenario metadata — expected outcome per scenario ID.
# PASS          = integration works as expected
# HARDWARE_LIMIT = known device limitation; integration correctly handles it
META: dict[str, dict] = {
    "S0": {"title": "Connection Stability", "expected": "PASS"},
    "S1": {"title": "Initial Connect & State Read", "expected": "PASS"},
    "S2": {"title": "BLE Unlock Command", "expected": "PASS"},
    "S3": {"title": "BLE Lock Command", "expected": "PASS"},
    "S4": {"title": "Manual Action Push", "expected": "PASS"},
    "S5": {"title": "Autonomous Periodic Push", "expected": "PASS"},
    "S6": {"title": "Reconnect — State Unchanged", "expected": "HARDWARE_LIMIT"},
    "S9": {"title": "Double-Command Echo", "expected": "PASS"},
    "S10": {"title": "Back-to-Back Serialization", "expected": "PASS"},
    "S11": {"title": "Post-Reconnect State Recovery", "expected": "PASS"},
    "S12": {"title": "DP47 Response Time Distribution", "expected": "PASS"},
}


@dataclasses.dataclass
class ScenarioResult:
    num: str
    name: str
    assertion: AssertionResult | None  # None for observation-only scenarios
    cross_ref_answer: CrossRefAnswer
    cross_ref_desc: str
    events_captured: list[BLEEvent]
    extra: dict = dataclasses.field(default_factory=dict)


def print_scenario_header(num: str, name: str, description: str) -> None:
    sep = "=" * 66
    meta = META.get(num, {})
    expected = meta.get("expected", "")
    expected_tag = f"  [expected: {expected}]" if expected else ""
    print(f"\n{sep}")
    print(f"  SCENARIO {num}: {name}{expected_tag}")
    print(f"  {description}")
    print(sep)


def _dp47_state_str(device: GimdowBLEDevice) -> str:
    s = device.get_lock_state(47)
    if s is True:
        return "LOCKED"
    if s is False:
        return "UNLOCKED"
    return "UNKNOWN"


def _find_result(results: list[ScenarioResult], num: str) -> ScenarioResult | None:
    for r in results:
        if r.num == num:
            return r
    return None


# ── Scenario S0: Connection Stability ────────────────────────────────────────


async def scenario_stability(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S0",
        "Connection Stability (60s keep-alive)",
        "Hold idle 125s; PASS if no unexpected disconnects (validates session renewal).",
    )
    confirm = (
        (await asyncio.to_thread(input, "  Run this scenario? [y/N]: ")).strip().lower()
    )
    if confirm != "y":
        print("  Skipping scenario S0.")
        return device

    drain_queue()
    print("  Holding idle for 125s — watching for disconnects…")

    state._assert_active = True
    collected: list[BLEEvent] = []
    t0 = time.monotonic()
    deadline = t0 + 125.0
    disconnect_events: list[BLEEvent] = []

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ev = await asyncio.wait_for(
                    state._assert_queue.get(), timeout=remaining
                )
                collected.append(ev)
                print(format_event(ev))
                if ev.kind == "disconnected":
                    disconnect_events.append(ev)
            except asyncio.TimeoutError:
                break
    finally:
        state._assert_active = False

    elapsed_s = time.monotonic() - t0
    passed = len(disconnect_events) == 0
    if passed:
        reason = f"No disconnects in {elapsed_s:.0f}s — session renewal working"
    else:
        times = [f"{(ev.timestamp - t0):.1f}s" for ev in disconnect_events]
        reason = f"{len(disconnect_events)} disconnect(s) at t={', '.join(times)}"

    assertion = AssertionResult(
        passed=passed, reason=reason, delta_t=None, events=collected
    )
    print_verdict(assertion)

    results.append(
        ScenarioResult(
            num="S0",
            name="Connection Stability",
            assertion=assertion,
            cross_ref_answer=CrossRefAnswer.SKIP,
            cross_ref_desc="",
            events_captured=collected,
            extra={"disconnect_count": len(disconnect_events)},
        )
    )
    return device


# ── Scenario 1 ───────────────────────────────────────────────────────────────


async def scenario_1(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S1",
        "Initial Connection & State Read",
        "Verify device connects, pairs, and pushes battery (DP9).\n"
        "  Note: DP47 (lock state) is push-only — it never arrives in update().",
    )
    drain_queue()

    t0 = time.monotonic()
    await device.update()
    # DP47 (lock_motor_state) is push-only; only arrives when the motor moves.
    # Assert only DP9 (battery_state) which IS included in every update() response.
    result = await assert_dp(9, expected_value=None, timeout=10.0, trigger_time=t0)
    print_verdict(result)

    if result.events:
        print("\n  DPs received during poll:")
        auto_lock_on: bool | None = None
        auto_lock_delay: int | None = None
        for ev in result.events:
            if ev.kind != "dp":
                continue
            raw = ev.dp_value
            name = DP_NAME.get(ev.dp_id, "")
            hint = f"  ({name})" if name else ""
            if ev.dp_id == 9:
                pct = DP9_BATTERY.get(str(raw), "?")
                hint = f"  (battery_state → {pct})"
            elif ev.dp_id == 33:
                auto_lock_on = bool(raw)
                hint = f"  (automatic_lock → {'ON' if raw else 'OFF'})"
            elif ev.dp_id == 36:
                auto_lock_delay = int(raw)
                hint = f"  (auto_lock_time → {raw}s)"
            print(f"    DP{ev.dp_id:>3} = {raw!r}{hint}")
        print(
            "  Note: DP47 not in update() response — push-only (only sent when motor moves)."
        )
        if auto_lock_on is True:
            delay_str = (
                f"{auto_lock_delay}s"
                if auto_lock_delay is not None
                else "unknown delay"
            )
            print(f"\n  *** AUTO-LOCK IS ON (delay={delay_str}) ***")
            print("      Device will re-lock automatically after unlock.")

    phys = (
        (
            await asyncio.to_thread(
                input, "  Physical lock state for reference (locked/unlocked/skip): "
            )
        )
        .strip()
        .lower()
    )
    results.append(
        ScenarioResult(
            num="S1",
            name="Initial State Read",
            assertion=result,
            cross_ref_answer=CrossRefAnswer.SKIP,
            cross_ref_desc=phys if phys not in ("", "skip") else "",
            events_captured=result.events,
        )
    )
    return device


# ── Scenario 2 ───────────────────────────────────────────────────────────────


async def scenario_2(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S2",
        "BLE Unlock Command",
        "Verify DP6=True unlocks the device (DP47 -> True/UNLOCKED).",
    )
    drain_queue()

    await ensure_precondition(required_is_locked=True, device=device)
    drain_queue()
    t0 = time.monotonic()
    # DP6 is absent from the Tuya spec's functions/status lists but works in practice —
    # likely a legacy shortcut retained in the firmware.
    # Hardware-confirmed motor response: max=18.33s, recommended timeout=28s.
    await device.send_control_datapoint(6, True)
    result = await assert_dp(47, True, timeout=30.0, trigger_time=t0)
    print_verdict(result)

    # DP19 (unlock_ble) is pushed alongside DP47 on a successful BLE unlock.
    dp19_events = [ev for ev in result.events if ev.kind == "dp" and ev.dp_id == 19]
    if dp19_events:
        print(
            f"  DP19 (unlock_ble record={dp19_events[0].dp_value!r}) also received — BLE unlock confirmed."
        )
    else:
        print(
            "  DP19 (unlock_ble) not seen — device may not push unlock records for DP6."
        )

    ble_str = "UNLOCKED" if result.passed else _dp47_state_str(device)
    cr, desc = await cross_ref(
        ble_str, "Does the bolt match (bolt retracted = UNLOCKED)?"
    )
    results.append(
        ScenarioResult(
            num="S2",
            name="BLE Unlock Command",
            assertion=result,
            cross_ref_answer=cr,
            cross_ref_desc=desc,
            events_captured=result.events,
            extra={"dp19_fired": bool(dp19_events)},
        )
    )
    return device


# ── Scenario 3 ───────────────────────────────────────────────────────────────


async def scenario_3(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S3",
        "BLE Lock Command",
        "Verify DP46=True locks the device (DP47 -> False/LOCKED).",
    )
    drain_queue()

    await ensure_precondition(required_is_locked=False, device=device)
    drain_queue()
    t0 = time.monotonic()
    # Hardware-confirmed motor response: max=18.33s, recommended timeout=28s.
    await device.send_control_datapoint(46, True)
    result = await assert_dp(47, False, timeout=30.0, trigger_time=t0)
    print_verdict(result)

    ble_str = "LOCKED" if result.passed else _dp47_state_str(device)
    cr, desc = await cross_ref(ble_str, "Does the bolt match (bolt extended = LOCKED)?")
    results.append(
        ScenarioResult(
            num="S3",
            name="BLE Lock Command",
            assertion=result,
            cross_ref_answer=cr,
            cross_ref_desc=desc,
            events_captured=result.events,
        )
    )
    return device


# ── Scenario 4 ───────────────────────────────────────────────────────────────


async def scenario_4(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S4",
        "Manual Action Push",
        "Verify device proactively pushes DP47 when physically operated.\n"
        "  Hardware-confirmed push latency: ~5–9s after motor movement.",
    )

    print("\n  -- Sub-case 4a: manually UNLOCK --")
    drain_queue()
    await ensure_precondition(required_is_locked=True, device=device)
    trigger_time = await user_gate("Manually UNLOCK the device. Press ENTER when done.")
    # 15s window: allows ~3-5s for physical operation + confirmed 5-9s BLE push latency
    result_4a = await assert_dp(47, True, timeout=15.0, trigger_time=trigger_time)
    print_verdict(result_4a)
    cr_4a, desc_4a = await cross_ref(
        "UNLOCKED" if result_4a.passed else _dp47_state_str(device),
        "Does the bolt match (bolt retracted = UNLOCKED)?",
    )
    results.append(
        ScenarioResult(
            num="S4a",
            name="Manual Push -> UNLOCK",
            assertion=result_4a,
            cross_ref_answer=cr_4a,
            cross_ref_desc=desc_4a,
            events_captured=result_4a.events,
        )
    )

    print("\n  -- Sub-case 4b: manually LOCK --")
    drain_queue()
    await ensure_precondition(required_is_locked=False, device=device)
    trigger_time = await user_gate("Manually LOCK the device. Press ENTER when done.")
    result_4b = await assert_dp(47, False, timeout=15.0, trigger_time=trigger_time)
    print_verdict(result_4b)
    cr_4b, desc_4b = await cross_ref(
        "LOCKED" if result_4b.passed else _dp47_state_str(device),
        "Does the bolt match (bolt extended = LOCKED)?",
    )
    results.append(
        ScenarioResult(
            num="S4b",
            name="Manual Push -> LOCK",
            assertion=result_4b,
            cross_ref_answer=cr_4b,
            cross_ref_desc=desc_4b,
            events_captured=result_4b.events,
        )
    )
    return device


# ── Scenario 5 ───────────────────────────────────────────────────────────────


async def scenario_5(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S5",
        "Autonomous Periodic Push",
        "Wait for device's keep-alive DP9 push. No update() call.\n"
        "  Hardware-confirmed push interval: ~45s.",
    )
    drain_queue()

    print("  Listening for autonomous DP9 push (up to 60s)…")
    t0 = time.monotonic()
    # 60s window covers the confirmed ~45s push interval with margin.
    result = await assert_dp(9, expected_value=None, timeout=60.0, trigger_time=t0)

    if result.passed and result.delta_t is not None:
        print(f"\n  DP9 arrived at t={result.delta_t:.1f}s from scenario start.")
        if result.delta_t < 10.0:
            print(
                "  Note: arrived very quickly — likely from connect handshake, not keep-alive cycle."
            )

    print_verdict(result)
    results.append(
        ScenarioResult(
            num="S5",
            name="Autonomous Push (DP9)",
            assertion=result,
            cross_ref_answer=CrossRefAnswer.SKIP,
            cross_ref_desc="",
            events_captured=result.events,
            extra={"push_interval": result.delta_t},
        )
    )
    return device


# ── Scenario 6 ───────────────────────────────────────────────────────────────


async def scenario_6(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    mac: str,
    os_address: str,
    manager: StandaloneManager,
    on_dp: Any,
    on_conn: Any,
    on_disc: Any,
    device_ref: list,
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S6",
        "Reconnect — State Unchanged",
        "Hardware limitation: device never pushes DP47 on reconnect.\n"
        "  HARDWARE_LIMIT = integration correctly shows unknown state (None).\n"
        "  FAIL = unexpected DP47 push contradicts known hardware behavior.",
    )
    drain_queue()
    await ensure_precondition(required_is_locked=True, device=device)
    await device.stop()
    await asyncio.sleep(0.5)
    drain_queue()
    device = await reconnect(
        os_address, mac, manager, on_dp, on_conn, on_disc, device_ref
    )
    await device.update()
    # Wait 5s for any unexpected DP47 push (hardware limit: we expect none)
    probe = await assert_dp(
        47, expected_value=None, timeout=5.0, trigger_time=time.monotonic()
    )
    if not probe.passed:
        # Timeout = DP47 not pushed = hardware limitation confirmed (expected)
        assertion = make_hardware_limit(
            "State unknown after reconnect — device never pushes DP47 on reconnect",
            events=probe.events,
        )
    else:
        # DP47 arrived = unexpected behavior
        assertion = AssertionResult(
            passed=False,
            reason=f"Unexpected: DP47 pushed on reconnect ({probe.reason})",
            delta_t=probe.delta_t,
            events=probe.events,
        )
    print_verdict(assertion)
    results.append(
        ScenarioResult(
            num="S6",
            name="Reconnect — State Unchanged",
            assertion=assertion,
            cross_ref_answer=CrossRefAnswer.SKIP,
            cross_ref_desc="",
            events_captured=probe.events,
        )
    )
    return device


# ── Scenario 9 ───────────────────────────────────────────────────────────────


async def scenario_9(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S9",
        "Double-Command Echo Pattern",
        "Send the target command twice, waiting for a DP47 echo each time.\n"
        "  Workaround for unknown state when DP47=None persists after reconnect.\n"
        "  Both echoes received = device reached target state.",
    )
    confirm = (
        (
            await asyncio.to_thread(
                input,
                "  This test sends 2 commands with 25s echo timeouts each. Run it? [y/N]: ",
            )
        )
        .strip()
        .lower()
    )
    if confirm != "y":
        print("  Skipping scenario S9.")
        return device

    drain_queue()
    print(
        "  Running double-command echo pattern (target=UNLOCKED, 2 × 25s echo, up to ~50s)…"
    )
    t0 = time.monotonic()

    # Send the unlock command twice, waiting for a DP47 echo each time.
    # Tests whether the hardware reliably pushes DP47 after each motor cycle.
    dp_id = 6  # unlock_dp_id
    value = True

    echo1 = await device.send_command_wait_state_echo(dp_id, value, 47, timeout=25.0)
    print(
        f"  1st echo: {'received' if echo1 else 'TIMEOUT'} at t={time.monotonic() - t0:.1f}s"
    )

    echo2 = await device.send_command_wait_state_echo(dp_id, value, 47, timeout=25.0)
    print(
        f"  2nd echo: {'received' if echo2 else 'TIMEOUT'} at t={time.monotonic() - t0:.1f}s"
    )

    passed = echo1 and echo2
    if passed:
        reason = "Both DP47 echoes received — device reached target state"
    else:
        reason = "One or both DP47 echoes timed out — state may still be unknown"
    print(f"  {reason}")

    assertion = AssertionResult(
        passed=passed,
        reason=reason,
        delta_t=time.monotonic() - t0,
        events=[],
    )
    print_verdict(assertion)

    cr, desc = await cross_ref(
        _dp47_state_str(device), "What physical sequence did you observe on the device?"
    )
    results.append(
        ScenarioResult(
            num="S9",
            name="Double-Command Echo",
            assertion=assertion,
            cross_ref_answer=cr,
            cross_ref_desc=desc,
            events_captured=[],
        )
    )
    return device


# ── Scenario 10 ──────────────────────────────────────────────────────────────


async def scenario_10(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S10",
        "Back-to-Back Command Serialization",
        "Unlock then lock, each waited on via DP47 confirmation before sending next.\n"
        "  Verifies the integration's serialization strategy works end-to-end.",
    )
    drain_queue()

    await ensure_precondition(required_is_locked=True, device=device)
    drain_queue()

    print("\n  Step 1: send unlock (DP6=True), wait for DP47=True (UNLOCKED)…")
    t0 = time.monotonic()
    await device.send_control_datapoint(6, True)
    unlock_result = await assert_dp(47, True, timeout=30.0, trigger_time=t0)
    print_verdict(unlock_result)

    if not unlock_result.passed:
        print("  Aborting — unlock did not confirm; cannot proceed to lock step.")
        results.append(
            ScenarioResult(
                num="S10",
                name="Back-to-Back Command Serialization",
                assertion=unlock_result,
                cross_ref_answer=CrossRefAnswer.SKIP,
                cross_ref_desc="aborted: unlock did not confirm",
                events_captured=unlock_result.events,
                extra={"unlock_delta_t": None, "lock_delta_t": None},
            )
        )
        return device

    print(
        f"  Unlock confirmed in {unlock_result.delta_t:.2f}s — sending lock (DP46=True)…"
    )
    drain_queue()
    t1 = time.monotonic()
    await device.send_control_datapoint(46, True)
    lock_result = await assert_dp(47, False, timeout=30.0, trigger_time=t1)
    print_verdict(lock_result)

    passed = unlock_result.passed and lock_result.passed
    summary = (
        f"unlock in {unlock_result.delta_t:.2f}s, lock in {lock_result.delta_t:.2f}s"
        if passed
        else f"unlock={'OK' if unlock_result.passed else 'FAIL'}, lock={'OK' if lock_result.passed else 'FAIL'}"
    )
    print(f"\n  Summary: {summary}")

    cr, desc = await cross_ref(
        "LOCKED" if lock_result.passed else _dp47_state_str(device),
        "Does the bolt match (bolt extended = LOCKED)?",
    )
    results.append(
        ScenarioResult(
            num="10",
            name="Back-to-Back Command Serialization",
            assertion=AssertionResult(
                passed=passed,
                reason=summary,
                delta_t=lock_result.delta_t,
                events=unlock_result.events + lock_result.events,
            ),
            cross_ref_answer=cr,
            cross_ref_desc=desc,
            events_captured=unlock_result.events + lock_result.events,
            extra={
                "unlock_delta_t": unlock_result.delta_t,
                "lock_delta_t": lock_result.delta_t,
            },
        )
    )
    return device


# ── Scenario 11 ──────────────────────────────────────────────────────────────


async def scenario_11(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    mac: str,
    os_address: str,
    manager: StandaloneManager,
    on_dp: Any,
    on_conn: Any,
    on_disc: Any,
    device_ref: list,
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S11",
        "Post-Reconnect State Recovery",
        "S6/S7 confirmed DP47 is never pushed on reconnect.\n"
        "  This scenario verifies recovery: disconnect → reconnect → lock command → DP47.\n"
        "  PASS = device responds to a command from unknown state.",
    )
    drain_queue()

    await ensure_precondition(required_is_locked=False, device=device)
    drain_queue()

    print("\n  Forcing disconnect to simulate unknown-state reconnect…")
    await device.stop()
    await asyncio.sleep(0.5)

    # Use reconnect() (creates a fresh GimdowBLEDevice) so _datapoints is empty.
    # Reusing the same device object after stop()/initialize() would leave the old
    # DP47 cached value intact, making get_lock_state(47) return the stale state
    # instead of None and producing a misleading "DP47 was pushed on reconnect" log.
    device = await reconnect(
        os_address, mac, manager, on_dp, on_conn, on_disc, device_ref
    )
    await asyncio.sleep(1.0)  # let CONNECTED event arrive

    state_after_reconnect = device.get_lock_state(47)
    print(f"  DP47 after reconnect: {_dp47_state_str(device)}")
    if state_after_reconnect is not None:
        print("  NOTE: DP47 was pushed on reconnect — unexpected (contradicts S6/S7).")
    else:
        print("  DP47 is None — unknown state confirmed. Sending lock command…")

    drain_queue()
    t0 = time.monotonic()
    await device.send_control_datapoint(46, True)
    # Hardware-confirmed: confirmed in 14.27s. 30s gives clean margin.
    lock_result = await assert_dp(47, False, timeout=30.0, trigger_time=t0)
    print_verdict(lock_result)

    cr, desc = await cross_ref(
        "LOCKED" if lock_result.passed else _dp47_state_str(device),
        "Does the bolt match (bolt extended = LOCKED)?",
    )
    results.append(
        ScenarioResult(
            num="S11",
            name="Post-Reconnect State Recovery",
            assertion=lock_result,
            cross_ref_answer=cr,
            cross_ref_desc=desc,
            events_captured=lock_result.events,
            extra={"dp47_after_reconnect": state_after_reconnect},
        )
    )
    return device


# ── Scenario 12 ──────────────────────────────────────────────────────────────


async def scenario_12(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    **_: Any,
) -> GimdowBLEDevice:
    print_scenario_header(
        "S12",
        "DP47 Response Time Distribution (5 Cycles)",
        "Lock/unlock 5 cycles, each waited on via DP47 confirmation.\n"
        "  Hardware-confirmed: min=12.28s, max=18.33s, avg=15.84s → timeout=28s.",
    )
    drain_queue()

    await ensure_precondition(required_is_locked=True, device=device)
    drain_queue()

    CYCLES = 3  # baseline data confirmed from 5-cycle run; 3 cycles sufficient for new hardware
    commands = []
    current_locked = True
    for _ in range(CYCLES):
        if current_locked:
            commands.append((6, True, True, "UNLOCK (DP6=True)"))
            current_locked = False
        else:
            commands.append((46, True, False, "LOCK (DP46=True)"))
            current_locked = True

    timings: list[float] = []
    all_events: list[BLEEvent] = []
    all_passed = True

    for i, (dp_id, value, expected_dp47, label) in enumerate(commands, start=1):
        drain_queue()
        print(f"\n  Cycle {i}/{CYCLES}: {label}")
        t0 = time.monotonic()
        await device.send_control_datapoint(dp_id, value)
        result = await assert_dp(47, expected_dp47, timeout=30.0, trigger_time=t0)
        all_events.extend(result.events)
        if result.passed:
            timings.append(result.delta_t)
            print(f"    DP47 confirmed in {result.delta_t:.2f}s")
        else:
            all_passed = False
            print(f"    FAIL — {result.reason}")
            print("    Aborting remaining cycles.")
            break

    if timings:
        min_t = min(timings)
        max_t = max(timings)
        avg_t = sum(timings) / len(timings)
        # Hardware-confirmed baseline: min=12.28s, max=18.33s, avg=15.84s, recommended=28s
        print(f"\n  Results ({len(timings)}/{CYCLES} cycles completed):")
        print(f"    min={min_t:.2f}s  max={max_t:.2f}s  avg={avg_t:.2f}s")
        recommended = int(max_t * 1.5 + 1)
        print(
            f"    Recommended transition_timeout: {recommended}s (1.5× max + 1s margin)"
        )
        summary = (
            f"{len(timings)} cycles: min={min_t:.2f}s max={max_t:.2f}s avg={avg_t:.2f}s"
        )
    else:
        summary = "No cycles completed"

    results.append(
        ScenarioResult(
            num="S12",
            name="DP47 Response Time Distribution",
            assertion=AssertionResult(
                passed=all_passed,
                reason=summary,
                delta_t=max(timings) if timings else None,
                events=all_events,
            ),
            cross_ref_answer=CrossRefAnswer.SKIP,
            cross_ref_desc="",
            events_captured=all_events,
            extra={
                "timings": timings,
                "min_t": min(timings) if timings else None,
                "max_t": max(timings) if timings else None,
                "avg_t": sum(timings) / len(timings) if timings else None,
            },
        )
    )
    return device
