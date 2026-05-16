from __future__ import annotations

import datetime
from typing import Any

from custom_components.gimdow_ble.gimdow_ble.device import GimdowBLEDevice

from .ble import BLEEvent, format_event
from .assertions import CrossRefAnswer
from .scenarios import ScenarioResult, _find_result


def generate_report(
    device: GimdowBLEDevice,
    results: list[ScenarioResult],
    session_start_dt: datetime.datetime,
    elapsed: float,
) -> None:
    sep = "=" * 66
    dash = "-" * 66

    lk = getattr(device, "local_key", "????")
    lk_redacted = f"{lk[:4]}…{lk[-4:]}" if len(lk) >= 8 else "????"

    print(f"\n{sep}")
    print("  GIMDOW BLE DEVICE TEST REPORT")
    print(sep)
    print(f"  Device:    {device.address}")
    print(
        f"  Model:     {getattr(device, 'product_model', '?')}   "
        f"product_id={getattr(device, 'product_id', '?')}"
    )
    print(
        f"  Firmware:  {getattr(device, 'device_version', '?')}  "
        f"protocol={getattr(device, 'protocol_version', '?')}"
    )
    print(f"  Key:       {lk_redacted}  (redacted)")
    print(
        f"  Session:   {session_start_dt.strftime('%Y-%m-%d %H:%M:%S')}   duration={elapsed:.0f}s"
    )

    print()
    print("SCENARIO VERDICTS")
    print(dash)

    verdict_order = [
        ("S0", "Connection Stability"),
        ("S1", "Initial State Read"),
        ("S2", "BLE Unlock Command"),
        ("S3", "BLE Lock Command"),
        ("S4a", "Manual Push -> UNLOCK"),
        ("S4b", "Manual Push -> LOCK"),
        ("S5", "Autonomous Push (DP9)"),
        ("S6", "Reconnect — State Unchanged"),
        ("S9", "Double-Command Echo"),
        ("S10", "Back-to-Back Serialization"),
        ("S11", "Post-Reconnect Recovery"),
        ("S12", "DP47 Response Times"),
    ]
    for num, label in verdict_order:
        r = _find_result(results, num)
        if r is None:
            print(f"  [{num:>3}]  {label:<35}  -- SKIPPED")
            continue
        if r.assertion is None:
            print(f"  [{num:>3}]  {label:<35}  SEE FINDINGS")
        elif r.assertion.hardware_limit:
            print(
                f"  [{num:>3}]  {label:<35}  [~~] HARDWARE_LIMIT — {r.assertion.reason}"
            )
        else:
            mark = "OK" if r.assertion.passed else "!!"
            print(f"  [{num:>3}]  {label:<35}  [{mark}] {r.assertion.reason}")

    print()
    print("BLE / PHYSICAL MISMATCHES  (findings — not verdict flips)")
    print(dash)
    mismatches = [r for r in results if r.cross_ref_answer == CrossRefAnswer.MISMATCH]
    if mismatches:
        for r in mismatches:
            print(f"  Scenario {r.num}: {r.name}")
            print(f"    Physical observation: {r.cross_ref_desc}")
    else:
        print("  (none)")

    print()
    print("KEY FINDINGS  (derived from BLE evidence)")
    print(dash)

    def _both_passed(na: str, nb: str) -> str:
        ra, rb = _find_result(results, na), _find_result(results, nb)
        if ra is None and rb is None:
            return "NOT RUN"
        # hardware_limit results are not true PASS — device limitation
        a_hl = (
            ra is not None and ra.assertion is not None and ra.assertion.hardware_limit
        )
        b_hl = (
            rb is not None and rb.assertion is not None and rb.assertion.hardware_limit
        )
        if a_hl or b_hl:
            return "NO (hardware limitation)"
        a_ok = ra is not None and ra.assertion is not None and ra.assertion.passed
        b_ok = rb is not None and rb.assertion is not None and rb.assertion.passed
        if a_ok and b_ok:
            return "YES"
        if not a_ok and not b_ok:
            return "NO"
        return f"DIRECTIONAL ({'->UNLOCK' if a_ok else '->LOCK'} only)"

    push_manual = _both_passed("S4a", "S4b")

    r6 = _find_result(results, "S6")
    if r6 is None:
        push_reconnect = "NOT RUN"
    elif r6.assertion and r6.assertion.hardware_limit:
        push_reconnect = "NO (hardware limitation)"
    elif r6.assertion and r6.assertion.passed:
        push_reconnect = "YES (unexpected)"
    else:
        push_reconnect = "NO"

    r5 = _find_result(results, "S5")
    if not r5:
        poll_str = "NOT RUN"
    elif r5.assertion and r5.assertion.passed:
        interval = r5.extra.get("push_interval")
        interval_str = f"{interval:.1f}s" if interval is not None else "unknown"
        poll_str = f"YES — DP9 at t={interval_str}"
    else:
        poll_str = "NO — DP9 not received within 60s"

    r2 = _find_result(results, "S2")
    r3 = _find_result(results, "S3")
    unlock_lat = (
        f"{r2.assertion.delta_t:.2f}s"
        if r2 and r2.assertion and r2.assertion.delta_t is not None
        else "N/A"
    )
    lock_lat = (
        f"{r3.assertion.delta_t:.2f}s"
        if r3 and r3.assertion and r3.assertion.delta_t is not None
        else "N/A"
    )

    rs0 = _find_result(results, "S0")
    stability = "N/A (not run)"
    if rs0 and rs0.assertion:
        stability = (
            "PASS — session renewal working"
            if rs0.assertion.passed
            else rs0.assertion.reason
        )

    dp19_str = (
        "YES"
        if (r2 and r2.extra.get("dp19_fired"))
        else "NO (DP6 may not trigger unlock_ble record)"
        if r2
        else "NOT RUN"
    )

    r12 = _find_result(results, "S12")
    timing_str = "NOT RUN"
    if r12 and r12.extra.get("timings"):
        mn = r12.extra.get("min_t")
        mx = r12.extra.get("max_t")
        av = r12.extra.get("avg_t")
        timing_str = f"min={mn:.2f}s  max={mx:.2f}s  avg={av:.2f}s → timeout={int(mx * 1.5 + 1)}s"

    print(f"  Connection stability (S0):            {stability}")
    print(f"  Device pushes DP47 on manual op:      {push_manual}")
    print(f"  Device pushes DP47 on reconnect:      {push_reconnect}")
    print(f"  Autonomous DP9 push observed:         {poll_str}")
    print(f"  DP19 (unlock_ble) fires on DP6:       {dp19_str}")
    print(
        f"  Command echo latency:                 lock={lock_lat}  unlock={unlock_lat}"
    )
    print(f"  Motor response distribution (S12):    {timing_str}")

    print(sep)
