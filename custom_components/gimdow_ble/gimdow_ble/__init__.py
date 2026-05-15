from __future__ import annotations

from .const import GimdowBLEDataPointType, SERVICE_UUID
from .device import GimdowBLEDevice
from .datapoints import GimdowBLEDataPoint, GimdowBLEDataPoints, GimdowBLEDeviceFunction, GimdowBLEEntityDescription
from .manager import AbstaractGimdowBLEDeviceManager, GimdowBLEDeviceCredentials
from .lock_manager import GimdowBLELockManager, LockBlockedReason, PendingLockIntent
from .diagnostics import GimdowBLEDiagContext
from .exceptions import (
    GimdowBLEError,
    GimdowBLETimeoutError,
    GimdowBLEEchoTimeoutError,
    GimdowBLEStateTimeoutError,
    GimdowBLEConnectionError,
    GimdowBLEResolutionAbortedError,
)

import json as _json
import pathlib as _pathlib

__version__: str = _json.loads(
    (_pathlib.Path(__file__).parent.parent / "manifest.json").read_text()
)["version"]

__all__ = [
    "AbstaractGimdowBLEDeviceManager",
    "GimdowBLEDataPoint",
    "GimdowBLEDataPoints",
    "GimdowBLEDataPointType",
    "GimdowBLEDevice",
    "GimdowBLEDeviceCredentials",
    "GimdowBLEDeviceFunction",
    "GimdowBLEEntityDescription",
    "GimdowBLELockManager",
    "GimdowBLEDiagContext",
    "GimdowBLEError",
    "GimdowBLETimeoutError",
    "GimdowBLEEchoTimeoutError",
    "GimdowBLEStateTimeoutError",
    "GimdowBLEConnectionError",
    "GimdowBLEResolutionAbortedError",
    "LockBlockedReason",
    "PendingLockIntent",
    "SERVICE_UUID",
]
