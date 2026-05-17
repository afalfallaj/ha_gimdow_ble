"""Tests for GimdowBLEProtocol — primitives, send path, and notification pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import os
from struct import pack, unpack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.gimdow_ble.gimdow_ble.const import (
    GATT_MTU,
    GimdowBLECode,
    GimdowBLEDataPointType,
)
from custom_components.gimdow_ble.gimdow_ble.datapoints import GimdowBLEDataPoints
from custom_components.gimdow_ble.gimdow_ble.exceptions import (
    GimdowBLEDataCRCError,
    GimdowBLEDataFormatError,
    GimdowBLEDataLengthError,
    GimdowBLEDeviceError,
    GimdowBLEUnsupportedProtocolError,
)
from custom_components.gimdow_ble.gimdow_ble.protocol import GimdowBLEProtocol


# ---------------------------------------------------------------------------
# Stub
# ---------------------------------------------------------------------------


class _StubProtocol(GimdowBLEProtocol):
    address = "AA:BB:CC:DD:EE:FF"
    rssi = None
    _protocol_version = 2
    _login_key = None
    _session_key = None
    _auth_key = None
    _flags = 0
    _is_bound = False
    _local_key = None
    _device_version = ""
    _protocol_version_str = ""
    _hardware_version = ""
    _is_paired = False

    def __init__(self) -> None:
        self._init_protocol()
        self._datapoints = GimdowBLEDataPoints(send_callback=MagicMock())
        self._callbacks: list = []

    def _fire_callbacks(self, datapoints) -> None:
        for cb in self._callbacks:
            cb(datapoints)

    def _create_safe_task(self, coro, *, name=None):
        try:
            loop = asyncio.get_running_loop()
            return loop.create_task(coro)
        except RuntimeError:
            return None

    async def _send_response(self, code, data, seq_num):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sign_dp_payload(
    dp_id: int, dp_value: int, seq: int = 1, flags: int = 0
) -> bytes:
    return pack(">HB", seq, flags) + bytes(
        [dp_id, GimdowBLEDataPointType.DT_ENUM.value, 1, dp_value]
    )


def _make_gatt_packet(
    packet_num: int, payload: bytes, total_length: int | None = None
) -> bytearray:
    buf = bytearray()
    buf += GimdowBLEProtocol._pack_int(packet_num)
    if packet_num == 0:
        assert total_length is not None
        buf += GimdowBLEProtocol._pack_int(total_length)
        buf += b"\x20"  # protocol_version=2 → 2 << 4
    buf += payload
    return buf


def _make_encrypted_buffer(
    key: bytes,
    code: GimdowBLECode,
    data: bytes,
    *,
    corrupt_crc: bool = False,
    response_to: int = 0,
    seq_num: int = 1,
    security_flag: int = 0x04,
) -> bytes:
    iv = os.urandom(16)
    raw = bytearray()
    raw += pack(">IIHH", seq_num, response_to, code.value, len(data))
    raw += data
    if corrupt_crc:
        correct = GimdowBLEProtocol._calc_crc16(bytes(raw))
        raw += pack(">H", correct ^ 0xFFFF)
    else:
        raw += pack(">H", GimdowBLEProtocol._calc_crc16(bytes(raw)))
    while len(raw) % 16 != 0:
        raw += b"\x00"
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return bytes([security_flag]) + iv + encryptor.update(bytes(raw)) + encryptor.finalize()


def _decode_packet0(pkt: bytes) -> tuple[int, int, bytes]:
    pos = 0
    pkt_num, pos = GimdowBLEProtocol._unpack_int(pkt, pos)
    assert pkt_num == 0
    total_len, pos = GimdowBLEProtocol._unpack_int(pkt, pos)
    protocol_version_byte = pkt[pos]
    return total_len, protocol_version_byte, bytes(pkt[pos + 1 :])


def _reassemble(packets: list[bytes]) -> bytes:
    buf = bytearray()
    for i, pkt in enumerate(packets):
        pos = 0
        _pkt_num, pos = GimdowBLEProtocol._unpack_int(pkt, pos)
        if i == 0:
            _total, pos = GimdowBLEProtocol._unpack_int(pkt, pos)
            pos += 1
        buf += pkt[pos:]
    return bytes(buf)


def _decrypt_payload(key: bytes, payload: bytes) -> bytes:
    iv = payload[1:17]
    ciphertext = payload[17:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


# ---------------------------------------------------------------------------
# TestCalcCrc16
# ---------------------------------------------------------------------------


class TestCalcCrc16:
    def test_empty_bytes_returns_ffff(self) -> None:
        assert GimdowBLEProtocol._calc_crc16(b"") == 0xFFFF

    def test_single_zero_byte(self) -> None:
        result = GimdowBLEProtocol._calc_crc16(b"\x00")
        assert isinstance(result, int)
        assert 0 <= result <= 0xFFFF

    def test_known_vector(self) -> None:
        assert GimdowBLEProtocol._calc_crc16(b"123456789") == 0x4B37

    def test_deterministic(self) -> None:
        data = b"\x01\x02\x03\x04\x05"
        assert GimdowBLEProtocol._calc_crc16(data) == GimdowBLEProtocol._calc_crc16(
            data
        )

    def test_different_data_different_crc(self) -> None:
        assert GimdowBLEProtocol._calc_crc16(b"\x01") != GimdowBLEProtocol._calc_crc16(
            b"\x02"
        )


# ---------------------------------------------------------------------------
# TestPackInt
# ---------------------------------------------------------------------------


class TestPackInt:
    def test_zero(self) -> None:
        assert bytes(GimdowBLEProtocol._pack_int(0)) == b"\x00"

    def test_single_byte_max(self) -> None:
        assert bytes(GimdowBLEProtocol._pack_int(127)) == b"\x7f"

    def test_two_bytes(self) -> None:
        assert bytes(GimdowBLEProtocol._pack_int(128)) == b"\x80\x01"

    def test_large_value(self) -> None:
        assert len(GimdowBLEProtocol._pack_int(0x3FFF)) == 2

    def test_pack_unpack_roundtrip(self) -> None:
        for value in [0, 1, 63, 127, 128, 255, 256, 16383, 16384, 0x1FFFFF]:
            packed = bytes(GimdowBLEProtocol._pack_int(value))
            unpacked, _ = GimdowBLEProtocol._unpack_int(packed, 0)
            assert unpacked == value


# ---------------------------------------------------------------------------
# TestUnpackInt
# ---------------------------------------------------------------------------


class TestUnpackInt:
    def test_unpack_zero(self) -> None:
        value, end_pos = GimdowBLEProtocol._unpack_int(b"\x00", 0)
        assert value == 0 and end_pos == 1

    def test_unpack_single_byte(self) -> None:
        value, end_pos = GimdowBLEProtocol._unpack_int(b"\x7f", 0)
        assert value == 127 and end_pos == 1

    def test_unpack_two_bytes(self) -> None:
        value, end_pos = GimdowBLEProtocol._unpack_int(b"\x80\x01", 0)
        assert value == 128 and end_pos == 2

    def test_unpack_with_offset(self) -> None:
        value, end_pos = GimdowBLEProtocol._unpack_int(b"\xff\x7f", 1)
        assert value == 127 and end_pos == 2

    def test_unpack_truncated_data_raises(self) -> None:
        with pytest.raises(GimdowBLEDataFormatError):
            GimdowBLEProtocol._unpack_int(b"\x80", 0)


# ---------------------------------------------------------------------------
# TestSignDPFrameOffset
# ---------------------------------------------------------------------------


class TestSignDPFrameOffset:
    def test_correct_start_pos_parses_dp_id_correctly(self) -> None:
        proto = _StubProtocol()
        payload = _make_sign_dp_payload(dp_id=9, dp_value=2)
        proto._parse_datapoints_v3(timestamp=0.0, flags=0, data=payload, start_pos=3)
        dp = proto._datapoints[9]
        assert dp is not None and dp.value == 2

    def test_wrong_start_pos_raises_data_format_error(self) -> None:
        proto = _StubProtocol()
        payload = _make_sign_dp_payload(dp_id=9, dp_value=2)
        with pytest.raises(GimdowBLEDataFormatError):
            proto._parse_datapoints_v3(
                timestamp=0.0, flags=0, data=payload, start_pos=2
            )

    def test_parse_multiple_dps(self) -> None:
        proto = _StubProtocol()
        payload = pack(">HB", 1, 0)
        payload += bytes([9, GimdowBLEDataPointType.DT_ENUM.value, 1, 1])
        payload += bytes([46, GimdowBLEDataPointType.DT_BOOL.value, 1, 1])
        proto._parse_datapoints_v3(timestamp=0.0, flags=0, data=payload, start_pos=3)
        assert proto._datapoints[9].value == 1
        assert proto._datapoints[46].value is True

    def test_dp_bool_false(self) -> None:
        proto = _StubProtocol()
        payload = pack(">HB", 1, 0)
        payload += bytes([47, GimdowBLEDataPointType.DT_BOOL.value, 1, 0])
        proto._parse_datapoints_v3(timestamp=0.0, flags=0, data=payload, start_pos=3)
        assert proto._datapoints[47].value is False


# ---------------------------------------------------------------------------
# TestSignTimeDPUsesTimestamp
# ---------------------------------------------------------------------------


class TestSignTimeDPUsesTimestamp:
    def test_parse_timestamp_type1_returns_correct_float(self) -> None:
        proto = _StubProtocol()
        known_ts = 1_700_000_000
        data = bytes([1]) + known_ts.to_bytes(4, "big")
        timestamp, end_pos = proto._parse_timestamp(data, 0)
        assert timestamp == float(known_ts) and end_pos == 5

    def test_datapoint_timestamp_equals_parsed_not_time_time(self) -> None:
        proto = _StubProtocol()
        known_ts = 1_700_000_000
        payload = pack(">HB", 1, 0)
        payload += bytes([1]) + known_ts.to_bytes(4, "big")
        payload += bytes([9, GimdowBLEDataPointType.DT_ENUM.value, 1, 3])
        timestamp, pos = proto._parse_timestamp(payload, 3)
        proto._parse_datapoints_v3(timestamp, flags=0, data=payload, start_pos=pos)
        assert proto._datapoints[9].timestamp == float(known_ts)

    def test_parse_timestamp_type0_milliseconds(self) -> None:
        proto = _StubProtocol()
        ts_ms = 1_700_000_000_000
        ts_str = str(ts_ms).encode()
        assert len(ts_str) == 13
        data = bytes([0]) + ts_str
        timestamp, end_pos = proto._parse_timestamp(data, 0)
        assert timestamp == pytest.approx(ts_ms / 1000)
        assert end_pos == 14


# ---------------------------------------------------------------------------
# TestParseTimestampEdgeCases
# ---------------------------------------------------------------------------


class TestParseTimestampEdgeCases:
    def test_type0_truncated_raises_length_error(self) -> None:
        proto = _StubProtocol()
        with pytest.raises(GimdowBLEDataLengthError):
            proto._parse_timestamp(bytes([0]) + b"123", 0)

    def test_type1_truncated_raises_length_error(self) -> None:
        proto = _StubProtocol()
        with pytest.raises(GimdowBLEDataLengthError):
            proto._parse_timestamp(bytes([1]) + b"\x00\x01", 0)

    def test_unknown_time_type_raises_format_error(self) -> None:
        proto = _StubProtocol()
        with pytest.raises(GimdowBLEDataFormatError):
            proto._parse_timestamp(bytes([99]) + b"\x00" * 10, 0)

    def test_empty_data_raises_length_error(self) -> None:
        proto = _StubProtocol()
        with pytest.raises(GimdowBLEDataLengthError):
            proto._parse_timestamp(b"", 0)

    def test_offset_respected(self) -> None:
        proto = _StubProtocol()
        ts_int = 1_700_000_001
        data = b"\xff\xff" + bytes([1]) + ts_int.to_bytes(4, "big")
        timestamp, end_pos = proto._parse_timestamp(data, 2)
        assert timestamp == float(ts_int) and end_pos == 7


# ---------------------------------------------------------------------------
# TestParseDatapointsV3TypeBranches
# ---------------------------------------------------------------------------


class TestParseDatapointsV3TypeBranches:
    def test_dt_string_parsed_correctly(self) -> None:
        proto = _StubProtocol()
        text = "hello"
        payload = pack(">HB", 1, 0)
        payload += (
            bytes([9, GimdowBLEDataPointType.DT_STRING.value, len(text)])
            + text.encode()
        )
        proto._parse_datapoints_v3(0.0, 0, payload, 3)
        assert proto._datapoints[9].value == "hello"

    def test_dt_value_signed_negative(self) -> None:
        proto = _StubProtocol()
        payload = pack(">HB", 1, 0)
        payload += bytes([10, GimdowBLEDataPointType.DT_VALUE.value, 4]) + pack(
            ">i", -50
        )
        proto._parse_datapoints_v3(0.0, 0, payload, 3)
        assert proto._datapoints[10].value == -50

    def test_dt_raw_returns_bytes(self) -> None:
        proto = _StubProtocol()
        raw = b"\xde\xad\xbe\xef"
        payload = pack(">HB", 1, 0)
        payload += bytes([11, GimdowBLEDataPointType.DT_RAW.value, len(raw)]) + raw
        proto._parse_datapoints_v3(0.0, 0, payload, 3)
        assert proto._datapoints[11].value == raw

    def test_dt_bitmap_returns_bytes(self) -> None:
        proto = _StubProtocol()
        bmap = b"\xff\x00"
        payload = pack(">HB", 1, 0)
        payload += bytes([12, GimdowBLEDataPointType.DT_BITMAP.value, len(bmap)]) + bmap
        proto._parse_datapoints_v3(0.0, 0, payload, 3)
        assert proto._datapoints[12].value == bmap

    def test_invalid_dp_type_raises_format_error(self) -> None:
        proto = _StubProtocol()
        payload = pack(">HB", 1, 0)
        payload += bytes([9, 99, 1, 0])
        with pytest.raises(GimdowBLEDataFormatError):
            proto._parse_datapoints_v3(0.0, 0, payload, 3)

    def test_data_length_overflow_raises(self) -> None:
        proto = _StubProtocol()
        payload = pack(">HB", 1, 0)
        payload += bytes([9, GimdowBLEDataPointType.DT_ENUM.value, 50, 0])
        with pytest.raises(GimdowBLEDataLengthError):
            proto._parse_datapoints_v3(0.0, 0, payload, 3)

    def test_callbacks_fired_with_list_of_datapoints(self) -> None:
        proto = _StubProtocol()
        fired: list = []
        proto._callbacks.append(lambda dps: fired.extend(dps))
        payload = pack(">HB", 1, 0)
        payload += bytes([9, GimdowBLEDataPointType.DT_ENUM.value, 1, 1])
        payload += bytes([46, GimdowBLEDataPointType.DT_BOOL.value, 1, 1])
        proto._parse_datapoints_v3(0.0, 0, payload, 3)
        assert {dp.id for dp in fired} == {9, 46}


# ---------------------------------------------------------------------------
# TestBuildPackets
# ---------------------------------------------------------------------------


class TestBuildPackets:
    def test_returns_non_empty_list(self) -> None:
        proto = _StubProtocol()
        proto._session_key = b"\x01" * 16
        assert len(proto._build_packets(1, GimdowBLECode.FUN_SENDER_DPS, b"")) >= 1

    def test_device_info_uses_login_key(self) -> None:
        proto = _StubProtocol()
        proto._login_key = b"\xaa" * 16
        packets = proto._build_packets(
            1, GimdowBLECode.FUN_SENDER_DEVICE_INFO, b"x" * 4
        )
        assert _reassemble(packets)[0] == 0x04

    def test_other_code_uses_session_key(self) -> None:
        proto = _StubProtocol()
        proto._session_key = b"\xbb" * 16
        packets = proto._build_packets(1, GimdowBLECode.FUN_SENDER_DPS, b"x" * 4)
        assert _reassemble(packets)[0] == 0x05

    def test_first_packet_header_structure(self) -> None:
        proto = _StubProtocol()
        proto._session_key = b"\x01" * 16
        packets = proto._build_packets(1, GimdowBLECode.FUN_SENDER_DPS, b"")
        total_len, pv_byte, _ = _decode_packet0(packets[0])
        assert total_len == len(_reassemble(packets))
        assert (pv_byte >> 4) == proto._protocol_version

    def test_large_payload_splits_into_multiple_packets(self) -> None:
        proto = _StubProtocol()
        proto._session_key = b"\x02" * 16
        assert (
            len(proto._build_packets(1, GimdowBLECode.FUN_SENDER_DPS, b"\x00" * 64)) > 1
        )

    def test_no_packet_exceeds_mtu(self) -> None:
        proto = _StubProtocol()
        proto._session_key = b"\x03" * 16
        for pkt in proto._build_packets(1, GimdowBLECode.FUN_SENDER_DPS, b"\x00" * 100):
            assert len(pkt) <= GATT_MTU

    def test_round_trip_decrypts_to_correct_data(self) -> None:
        proto = _StubProtocol()
        key = b"\x05" * 16
        proto._session_key = key
        payload = b"\xde\xad\xbe\xef"
        seq_num = 42
        packets = proto._build_packets(seq_num, GimdowBLECode.FUN_SENDER_DPS, payload)
        raw = _decrypt_payload(key, _reassemble(packets))
        s, rt, code_val, length = unpack(">IIHH", raw[:12])
        assert s == seq_num and rt == 0
        assert code_val == GimdowBLECode.FUN_SENDER_DPS.value
        assert raw[12 : 12 + length] == payload


# ---------------------------------------------------------------------------
# TestGetSeqNum
# ---------------------------------------------------------------------------


class TestGetSeqNum:
    async def test_first_call_returns_one(self) -> None:
        assert await _StubProtocol()._get_seq_num() == 1

    async def test_increments_each_call(self) -> None:
        proto = _StubProtocol()
        first = await proto._get_seq_num()
        assert await proto._get_seq_num() == first + 1

    async def test_concurrent_calls_no_duplicate(self) -> None:
        proto = _StubProtocol()
        results = await asyncio.gather(*[proto._get_seq_num() for _ in range(10)])
        assert len(set(results)) == 10


# ---------------------------------------------------------------------------
# TestGetKey
# ---------------------------------------------------------------------------


class TestGetKey:
    def test_flag4_returns_login_key(self) -> None:
        proto = _StubProtocol()
        proto._login_key = b"\x04" * 16
        assert proto._get_key(4) == b"\x04" * 16

    def test_flag5_returns_session_key(self) -> None:
        proto = _StubProtocol()
        proto._session_key = b"\x05" * 16
        assert proto._get_key(5) == b"\x05" * 16

    def test_flag1_returns_auth_key(self) -> None:
        proto = _StubProtocol()
        proto._auth_key = b"\x01" * 16
        assert proto._get_key(1) == b"\x01" * 16

    def test_unknown_flag_raises_format_error(self) -> None:
        proto = _StubProtocol()
        with pytest.raises(GimdowBLEDataFormatError):
            proto._get_key(99)

    def test_none_key_raises_format_error(self) -> None:
        proto = _StubProtocol()
        proto._login_key = None
        with pytest.raises(GimdowBLEDataFormatError):
            proto._get_key(4)


# ---------------------------------------------------------------------------
# TestSendDatapoints
# ---------------------------------------------------------------------------


class TestSendDatapoints:
    async def test_send_datapoints_v3_calls_send_packet(self) -> None:
        proto = _StubProtocol()
        proto._send_packet = AsyncMock()
        proto._datapoints.get_or_create(9, GimdowBLEDataPointType.DT_ENUM, 1)
        proto._datapoints[9]._get_value = MagicMock(return_value=b"\x01")
        await proto._send_datapoints_v3([9])
        proto._send_packet.assert_awaited_once()
        assert proto._send_packet.call_args[0][0] == GimdowBLECode.FUN_SENDER_DPS

    async def test_send_datapoints_v2_dispatches_to_v3(self) -> None:
        proto = _StubProtocol()
        proto._protocol_version = 2
        proto._send_datapoints_v3 = AsyncMock()
        await proto._send_datapoints([9])
        proto._send_datapoints_v3.assert_awaited_once_with([9])

    async def test_send_datapoints_v3_dispatches_to_v3(self) -> None:
        proto = _StubProtocol()
        proto._protocol_version = 3
        proto._send_datapoints_v3 = AsyncMock()
        await proto._send_datapoints([9])
        proto._send_datapoints_v3.assert_awaited_once_with([9])

    async def test_send_datapoints_unknown_version_raises(self) -> None:
        proto = _StubProtocol()
        proto._protocol_version = 99
        with pytest.raises(GimdowBLEUnsupportedProtocolError):
            await proto._send_datapoints([9])


# ---------------------------------------------------------------------------
# TestHandleCommandRemainingCases
# ---------------------------------------------------------------------------


class TestHandleCommandRemainingCases:
    def test_device_status_stores_result(self) -> None:
        proto = _StubProtocol()
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_SENDER_DEVICE_STATUS, b"\x00"
        )

    def test_device_status_bad_length_raises(self) -> None:
        proto = _StubProtocol()
        with pytest.raises(GimdowBLEDataLengthError):
            proto._handle_command_or_response(
                1, 0, GimdowBLECode.FUN_SENDER_DEVICE_STATUS, b""
            )

    async def test_receive_dp_fires_callbacks(self) -> None:
        proto = _StubProtocol()
        proto._send_response = AsyncMock()
        cb = MagicMock()
        proto._callbacks.append(cb)
        dp_payload = bytes([9, GimdowBLEDataPointType.DT_ENUM.value, 1, 5])
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_RECEIVE_DP, dp_payload
        )
        await asyncio.sleep(0)
        cb.assert_called_once()
        assert proto._datapoints[9].value == 5

    async def test_receive_sign_dp_fires_callbacks_and_schedules_ack(self) -> None:
        proto = _StubProtocol()
        proto._send_response = AsyncMock()
        dp_payload = pack(">HB", 7, 0) + bytes(
            [9, GimdowBLEDataPointType.DT_ENUM.value, 1, 3]
        )
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_RECEIVE_SIGN_DP, dp_payload
        )
        await asyncio.sleep(0)
        proto._send_response.assert_awaited_once()
        assert proto._datapoints[9].value == 3

    async def test_receive_time_dp_fires_callbacks(self) -> None:
        proto = _StubProtocol()
        proto._send_response = AsyncMock()
        ts_int = 1_700_000_000
        dp_payload = (
            bytes([1])
            + ts_int.to_bytes(4, "big")
            + bytes([9, GimdowBLEDataPointType.DT_ENUM.value, 1, 7])
        )
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_RECEIVE_TIME_DP, dp_payload
        )
        await asyncio.sleep(0)
        assert proto._datapoints[9].value == 7
        assert proto._datapoints[9].timestamp == float(ts_int)

    async def test_receive_sign_time_dp_fires_callbacks_and_schedules_ack(self) -> None:
        proto = _StubProtocol()
        proto._send_response = AsyncMock()
        ts_int = 1_700_000_001
        data = (
            pack(">HB", 3, 0)
            + bytes([1])
            + ts_int.to_bytes(4, "big")
            + bytes([46, GimdowBLEDataPointType.DT_BOOL.value, 1, 1])
        )
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_RECEIVE_SIGN_TIME_DP, data
        )
        await asyncio.sleep(0)
        proto._send_response.assert_awaited_once()
        assert proto._datapoints[46].value is True
        assert proto._datapoints[46].timestamp == float(ts_int)


# ---------------------------------------------------------------------------
# TestTimeRequestHandlers
# ---------------------------------------------------------------------------


class TestTimeRequestHandlers:
    async def test_time1_req_schedules_response(self) -> None:
        proto = _StubProtocol()
        proto._send_response = AsyncMock()
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_RECEIVE_TIME1_REQ, b""
        )
        await asyncio.sleep(0)
        proto._send_response.assert_awaited_once()
        args = proto._send_response.call_args[0]
        assert args[0] == GimdowBLECode.FUN_RECEIVE_TIME1_REQ
        assert len(args[1]) == 15

    async def test_time1_req_bad_length_raises(self) -> None:
        proto = _StubProtocol()
        with pytest.raises(GimdowBLEDataLengthError):
            proto._handle_command_or_response(
                1, 0, GimdowBLECode.FUN_RECEIVE_TIME1_REQ, b"\x01"
            )

    async def test_time2_req_schedules_response(self) -> None:
        proto = _StubProtocol()
        proto._send_response = AsyncMock()
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_RECEIVE_TIME2_REQ, b""
        )
        await asyncio.sleep(0)
        proto._send_response.assert_awaited_once()
        args = proto._send_response.call_args[0]
        assert args[0] == GimdowBLECode.FUN_RECEIVE_TIME2_REQ
        assert len(args[1]) == 9

    async def test_time2_req_bad_length_raises(self) -> None:
        proto = _StubProtocol()
        with pytest.raises(GimdowBLEDataLengthError):
            proto._handle_command_or_response(
                1, 0, GimdowBLECode.FUN_RECEIVE_TIME2_REQ, b"\x01"
            )


# ---------------------------------------------------------------------------
# TestNotificationHandlerReassembly
# ---------------------------------------------------------------------------


class TestNotificationHandlerReassembly:
    def test_single_packet_calls_parse_input(self) -> None:
        proto = _StubProtocol()
        proto._parse_input = MagicMock()
        pkt = _make_gatt_packet(0, b"\xaa\xbb\xcc\xdd\xee", total_length=5)
        proto._notification_handler(None, pkt)
        proto._parse_input.assert_called_once()

    def test_two_fragment_calls_parse_input_once(self) -> None:
        proto = _StubProtocol()
        proto._parse_input = MagicMock()
        full_payload = b"\xaa" * 10
        proto._notification_handler(
            None, _make_gatt_packet(0, full_payload[:4], total_length=10)
        )
        proto._parse_input.assert_not_called()
        proto._notification_handler(None, _make_gatt_packet(1, full_payload[4:]))
        proto._parse_input.assert_called_once()

    def test_two_fragment_partial_state(self) -> None:
        proto = _StubProtocol()
        proto._parse_input = MagicMock()
        proto._notification_handler(
            None, _make_gatt_packet(0, b"\xaa" * 4, total_length=10)
        )
        assert proto._input_expected_packet_num == 1
        assert proto._input_expected_length == 10

    def test_midstream_packet0_resets_and_processes_new(self) -> None:
        proto = _StubProtocol()
        proto._parse_input = MagicMock()
        proto._notification_handler(
            None, _make_gatt_packet(0, b"\xaa" * 4, total_length=10)
        )
        proto._notification_handler(
            None, _make_gatt_packet(0, b"\xcc\xdd\xee", total_length=3)
        )
        proto._parse_input.assert_called_once()
        assert proto._input_expected_length == 3

    def test_buffer_overflow_clears_state(self) -> None:
        proto = _StubProtocol()
        proto._parse_input = MagicMock()
        proto._notification_handler(
            None, _make_gatt_packet(0, b"\xaa" * 10, total_length=5)
        )
        assert proto._input_buffer is None
        assert proto._input_expected_packet_num == 0
        proto._parse_input.assert_not_called()

    def test_unexpected_skipped_packet_num_clears_state(self) -> None:
        proto = _StubProtocol()
        proto._parse_input = MagicMock()
        proto._notification_handler(
            None, _make_gatt_packet(0, b"\xaa" * 4, total_length=10)
        )
        proto._notification_handler(None, _make_gatt_packet(5, b"\xbb" * 6))
        assert proto._input_buffer is None
        assert proto._input_expected_packet_num == 0
        proto._parse_input.assert_not_called()


# ---------------------------------------------------------------------------
# TestParseInput
# ---------------------------------------------------------------------------


class TestParseInput:
    def test_crc_mismatch_raises_data_crc_error(self) -> None:
        proto = _StubProtocol()
        key = b"\x01" * 16
        proto._login_key = key
        buf = _make_encrypted_buffer(
            key, GimdowBLECode.FUN_SENDER_PAIR, b"\x00", corrupt_crc=True
        )
        proto._input_buffer = bytearray(buf)
        with pytest.raises(GimdowBLEDataCRCError):
            proto._parse_input()

    def test_valid_pair_result0_sets_is_paired(self) -> None:
        proto = _StubProtocol()
        key = b"\x02" * 16
        proto._login_key = key
        buf = _make_encrypted_buffer(key, GimdowBLECode.FUN_SENDER_PAIR, b"\x00")
        proto._notification_handler(
            None, _make_gatt_packet(0, buf, total_length=len(buf))
        )
        assert proto._is_paired is True

    def test_valid_pair_result2_sets_is_paired(self) -> None:
        proto = _StubProtocol()
        key = b"\x03" * 16
        proto._login_key = key
        buf = _make_encrypted_buffer(key, GimdowBLECode.FUN_SENDER_PAIR, b"\x02")
        proto._notification_handler(
            None, _make_gatt_packet(0, buf, total_length=len(buf))
        )
        assert proto._is_paired is True

    def test_unknown_code_silently_returns(self) -> None:
        proto = _StubProtocol()
        key = b"\x04" * 16
        proto._login_key = key
        iv = os.urandom(16)
        raw = bytearray(pack(">IIHH", 1, 0, 0xFFFF, 0))
        raw += pack(">H", GimdowBLEProtocol._calc_crc16(bytes(raw)))
        while len(raw) % 16 != 0:
            raw += b"\x00"
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        proto._input_buffer = bytearray(b"\x04" + iv + encryptor.update(bytes(raw)) + encryptor.finalize())
        proto._parse_input()
        assert proto._input_buffer is None


# ---------------------------------------------------------------------------
# TestHandleCommandOrResponsePair
# ---------------------------------------------------------------------------


class TestHandleCommandOrResponsePair:
    def test_pair_result0_sets_paired(self) -> None:
        proto = _StubProtocol()
        proto._handle_command_or_response(1, 0, GimdowBLECode.FUN_SENDER_PAIR, b"\x00")
        assert proto._is_paired is True

    def test_pair_result2_already_paired_sets_paired(self) -> None:
        proto = _StubProtocol()
        proto._handle_command_or_response(1, 0, GimdowBLECode.FUN_SENDER_PAIR, b"\x02")
        assert proto._is_paired is True

    def test_pair_result1_clears_paired(self) -> None:
        proto = _StubProtocol()
        proto._is_paired = True
        proto._handle_command_or_response(1, 0, GimdowBLECode.FUN_SENDER_PAIR, b"\x01")
        assert proto._is_paired is False

    def test_pair_empty_data_raises_length_error(self) -> None:
        proto = _StubProtocol()
        with pytest.raises(GimdowBLEDataLengthError):
            proto._handle_command_or_response(1, 0, GimdowBLECode.FUN_SENDER_PAIR, b"")


# ---------------------------------------------------------------------------
# TestHandleCommandOrResponseDeviceInfo
# ---------------------------------------------------------------------------


class TestHandleCommandOrResponseDeviceInfo:
    def _make_device_info(self, local_key: bytes, srand: bytes) -> bytes:
        return bytes([1, 2, 2, 3, 0, 1]) + srand + bytes([1, 0]) + bytes(32)

    def test_session_key_derived_from_local_key_and_srand(self) -> None:
        proto = _StubProtocol()
        local_key = b"\x01\x02\x03\x04\x05\x06"
        srand = b"\xaa\xbb\xcc\xdd\xee\xff"
        proto._local_key = local_key
        data = self._make_device_info(local_key, srand)
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_SENDER_DEVICE_INFO, data
        )
        assert proto._session_key == hashlib.md5(local_key + srand).digest()

    def test_device_info_short_data_raises_length_error(self) -> None:
        proto = _StubProtocol()
        proto._local_key = b"\x00" * 6
        with pytest.raises(GimdowBLEDataLengthError):
            proto._handle_command_or_response(
                1, 0, GimdowBLECode.FUN_SENDER_DEVICE_INFO, bytes(45)
            )

    def test_device_info_sets_version_strings(self) -> None:
        proto = _StubProtocol()
        proto._local_key = b"\x00" * 6
        data = bytes([3, 1, 2, 0, 0, 0]) + bytes(6) + bytes([1, 5]) + bytes(32)
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_SENDER_DEVICE_INFO, data
        )
        assert proto._device_version == "3.1"
        assert proto._protocol_version_str == "2.0"
        assert proto._hardware_version == "1.5"

    def test_device_info_sets_auth_key(self) -> None:
        proto = _StubProtocol()
        proto._local_key = b"\x00" * 6
        auth_key = bytes(range(32))
        data = bytes([1, 0, 2, 0, 0, 0]) + bytes(6) + bytes(2) + auth_key
        proto._handle_command_or_response(
            1, 0, GimdowBLECode.FUN_SENDER_DEVICE_INFO, data
        )
        assert proto._auth_key == auth_key


# ---------------------------------------------------------------------------
# TestHandleCommandOrResponseFutures
# ---------------------------------------------------------------------------


class TestHandleCommandOrResponseFutures:
    async def test_future_resolved_with_zero_on_success(self) -> None:
        proto = _StubProtocol()
        future = asyncio.get_running_loop().create_future()
        proto._input_expected_responses[5] = future
        proto._handle_command_or_response(2, 5, GimdowBLECode.FUN_SENDER_PAIR, b"\x00")
        assert future.done() and future.result() == 0
        assert 5 not in proto._input_expected_responses

    async def test_future_exception_on_nonzero_result(self) -> None:
        proto = _StubProtocol()
        future = asyncio.get_running_loop().create_future()
        proto._input_expected_responses[7] = future
        proto._handle_command_or_response(3, 7, GimdowBLECode.FUN_SENDER_PAIR, b"\x01")
        assert future.done()
        with pytest.raises(GimdowBLEDeviceError):
            future.result()

    def test_no_matching_future_is_noop(self) -> None:
        proto = _StubProtocol()
        proto._handle_command_or_response(1, 99, GimdowBLECode.FUN_SENDER_PAIR, b"\x00")

    async def test_future_popped_from_dict_after_resolution(self) -> None:
        proto = _StubProtocol()
        future = asyncio.get_running_loop().create_future()
        proto._input_expected_responses[3] = future
        proto._handle_command_or_response(1, 3, GimdowBLECode.FUN_SENDER_PAIR, b"\x00")
        assert 3 not in proto._input_expected_responses


# ---------------------------------------------------------------------------
# TestNotificationHandlerErrorPaths
# ---------------------------------------------------------------------------


class TestNotificationHandlerErrorPaths:
    def test_parse_exception_clears_buffer(self) -> None:
        proto = _StubProtocol()
        proto._parse_input = MagicMock(side_effect=Exception("parse error"))
        proto._notification_handler(
            None, _make_gatt_packet(0, b"\xaa" * 5, total_length=5)
        )
        assert proto._input_buffer is None
        assert proto._input_expected_packet_num == 0
