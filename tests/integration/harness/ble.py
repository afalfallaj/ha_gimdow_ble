from __future__ import annotations

import asyncio
import dataclasses
import platform as _platform
import time
from typing import Any

from bleak import BleakScanner

from custom_components.gimdow_ble.gimdow_ble.device import GimdowBLEDevice
from custom_components.gimdow_ble.gimdow_ble.const import SERVICE_UUID
from custom_components.gimdow_ble.gimdow_ble.datapoints import GimdowBLEDataPoint

from . import state
from .credentials import StandaloneManager

# DP reference from Tuya device specification (functions + status arrays).
# functions = writable (commands); status = reportable (push/poll).
# DP6 is absent from both — undocumented shortcut used by this firmware.
DP_NAME: dict[int, str] = {
    1: "unlock_method_create",
    2: "unlock_method_delete",
    3: "unlock_method_modify",
    9: "battery_state",
    16: "unlock_key",
    19: "unlock_ble",
    20: "lock_record",
    21: "alarm_lock",
    31: "beep_volume",
    33: "automatic_lock",
    36: "auto_lock_time",
    44: "rtc_lock",
    46: "manual_lock",
    47: "lock_motor_state",
    54: "synch_method",
    61: "remote_no_dp_key",
    62: "unlock_phone_remote",
    63: "unlock_voice_remote",
    68: "special_function",
    69: "record",
    70: "check_code_set",
    71: "ble_unlock_check",
    72: "unlock_record_check",
    73: "remote_pd_setkey_check",
    78: "special_control",
}
# DP9 battery_state enum → approximate percentage string
DP9_BATTERY: dict[int, str] = {
    0: "~90% (high)",
    1: "~60% (normal)",
    2: "~30% (low)",
    3: "0% (poweroff)",
}


@dataclasses.dataclass
class BLEEvent:
    kind: str  # "dp" | "connected" | "disconnected"
    timestamp: float  # time.monotonic()
    dp_id: int | None = None
    dp_value: Any = None
    dp_prev_value: Any = None
    changed_by_device: bool = False
    rssi: int | None = None
    paired: bool = False


def _enqueue(ev: BLEEvent) -> None:
    """Push an event to both consumer queues atomically."""
    state.event_queue.put_nowait(ev)
    state._assert_queue.put_nowait(ev)


def make_callbacks(device_ref: list) -> tuple:
    """Return (on_dp, on_connected, on_disconnected) closures sharing device_ref[0]."""

    def on_dp_update(datapoints: list[GimdowBLEDataPoint]) -> None:
        for dp in datapoints:
            prev = state.last_dp_values.get(dp.id)
            state.last_dp_values[dp.id] = dp.value
            _enqueue(
                BLEEvent(
                    kind="dp",
                    timestamp=time.monotonic(),
                    dp_id=dp.id,
                    dp_value=dp.value,
                    dp_prev_value=prev,
                    changed_by_device=dp.changed_by_device,
                )
            )

    def on_connected() -> None:
        d = device_ref[0]
        _enqueue(
            BLEEvent(
                kind="connected",
                timestamp=time.monotonic(),
                rssi=getattr(d, "rssi", None),
                paired=getattr(d, "is_paired", False),
            )
        )

    def on_disconnected() -> None:
        _enqueue(
            BLEEvent(
                kind="disconnected",
                timestamp=time.monotonic(),
            )
        )

    return on_dp_update, on_connected, on_disconnected


def _wire_callbacks(
    device: GimdowBLEDevice, on_dp: Any, on_conn: Any, on_disc: Any
) -> None:
    device.register_callback(on_dp)
    device.register_connected_callback(on_conn)
    device.register_disconnected_callback(on_disc)


def format_event(ev: BLEEvent) -> str:
    t = ev.timestamp - state.session_start
    ts = f"[{t:8.3f}s]"
    if ev.kind == "dp":
        name = DP_NAME.get(ev.dp_id or 0, "")
        name_str = f"({name}) " if name else ""
        value_hint = ""
        if ev.dp_id == 47:
            value_hint = "  -> LOCKED" if ev.dp_value is False else "  -> UNLOCKED"
        elif ev.dp_id == 9:
            pct = DP9_BATTERY.get(ev.dp_value, "")
            if pct:
                value_hint = f"  -> {pct}"
        arrow = (
            f"{ev.dp_prev_value!r} -> {ev.dp_value!r}"
            if ev.dp_prev_value is not None
            else repr(ev.dp_value)
        )
        src = "device" if ev.changed_by_device else "host"
        return f"{ts}  DP{ev.dp_id:>3} {name_str}= {arrow:<28}{value_hint}  [{src}]"
    elif ev.kind == "connected":
        return f"{ts}  CONNECTED   rssi={ev.rssi} dBm  paired={ev.paired}"
    else:
        return f"{ts}  DISCONNECTED"


async def event_printer() -> None:
    while True:
        ev = await state.event_queue.get()
        # Suppress when assert_dp is active — it prints its own copy from _assert_queue
        if not state._assert_active:
            print(format_event(ev))
        state.event_queue.task_done()


def drain_queue() -> None:
    for q in (state.event_queue, state._assert_queue):
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break


async def find_device(mac: str) -> Any:
    """
    Locate the Gimdow device cross-platform.

    Linux / Windows: BLE stack exposes real MAC addresses.
      → find_device_by_address works directly; service-UUID scan as fallback.
    macOS: CoreBluetooth assigns its own UUIDs instead of MAC addresses.
      → Always scan by SERVICE_UUID; prefer exact MAC match if CoreBluetooth
        happens to expose it, otherwise accept the only (or user-chosen) device.

    After this call, use ble_device.address (the OS-native address) for all
    future reconnections — it works on every platform.
    """
    mac_norm = mac.upper().replace("-", ":")
    is_mac = _platform.system() == "Darwin"

    # On Linux/Windows try the fast direct-address lookup first
    if not is_mac:
        print(f"\n  Scanning for {mac_norm}…")
        device = await BleakScanner.find_device_by_address(mac_norm, timeout=15.0)
        if device is not None:
            print(f"  Found: {device.name or '(unnamed)'}  addr={device.address}")
            return device
        print("  Direct address lookup failed, falling back to service-UUID scan…")

    # Service-UUID scan — works on all platforms, required on macOS
    print(f"\n  Scanning for Gimdow service UUID{' (macOS mode)' if is_mac else ''}…")
    found: list = []

    def _cb(device: Any, adv: Any) -> None:
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if SERVICE_UUID.lower() in uuids:
            if not any(d.address == device.address for d, _ in found):
                found.append((device, adv))

    async with BleakScanner(detection_callback=_cb):
        await asyncio.sleep(10.0)

    if not found:
        hints = (
            "  • macOS: grant Bluetooth permission in System Settings → Privacy & Security → Bluetooth\n"
            if is_mac
            else "  • Linux: ensure the BLE adapter is up (bluetoothctl power on)\n"
            "  • Windows: check that Bluetooth is enabled in Settings\n"
        )
        raise RuntimeError(
            f"No Gimdow device found within 10s.\n{hints}"
            "  • Make sure the lock is powered on and within range."
        )

    # Prefer exact MAC match (reliable on Linux/Windows, sometimes works on macOS too)
    for device, _ in found:
        if device.address.upper() == mac_norm:
            print(
                f"  Found (exact match): {device.name or '(unnamed)'}  addr={device.address}"
            )
            return device

    if len(found) == 1:
        device, _ = found[0]
        if is_mac:
            print(f"  Found: {device.name or '(unnamed)'}  os_addr={device.address}")
            print(
                f"  (macOS uses CoreBluetooth UUIDs instead of MAC addresses — this is normal)"
            )
        else:
            print(f"  Found: {device.name or '(unnamed)'}  addr={device.address}")
        return device

    # Multiple Gimdow devices nearby — let user pick
    print(f"\n  Multiple Gimdow devices found:")
    for i, (d, _) in enumerate(found):
        print(f"    [{i + 1}] {d.name or '(unnamed)'}  addr={d.address}")
    choice = (await asyncio.to_thread(input, "  Which device? [1]: ")).strip()
    idx = (int(choice) - 1) if choice.isdigit() else 0
    device, _ = found[max(0, min(idx, len(found) - 1))]
    return device


async def reconnect(
    os_address: str,
    mac: str,
    manager: StandaloneManager,
    on_dp: Any,
    on_conn: Any,
    on_disc: Any,
    device_ref: list,
) -> GimdowBLEDevice:
    """
    Reconnect cross-platform.
    os_address: ble_device.address from initial discovery (CoreBluetooth UUID on macOS,
                real MAC on Linux/Windows). This always works with find_device_by_address.
    mac:        cloud/env MAC, used only as fallback if os_address lookup fails.
    """
    print("  Reconnecting…")
    ble_device = await BleakScanner.find_device_by_address(os_address, timeout=15.0)
    if ble_device is None:
        print(
            f"  Address lookup failed for {os_address}, falling back to service-UUID scan…"
        )
        ble_device = await find_device(mac)
    new_device = GimdowBLEDevice(manager, ble_device)
    await new_device.initialize()
    _wire_callbacks(new_device, on_dp, on_conn, on_disc)
    device_ref[0] = new_device
    return new_device
