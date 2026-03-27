"""Gimdow BLE datapoint model — GimdowBLEDataPoint and GimdowBLEDataPoints.

Contains no BLE or HA dependencies; pure Python data model.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from struct import pack
from typing import Any

from ..const import DPType
from .const import GimdowBLEDataPointType
from .exceptions import GimdowBLEEnumValueError, GimdowBLEDataFormatError

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entity / function descriptions (cloud schema)
# ---------------------------------------------------------------------------

class GimdowBLEEntityDescription:
    """Extra schema info merged on top of cloud device description."""

    function: list[dict[str, dict]] | None = None
    status_range: list[dict[str, dict]] | None = None
    values_overrides: dict[str, dict] | None = None
    values_defaults: dict[str, dict] | None = None


@dataclass
class GimdowBLEDeviceFunction:
    """Single Tuya function/status-range entry from the cloud."""

    code: str
    dp_id: int
    type: DPType
    values: str | dict | list | None

    def __setattr__(self, name: str, value: str | dict | list | None) -> None:
        if name == "values" and isinstance(value, str):
            parsed = json.loads(value)
            if parsed:
                value = parsed
        super().__setattr__(name, value)


# ---------------------------------------------------------------------------
# GimdowBLEDataPoint
# ---------------------------------------------------------------------------

class GimdowBLEDataPoint:
    """Single datapoint value container.

    Tracks whether the value was changed by the device (push notification)
    vs by the user (HA command).
    """

    def __init__(
        self,
        owner: GimdowBLEDataPoints,
        id: int,
        timestamp: float,
        flags: int,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str,
    ) -> None:
        self._owner = owner
        self._id = id
        self._value = value
        self._changed_by_device = False
        self._update_from_device(timestamp, flags, type, value)

    # ------------------------------------------------------------------
    # Internal update
    # ------------------------------------------------------------------

    def _update_from_device(
        self,
        timestamp: float,
        flags: int,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str,
    ) -> None:
        self._timestamp = timestamp
        self._flags = flags
        self._type = type
        self._changed_by_device = self._value != value
        self._value = value

    def _get_value(self) -> bytes:
        """Serialise current value to bytes for transmission."""
        match self._type:
            case GimdowBLEDataPointType.DT_RAW | GimdowBLEDataPointType.DT_BITMAP:
                return self._value
            case GimdowBLEDataPointType.DT_BOOL:
                return pack(">B", 1 if self._value else 0)
            case GimdowBLEDataPointType.DT_VALUE:
                return pack(">i", self._value)
            case GimdowBLEDataPointType.DT_ENUM:
                if self._value > 0xFFFF:
                    return pack(">I", self._value)
                elif self._value > 0xFF:
                    return pack(">H", self._value)
                else:
                    return pack(">B", self._value)
            case GimdowBLEDataPointType.DT_STRING:
                return self._value.encode()
            case _:
                raise GimdowBLEDataFormatError()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def id(self) -> int:
        return self._id

    @property
    def timestamp(self) -> float:
        return self._timestamp

    @property
    def flags(self) -> int:
        return self._flags

    @property
    def type(self) -> GimdowBLEDataPointType:
        return self._type

    @property
    def value(self) -> bytes | bool | int | str:
        return self._value

    @property
    def changed_by_device(self) -> bool:
        return self._changed_by_device

    def __repr__(self) -> str:
        return f"{{id:{self.id} type:{self.type} value:{self.value}}}"

    def __str__(self) -> str:
        return repr(self)

    # ------------------------------------------------------------------
    # User-initiated update
    # ------------------------------------------------------------------

    async def set_value(self, value: bytes | bool | int | str) -> None:
        """Set value from HA and enqueue a send to the device."""
        match self._type:
            case GimdowBLEDataPointType.DT_RAW | GimdowBLEDataPointType.DT_BITMAP:
                self._value = bytes(value)
            case GimdowBLEDataPointType.DT_BOOL:
                self._value = bool(value)
            case GimdowBLEDataPointType.DT_VALUE:
                self._value = int(value)
            case GimdowBLEDataPointType.DT_ENUM:
                value = int(value)
                if value >= 0:
                    self._value = value
                else:
                    raise GimdowBLEEnumValueError()
            case GimdowBLEDataPointType.DT_STRING:
                self._value = str(value)
            case _:
                raise GimdowBLEDataFormatError()

        self._changed_by_device = False
        await self._owner._update_from_user(self._id)


# ---------------------------------------------------------------------------
# GimdowBLEDataPoints (collection)
# ---------------------------------------------------------------------------

class GimdowBLEDataPoints:
    """Collection of all known datapoints for a device.

    Args:
        send_callback: async callable that pushes a list of DP IDs to the device.
                       Typically ``GimdowBLEDevice._send_datapoints``.
    """

    def __init__(
        self,
        send_callback: Callable[[list[int]], Awaitable[None]],
    ) -> None:
        self._send_callback = send_callback
        self._datapoints: dict[int, GimdowBLEDataPoint] = {}
        self._update_started: int = 0
        self._updated_datapoints: list[int] = []

    # ------------------------------------------------------------------
    # Collection protocol
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._datapoints)

    def __getitem__(self, key: int) -> GimdowBLEDataPoint | None:
        return self._datapoints.get(key)

    def has_id(self, id: int, type: GimdowBLEDataPointType | None = None) -> bool:
        return (id in self._datapoints) and (
            (type is None) or (self._datapoints[id].type == type)
        )

    def get_or_create(
        self,
        id: int,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str | None = None,
    ) -> GimdowBLEDataPoint:
        dp = self._datapoints.get(id)
        if dp:
            return dp
        dp = GimdowBLEDataPoint(self, id, time.time(), 0, type, value)
        self._datapoints[id] = dp
        return dp

    # ------------------------------------------------------------------
    # Batch-update context manager
    # ------------------------------------------------------------------

    def begin_update(self) -> None:
        """Start batching DP writes — call end_update() to flush."""
        self._update_started += 1

    async def end_update(self) -> None:
        """Flush any pending DP writes accumulated during begin_update()."""
        if self._update_started > 0:
            self._update_started -= 1
            if self._update_started == 0 and len(self._updated_datapoints) > 0:
                await self._send_callback(self._updated_datapoints)
                self._updated_datapoints = []

    # ------------------------------------------------------------------
    # Internal update paths
    # ------------------------------------------------------------------

    def _update_from_device(
        self,
        dp_id: int,
        timestamp: float,
        flags: int,
        type: GimdowBLEDataPointType,
        value: bytes | bool | int | str,
    ) -> None:
        dp = self._datapoints.get(dp_id)
        if dp:
            dp._update_from_device(timestamp, flags, type, value)
        else:
            self._datapoints[dp_id] = GimdowBLEDataPoint(
                self, dp_id, timestamp, flags, type, value
            )

    async def _update_from_user(self, dp_id: int) -> None:
        if self._update_started > 0:
            if dp_id in self._updated_datapoints:
                self._updated_datapoints.remove(dp_id)
            self._updated_datapoints.append(dp_id)
        else:
            await self._send_callback([dp_id])
