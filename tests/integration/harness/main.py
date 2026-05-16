from __future__ import annotations

import asyncio
import datetime
import time

from custom_components.gimdow_ble.gimdow_ble.device import GimdowBLEDevice

from . import state
from .credentials import load_credentials, StandaloneManager
from .ble import find_device, make_callbacks, _wire_callbacks, event_printer
from .scenarios import (
    scenario_stability,
    scenario_1,
    scenario_2,
    scenario_3,
    scenario_4,
    scenario_5,
    scenario_6,
    scenario_9,
    scenario_10,
    scenario_11,
    scenario_12,
    ScenarioResult,
    META,
)
from .report import generate_report

# Ordered dispatch list: (scenario_id, coroutine_function)
_SCENARIOS = [
    ("S0", scenario_stability),
    ("S1", scenario_1),
    ("S2", scenario_2),
    ("S3", scenario_3),
    ("S4", scenario_4),
    ("S5", scenario_5),
    ("S6", scenario_6),
    ("S9", scenario_9),
    ("S10", scenario_10),
    ("S11", scenario_11),
    ("S12", scenario_12),
]


async def main(
    dotenv_source: str = "unknown",
    scenario_filter: set[str] | None = None,
) -> None:
    active = (
        [sid for sid, _ in _SCENARIOS if sid in scenario_filter]
        if scenario_filter is not None
        else [sid for sid, _ in _SCENARIOS]
    )

    print("=" * 66)
    print("  Gimdow BLE Hardware Integration Tests")
    print(f"  Scenarios: {', '.join(active)}")
    print(f"  Env file:  {dotenv_source}")
    print("=" * 66)

    # 1. Credentials
    creds, mac = await load_credentials()
    manager = StandaloneManager(creds)

    # 2. BLE discovery
    ble_device = await find_device(mac)
    # os_address is the platform-native address (CoreBluetooth UUID on macOS,
    # real MAC on Linux/Windows) — use this for all reconnections.
    os_address = ble_device.address

    # 3. Device init
    device: GimdowBLEDevice = GimdowBLEDevice(manager, ble_device)
    await device.initialize()

    # 4. Event infrastructure (must init after event loop is running)
    state.event_queue = asyncio.Queue()
    state._assert_queue = asyncio.Queue()
    state.last_dp_values = {}
    state.session_start = time.monotonic()
    session_start_dt = datetime.datetime.now()
    device_ref: list = [device]

    on_dp, on_conn, on_disc = make_callbacks(device_ref)
    _wire_callbacks(device, on_dp, on_conn, on_disc)

    printer_task = asyncio.create_task(event_printer())

    # 5. Scenario context passed as kwargs
    ctx: dict = dict(
        mac=mac,
        os_address=os_address,
        manager=manager,
        on_dp=on_dp,
        on_conn=on_conn,
        on_disc=on_disc,
        device_ref=device_ref,
    )

    results: list[ScenarioResult] = []
    try:
        for sid, fn in _SCENARIOS:
            if scenario_filter is not None and sid not in scenario_filter:
                continue
            device = await fn(device, results, **ctx)
    finally:
        try:
            await device.stop()
        except Exception:
            pass
        printer_task.cancel()
        try:
            await printer_task
        except asyncio.CancelledError:
            pass

    elapsed = time.monotonic() - state.session_start
    generate_report(device, results, session_start_dt, elapsed)
