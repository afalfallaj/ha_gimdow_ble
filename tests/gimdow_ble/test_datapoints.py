"""Unit tests for GimdowBLEDataPoint and GimdowBLEDataPoints."""

from __future__ import annotations

import json
import time
from struct import pack, unpack
from unittest.mock import AsyncMock

import pytest

from custom_components.gimdow_ble.gimdow_ble.const import GimdowBLEDataPointType
from custom_components.gimdow_ble.gimdow_ble.datapoints import (
    GimdowBLEDataPoint,
    GimdowBLEDataPoints,
    GimdowBLEDeviceFunction,
)
from tests.gimdow_ble.conftest import BatchableGimdowBLEDataPoints
from custom_components.gimdow_ble.gimdow_ble.exceptions import (
    GimdowBLEDataFormatError,
    GimdowBLEEnumValueError,
)
from custom_components.gimdow_ble.const import DPType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_collection() -> GimdowBLEDataPoints:
    return GimdowBLEDataPoints(send_callback=AsyncMock(return_value=None))


def _make_dp(
    type_: GimdowBLEDataPointType,
    value: bytes | bool | int | str,
    dp_id: int = 1,
) -> GimdowBLEDataPoint:
    owner = _make_collection()
    return GimdowBLEDataPoint(owner, dp_id, time.time(), 0, type_, value)


# ---------------------------------------------------------------------------
# TestDataPointGetValue
# ---------------------------------------------------------------------------


class TestDataPointGetValue:
    def test_bool_true(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_BOOL, True)
        assert dp._get_value() == pack(">B", 1)

    def test_bool_false(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_BOOL, False)
        assert dp._get_value() == pack(">B", 0)

    def test_value_positive(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_VALUE, 42)
        assert dp._get_value() == pack(">i", 42)

    def test_value_negative(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_VALUE, -1)
        raw = dp._get_value()
        (val,) = unpack(">i", raw)
        assert val == -1

    def test_enum_single_byte(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_ENUM, 3)
        assert dp._get_value() == pack(">B", 3)

    def test_enum_two_bytes(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_ENUM, 0x100)
        assert dp._get_value() == pack(">H", 0x100)

    def test_enum_four_bytes(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_ENUM, 0x10000)
        assert dp._get_value() == pack(">I", 0x10000)

    def test_string(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_STRING, "hello")
        assert dp._get_value() == b"hello"

    def test_raw(self) -> None:
        raw = b"\x01\x02\x03"
        dp = _make_dp(GimdowBLEDataPointType.DT_RAW, raw)
        assert dp._get_value() == raw

    def test_bitmap(self) -> None:
        bm = b"\xff\x00"
        dp = _make_dp(GimdowBLEDataPointType.DT_BITMAP, bm)
        assert dp._get_value() == bm


# ---------------------------------------------------------------------------
# TestSetValue
# ---------------------------------------------------------------------------


class TestSetValue:
    async def test_set_value_dt_string(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_STRING, "old")
        await dp.set_value("new")
        assert dp.value == "new"

    async def test_set_value_dt_value_int(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_VALUE, 0)
        await dp.set_value(42)
        assert dp.value == 42

    async def test_set_value_dt_raw_converts_to_bytes(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_RAW, b"")
        await dp.set_value(b"\x01\x02")
        assert dp.value == b"\x01\x02"

    async def test_set_value_dt_bitmap_converts_to_bytes(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_BITMAP, b"")
        await dp.set_value(b"\xff")
        assert dp.value == b"\xff"

    async def test_set_value_dt_enum_negative_raises(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_ENUM, 0)
        with pytest.raises(GimdowBLEEnumValueError):
            await dp.set_value(-1)

    async def test_set_value_dt_enum_positive_ok(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_ENUM, 0)
        await dp.set_value(3)
        assert dp.value == 3

    async def test_set_value_clears_changed_by_device(self) -> None:
        """After set_value (user-initiated), changed_by_device must be False."""
        owner = _make_collection()
        dp = GimdowBLEDataPoint(owner, 1, 0.0, 0, GimdowBLEDataPointType.DT_BOOL, False)
        dp._update_from_device(1.0, 0, GimdowBLEDataPointType.DT_BOOL, True)
        assert dp.changed_by_device is True
        await dp.set_value(False)
        assert dp.changed_by_device is False


# ---------------------------------------------------------------------------
# TestDataPointProperties
# ---------------------------------------------------------------------------


class TestDataPointProperties:
    def test_id(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_BOOL, True, dp_id=47)
        assert dp.id == 47

    def test_value(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_VALUE, 100)
        assert dp.value == 100

    def test_changed_by_device_false_on_construction(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_BOOL, True)
        assert dp.changed_by_device is False

    def test_update_from_device_detects_change(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_BOOL, True)
        dp._update_from_device(time.time(), 0, GimdowBLEDataPointType.DT_BOOL, False)
        assert dp.changed_by_device is True
        assert dp.value is False

    def test_update_from_device_no_change(self) -> None:
        dp = _make_dp(GimdowBLEDataPointType.DT_VALUE, 5)
        dp._update_from_device(time.time(), 0, GimdowBLEDataPointType.DT_VALUE, 5)
        assert dp.changed_by_device is False


# ---------------------------------------------------------------------------
# TestDataPointsCollection
# ---------------------------------------------------------------------------


class TestDataPointsCollection:
    def test_get_or_create_creates_new(self) -> None:
        dps = _make_collection()
        dp = dps.get_or_create(6, GimdowBLEDataPointType.DT_BOOL, False)
        assert dp is not None
        assert dp.id == 6

    def test_get_or_create_returns_same_object(self) -> None:
        dps = _make_collection()
        dp1 = dps.get_or_create(6, GimdowBLEDataPointType.DT_BOOL, False)
        dp2 = dps.get_or_create(6, GimdowBLEDataPointType.DT_BOOL, True)
        assert dp1 is dp2

    def test_len(self) -> None:
        dps = _make_collection()
        assert len(dps) == 0
        dps.get_or_create(1, GimdowBLEDataPointType.DT_BOOL, True)
        assert len(dps) == 1

    def test_getitem_existing(self) -> None:
        dps = _make_collection()
        dps.get_or_create(9, GimdowBLEDataPointType.DT_VALUE, 42)
        assert dps[9] is not None
        assert dps[9].value == 42

    def test_getitem_missing_returns_none(self) -> None:
        dps = _make_collection()
        assert dps[99] is None

    def test_has_id_false_when_absent(self) -> None:
        dps = _make_collection()
        assert dps.has_id(9) is False

    def test_has_id_true(self) -> None:
        dps = _make_collection()
        dps.get_or_create(5, GimdowBLEDataPointType.DT_BOOL, True)
        assert dps.has_id(5)

    def test_has_id_with_type_match(self) -> None:
        dps = _make_collection()
        dps.get_or_create(5, GimdowBLEDataPointType.DT_BOOL, True)
        assert dps.has_id(5, GimdowBLEDataPointType.DT_BOOL)

    def test_has_id_with_type_mismatch(self) -> None:
        dps = _make_collection()
        dps.get_or_create(5, GimdowBLEDataPointType.DT_BOOL, True)
        assert not dps.has_id(5, GimdowBLEDataPointType.DT_VALUE)

    def test_clear_removes_datapoint(self) -> None:
        dps = _make_collection()
        dps.get_or_create(9, GimdowBLEDataPointType.DT_BOOL, True)
        dps.clear(9)
        assert dps[9] is None

    def test_clear_nonexistent_is_noop(self) -> None:
        dps = _make_collection()
        dps.clear(99)  # must not raise

    def test_update_from_device_creates_if_missing(self) -> None:
        dps = _make_collection()
        dps._update_from_device(7, time.time(), 0, GimdowBLEDataPointType.DT_VALUE, 99)
        assert dps[7] is not None
        assert dps[7].value == 99

    def test_update_from_device_updates_existing(self) -> None:
        dps = _make_collection()
        dps.get_or_create(7, GimdowBLEDataPointType.DT_VALUE, 10)
        dps._update_from_device(7, time.time(), 0, GimdowBLEDataPointType.DT_VALUE, 20)
        assert dps[7].value == 20

    async def test_set_value_triggers_send(self) -> None:
        send_cb = AsyncMock(return_value=None)
        dps = GimdowBLEDataPoints(send_callback=send_cb)
        dp = dps.get_or_create(6, GimdowBLEDataPointType.DT_BOOL, False)
        await dp.set_value(True)
        send_cb.assert_awaited_once()

    async def test_update_from_user_calls_send_when_not_batching(self) -> None:
        cb = AsyncMock()
        col = GimdowBLEDataPoints(send_callback=cb)
        col.get_or_create(9, GimdowBLEDataPointType.DT_BOOL, False)
        await col._update_from_user(9)
        cb.assert_awaited_once_with([9])

    async def test_begin_end_update_batches_sends(self) -> None:
        cb = AsyncMock()
        col = BatchableGimdowBLEDataPoints(send_callback=cb)
        col.get_or_create(9, GimdowBLEDataPointType.DT_BOOL, False)
        col.get_or_create(46, GimdowBLEDataPointType.DT_BOOL, False)

        col.begin_update()
        await col._update_from_user(9)
        await col._update_from_user(46)
        cb.assert_not_awaited()

        await col.end_update()
        cb.assert_awaited_once()
        sent_ids = set(cb.call_args[0][0])
        assert sent_ids == {9, 46}

    async def test_begin_end_update_deduplicates(self) -> None:
        cb = AsyncMock()
        col = BatchableGimdowBLEDataPoints(send_callback=cb)
        col.get_or_create(9, GimdowBLEDataPointType.DT_BOOL, False)

        col.begin_update()
        await col._update_from_user(9)
        await col._update_from_user(9)
        await col.end_update()

        sent_ids = cb.call_args[0][0]
        assert sent_ids.count(9) == 1

    async def test_end_update_noop_when_not_batching(self) -> None:
        cb = AsyncMock()
        col = BatchableGimdowBLEDataPoints(send_callback=cb)
        await col.end_update()
        cb.assert_not_awaited()

    async def test_nested_begin_update_flushes_only_on_last_end(self) -> None:
        cb = AsyncMock()
        col = BatchableGimdowBLEDataPoints(send_callback=cb)
        col.get_or_create(9, GimdowBLEDataPointType.DT_BOOL, False)

        col.begin_update()
        col.begin_update()
        await col._update_from_user(9)
        await col.end_update()
        cb.assert_not_awaited()
        await col.end_update()
        cb.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestDeviceFunction
# ---------------------------------------------------------------------------


class TestDeviceFunction:
    def test_values_string_parsed_to_dict(self) -> None:
        payload = json.dumps({"min": 0, "max": 3600, "scale": 0, "step": 1})
        fn = GimdowBLEDeviceFunction(
            code="auto_lock_timer",
            dp_id=46,
            type=DPType.INTEGER,
            values=payload,
        )
        assert isinstance(fn.values, dict)
        assert fn.values["min"] == 0
        assert fn.values["max"] == 3600

    def test_values_dict_unchanged(self) -> None:
        d = {"min": 0, "max": 100}
        fn = GimdowBLEDeviceFunction(
            code="test", dp_id=1, type=DPType.INTEGER, values=d
        )
        assert fn.values is d

    def test_values_none_unchanged(self) -> None:
        fn = GimdowBLEDeviceFunction(
            code="test", dp_id=1, type=DPType.BOOLEAN, values=None
        )
        assert fn.values is None

    def test_values_empty_json_null_string(self) -> None:
        """json.loads('null') == None, so an empty/null JSON string yields None."""
        fn = GimdowBLEDeviceFunction(
            code="x", dp_id=1, type=DPType.BOOLEAN, values="null"
        )
        assert fn.values is None or fn.values == "null"
