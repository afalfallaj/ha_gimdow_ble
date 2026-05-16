"""
Gimdow BLE hardware integration tests.
Runs without Home Assistant — pure asyncio + bleak on macOS / Linux / Windows.

Setup:
  cp tests/integration/.env.example tests/integration/.env
  # fill in your credentials

Run:
  python tests/integration/run.py               # full suite
  python tests/integration/run.py --quick       # S2 + S3 only (lock/unlock sanity)
  python tests/integration/run.py --scenarios S2,S3,S11

Dependencies:
  pip install bleak bleak-retry-connector tuya-iot-py-sdk pycryptodome python-dotenv
  # or with uv:
  uv run --with bleak --with bleak-retry-connector --with tuya-iot-py-sdk \
         --with pycryptodome --with python-dotenv --with typing-extensions \
         python tests/integration/run.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))  # tests/integration/
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))  # repo root

sys.path.insert(0, _HERE)  # so 'harness.*' is importable
sys.path.insert(0, _REPO_ROOT)  # so 'custom_components.*' is importable


# Stub out HA-dependent package __init__.py files so their top-level imports
# (homeassistant.*, lock_manager) are never executed.
def _stub_package(name: str, real_path: str) -> None:
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    mod.__path__ = [real_path]  # type: ignore[attr-defined]
    mod.__package__ = name
    mod.__file__ = os.path.join(real_path, "__init__.py")
    sys.modules[name] = mod


_stub_package(
    "custom_components.gimdow_ble",
    os.path.join(_REPO_ROOT, "custom_components", "gimdow_ble"),
)
_stub_package(
    "custom_components.gimdow_ble.gimdow_ble",
    os.path.join(_REPO_ROOT, "custom_components", "gimdow_ble", "gimdow_ble"),
)

try:
    import dotenv as _dotenv

    _loaded = _dotenv.load_dotenv(
        dotenv_path=os.path.join(_HERE, ".env"), override=False
    )
    _example = _dotenv.load_dotenv(
        dotenv_path=os.path.join(_HERE, ".env.example"), override=False
    )
    _dotenv_source = ".env" if _loaded else ".env.example" if _example else "none found"
except ImportError:
    _dotenv_source = "python-dotenv not installed"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("custom_components.gimdow_ble").setLevel(logging.WARNING)

from harness.main import main  # noqa: E402 — imports after path/stub setup


def _parse_args() -> tuple[set[str] | None, str]:
    parser = argparse.ArgumentParser(
        description="Gimdow BLE hardware integration tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
scenarios:
  S0   Connection stability (125s idle hold)
  S1   Initial connect + state read
  S2   BLE unlock command
  S3   BLE lock command
  S4   Manual action push (unlock + lock)
  S5   Autonomous periodic push (DP9)
  S6   Reconnect — state unchanged        [HARDWARE_LIMIT]
  S9   Double-command echo pattern
  S10  Back-to-back command serialization
  S11  Post-reconnect state recovery
  S12  DP47 response time distribution (3 cycles)
""",
    )
    parser.add_argument(
        "--scenarios",
        metavar="IDs",
        help="Comma-separated scenario IDs to run (e.g. S2,S3,S11)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run S2 and S3 only — basic lock/unlock sanity check (~2 min)",
    )
    args = parser.parse_args()

    if args.quick:
        return {"S2", "S3"}, _dotenv_source
    if args.scenarios:
        ids = {s.strip().upper() for s in args.scenarios.split(",") if s.strip()}
        return ids, _dotenv_source
    return None, _dotenv_source  # None = run all


if __name__ == "__main__":
    scenario_filter, dotenv_source = _parse_args()
    asyncio.run(main(dotenv_source, scenario_filter))
