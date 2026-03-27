"""Gimdow BLE protocol mixin — packet encoding, AES encryption, CRC, and notification parsing.

``GimdowBLEProtocol`` is a mixin class. It must be combined with ``GimdowBLEConnection``
(for :meth:`_send_response`) and a concrete class that provides:

  - ``self.address`` (str)
  - ``self.rssi`` (int | None)
  - ``self._protocol_version`` (int)
  - ``self._login_key``, ``self._session_key``, ``self._auth_key`` (bytes)
  - ``self._input_buffer``, ``self._input_expected_packet_num``, ``self._input_expected_length``
  - ``self._input_expected_responses`` (dict)
  - ``self._current_seq_num``, ``self._seq_num_lock`` (asyncio.Lock)
  - ``self._datapoints`` (GimdowBLEDataPoints)
  - ``self._is_paired`` (bool)
  - ``self._device_version``, ``self._protocol_version_str``, ``self._hardware_version`` (str)
  - ``self._flags``, ``self._is_bound`` (int / bool)
  - ``self._local_key`` (bytes)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from struct import pack, unpack
from typing import Any

from Crypto.Cipher import AES

from .const import (
    CHARACTERISTIC_NOTIFY,
    CHARACTERISTIC_WRITE,
    GATT_MTU,
    RESPONSE_WAIT_TIMEOUT,
    GimdowBLECode,
    GimdowBLEDataPointType,
)
from .datapoints import GimdowBLEDataPoint
from .exceptions import (
    GimdowBLEDataCRCError,
    GimdowBLEDataFormatError,
    GimdowBLEDataLengthError,
    GimdowBLEDeviceError,
)

_LOGGER = logging.getLogger(__name__)


class GimdowBLEProtocol:
    """Mixin: BLE packet encoding/decoding, AES encryption, and notification parsing."""

    # ------------------------------------------------------------------
    # Protocol state initialiser — call from GimdowBLEDevice.__init__
    # ------------------------------------------------------------------

    def _init_protocol(self) -> None:
        self._input_buffer: bytearray | None = None
        self._input_expected_packet_num: int = 0
        self._input_expected_length: int = 0
        self._input_expected_responses: dict[int, asyncio.Future] = {}

        self._auth_key: bytes | None = None
        self._login_key: bytes | None = None
        self._session_key: bytes | None = None

        self._seq_num_lock = asyncio.Lock()
        self._current_seq_num: int = 1

    # ------------------------------------------------------------------
    # CRC / integer helpers (static)
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_crc16(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte & 255
            for _ in range(8):
                tmp = crc & 1
                crc >>= 1
                if tmp != 0:
                    crc ^= 0xA001
        return crc

    @staticmethod
    def _pack_int(value: int) -> bytearray:
        result = bytearray()
        while True:
            curr_byte = value & 0x7F
            value >>= 7
            if value != 0:
                curr_byte |= 0x80
            result += pack(">B", curr_byte)
            if value == 0:
                break
        return result

    @staticmethod
    def _unpack_int(data: bytes, start_pos: int) -> tuple:
        result: int = 0
        offset: int = 0
        while offset < 5:
            pos: int = start_pos + offset
            if pos >= len(data):
                raise GimdowBLEDataFormatError()
            curr_byte: int = data[pos]
            result |= (curr_byte & 0x7F) << (offset * 7)
            offset += 1
            if (curr_byte & 0x80) == 0:
                break
        if offset > 4:
            raise GimdowBLEDataFormatError()
        return (result, start_pos + offset)

    # ------------------------------------------------------------------
    # Packet building
    # ------------------------------------------------------------------

    def _build_packets(
        self,
        seq_num: int,
        code: GimdowBLECode,
        data: bytes,
        response_to: int = 0,
    ) -> list[bytes]:
        iv = secrets.token_bytes(16)
        if code == GimdowBLECode.FUN_SENDER_DEVICE_INFO:
            key = self._login_key
            security_flag = b"\x04"
        else:
            key = self._session_key
            security_flag = b"\x05"

        raw = bytearray()
        raw += pack(">IIHH", seq_num, response_to, code.value, len(data))
        raw += data
        crc = self._calc_crc16(raw)
        raw += pack(">H", crc)
        while len(raw) % 16 != 0:
            raw += b"\x00"

        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = security_flag + iv + cipher.encrypt(raw)

        command = []
        packet_num = 0
        pos = 0
        length = len(encrypted)
        while pos < length:
            packet = bytearray()
            packet += self._pack_int(packet_num)
            if packet_num == 0:
                packet += self._pack_int(length)
                packet += pack(">B", self._protocol_version << 4)
            data_part = encrypted[pos:pos + GATT_MTU - len(packet)]
            packet += data_part
            command.append(packet)
            pos += len(data_part)
            packet_num += 1

        return command

    async def _get_seq_num(self) -> int:
        async with self._seq_num_lock:
            result = self._current_seq_num
            self._current_seq_num += 1
        return result

    # ------------------------------------------------------------------
    # Key lookup
    # ------------------------------------------------------------------

    def _get_key(self, security_flag: int) -> bytes:
        if security_flag == 1:
            key = self._auth_key
        elif security_flag == 4:
            key = self._login_key
        elif security_flag == 5:
            key = self._session_key
        else:
            raise GimdowBLEDataFormatError()
        if key is None:
            raise GimdowBLEDataFormatError()
        return key

    # ------------------------------------------------------------------
    # Datapoint parsing
    # ------------------------------------------------------------------

    def _parse_timestamp(self, data: bytes, start_pos: int) -> tuple:
        pos = start_pos
        if pos >= len(data):
            raise GimdowBLEDataLengthError()
        time_type = data[pos]
        pos += 1
        end_pos = pos
        match time_type:
            case 0:
                end_pos += 13
                if end_pos > len(data):
                    raise GimdowBLEDataLengthError()
                timestamp = int(data[pos:end_pos].decode()) / 1000
            case 1:
                end_pos += 4
                if end_pos > len(data):
                    raise GimdowBLEDataLengthError()
                timestamp = int.from_bytes(data[pos:end_pos], "big") * 1.0
            case _:
                raise GimdowBLEDataFormatError()

        _LOGGER.debug("%s: Received timestamp: %s", self.address, time.ctime(timestamp))
        return (timestamp, end_pos)

    def _parse_datapoints_v3(
        self, timestamp: float, flags: int, data: bytes, start_pos: int
    ) -> None:
        datapoints: list[GimdowBLEDataPoint] = []
        pos = start_pos
        while len(data) - pos >= 4:
            id: int = data[pos]
            pos += 1
            _type: int = data[pos]
            if _type > GimdowBLEDataPointType.DT_BITMAP.value:
                raise GimdowBLEDataFormatError()
            dp_type = GimdowBLEDataPointType(_type)
            pos += 1
            data_len: int = data[pos]
            pos += 1
            next_pos = pos + data_len
            if next_pos > len(data):
                raise GimdowBLEDataLengthError()
            raw_value = data[pos:next_pos]
            match dp_type:
                case (GimdowBLEDataPointType.DT_RAW | GimdowBLEDataPointType.DT_BITMAP):
                    value = raw_value
                case GimdowBLEDataPointType.DT_BOOL:
                    value = int.from_bytes(raw_value, "big") != 0
                case (GimdowBLEDataPointType.DT_VALUE | GimdowBLEDataPointType.DT_ENUM):
                    value = int.from_bytes(raw_value, "big", signed=True)
                case GimdowBLEDataPointType.DT_STRING:
                    value = raw_value.decode()
                case _:
                    raise GimdowBLEDataFormatError()

            _LOGGER.debug(
                "%s: DP update — id:%s type:%s value:%s",
                self.address, id, dp_type.name, value,
            )
            self._datapoints._update_from_device(id, timestamp, flags, dp_type, value)
            datapoints.append(self._datapoints[id])
            pos = next_pos

        self._fire_callbacks(datapoints)

    # ------------------------------------------------------------------
    # Command / response handler
    # ------------------------------------------------------------------

    def _handle_command_or_response(
        self, seq_num: int, response_to: int, code: GimdowBLECode, data: bytes
    ) -> None:
        result: int = 0
        _LOGGER.debug(
            "%s: Handling command/response code=%s data_len=%s",
            self.address, code.name, len(data),
        )

        match code:
            case GimdowBLECode.FUN_SENDER_DEVICE_INFO:
                if len(data) < 46:
                    raise GimdowBLEDataLengthError()
                self._device_version = "%s.%s" % (data[0], data[1])
                self._protocol_version_str = "%s.%s" % (data[2], data[3])
                self._hardware_version = "%s.%s" % (data[12], data[13])
                self._protocol_version = data[2]
                self._flags = data[4]
                self._is_bound = data[5] != 0
                srand = data[6:12]
                self._session_key = hashlib.md5(self._local_key + srand).digest()
                self._auth_key = data[14:46]
                _LOGGER.info("%s: Device Info received. Session Key derived.", self.address)

            case GimdowBLECode.FUN_SENDER_PAIR:
                if len(data) != 1:
                    raise GimdowBLEDataLengthError()
                result = data[0]
                if result == 2:
                    _LOGGER.debug("%s: Device is already paired", self.address)
                    result = 0
                self._is_paired = result == 0
                _LOGGER.info("%s: Pairing result: %s (paired=%s)", self.address, result, self._is_paired)

            case GimdowBLECode.FUN_SENDER_DEVICE_STATUS:
                if len(data) != 1:
                    raise GimdowBLEDataLengthError()
                result = data[0]

            case GimdowBLECode.FUN_RECEIVE_TIME1_REQ:
                if len(data) != 0:
                    raise GimdowBLEDataLengthError()
                timestamp = int(time.time_ns() / 1000000)
                timezone = -int(time.timezone / 36)
                resp_data = str(timestamp).encode() + pack(">h", timezone)
                self._create_safe_task(self._send_response(code, resp_data, seq_num))

            case GimdowBLECode.FUN_RECEIVE_TIME2_REQ:
                if len(data) != 0:
                    raise GimdowBLEDataLengthError()
                time_str = time.localtime()
                timezone = -int(time.timezone / 36)
                resp_data = pack(
                    ">BBBBBBBh",
                    time_str.tm_year % 100, time_str.tm_mon, time_str.tm_mday,
                    time_str.tm_hour, time_str.tm_min, time_str.tm_sec,
                    time_str.tm_wday, timezone,
                )
                self._create_safe_task(self._send_response(code, resp_data, seq_num))

            case GimdowBLECode.FUN_RECEIVE_DP:
                self._parse_datapoints_v3(time.time(), 0, data, 0)
                self._create_safe_task(self._send_response(code, bytes(0), seq_num))

            case GimdowBLECode.FUN_RECEIVE_SIGN_DP:
                dp_seq_num = int.from_bytes(data[:2], "big")
                flags = data[2]
                self._parse_datapoints_v3(time.time(), flags, data, 2)
                resp_data = pack(">HBB", dp_seq_num, flags, 0)
                self._create_safe_task(self._send_response(code, resp_data, seq_num))

            case GimdowBLECode.FUN_RECEIVE_TIME_DP:
                timestamp, pos = self._parse_timestamp(data, 0)
                self._parse_datapoints_v3(timestamp, 0, data, pos)
                self._create_safe_task(self._send_response(code, bytes(0), seq_num))

            case GimdowBLECode.FUN_RECEIVE_SIGN_TIME_DP:
                dp_seq_num = int.from_bytes(data[:2], "big")
                flags = data[2]
                timestamp, pos = self._parse_timestamp(data, 3)
                self._parse_datapoints_v3(time.time(), flags, data, pos)
                resp_data = pack(">HBB", dp_seq_num, flags, 0)
                self._create_safe_task(self._send_response(code, resp_data, seq_num))

        if response_to != 0:
            future = self._input_expected_responses.pop(response_to, None)
            if future:
                _LOGGER.debug(
                    "%s: Response to #%s — result=%s", self.address, response_to, result
                )
                if result == 0:
                    future.set_result(result)
                else:
                    future.set_exception(GimdowBLEDeviceError(result))

    # ------------------------------------------------------------------
    # Notification handler (BLE input pipeline)
    # ------------------------------------------------------------------

    def _clean_input(self) -> None:
        self._input_buffer = None
        self._input_expected_packet_num = 0
        self._input_expected_length = 0

    def _parse_input(self) -> None:
        security_flag = self._input_buffer[0]
        key = self._get_key(security_flag)
        iv = self._input_buffer[1:17]
        encrypted = self._input_buffer[17:]
        self._clean_input()

        cipher = AES.new(key, AES.MODE_CBC, iv)
        raw = cipher.decrypt(encrypted)

        seq_num, response_to, _code, length = unpack(">IIHH", raw[:12])
        data_end_pos = length + 12
        raw_length = len(raw)
        if raw_length < data_end_pos:
            raise GimdowBLEDataLengthError()
        if raw_length > data_end_pos:
            calc_crc = self._calc_crc16(raw[:data_end_pos])
            (data_crc,) = unpack(">H", raw[data_end_pos:data_end_pos + 2])
            if calc_crc != data_crc:
                raise GimdowBLEDataCRCError()
        data = raw[12:data_end_pos]

        try:
            code = GimdowBLECode(_code)
        except ValueError:
            _LOGGER.debug(
                "%s: Unknown message: #%s 0x%x → #%s  data=%s",
                self.address, seq_num, _code, response_to, data.hex(),
            )
            return

        if response_to != 0:
            _LOGGER.debug("%s: Received #%s %s → #%s", self.address, seq_num, code.name, response_to)
        else:
            _LOGGER.debug("%s: Received #%s %s", self.address, seq_num, code.name)

        self._handle_command_or_response(seq_num, response_to, code, data)

    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle raw BLE notification — reassemble multi-packet messages."""
        _LOGGER.debug("%s: Packet received: %s", self.address, data.hex())
        pos: int = 0
        packet_num, pos = self._unpack_int(data, pos)

        if packet_num < self._input_expected_packet_num:
            if packet_num == 0:
                _LOGGER.debug(
                    "%s: Packet 0 while expecting %s — resetting buffer",
                    self.address, self._input_expected_packet_num,
                )
                self._clean_input()
            else:
                _LOGGER.error(
                    "%s: Unexpected packet #%s (expected %s)",
                    self.address, packet_num, self._input_expected_packet_num,
                )
                self._clean_input()

        if packet_num == self._input_expected_packet_num:
            if packet_num == 0:
                self._input_buffer = bytearray()
                self._input_expected_length, pos = self._unpack_int(data, pos)
                pos += 1  # skip protocol version byte
            self._input_buffer += data[pos:]
            self._input_expected_packet_num += 1
        else:
            _LOGGER.error(
                "%s: Missing packet #%s (received %s)",
                self.address, self._input_expected_packet_num, packet_num,
            )
            self._clean_input()
            return

        if len(self._input_buffer) > self._input_expected_length:
            _LOGGER.error(
                "%s: Buffer overflow: got %s, expected %s",
                self.address, len(self._input_buffer), self._input_expected_length,
            )
            self._clean_input()
        elif len(self._input_buffer) == self._input_expected_length:
            try:
                self._parse_input()
            except Exception:
                _LOGGER.error(
                    "%s: Error parsing BLE notification — discarding buffer",
                    self.address, exc_info=True,
                )
                self._clean_input()

    # ------------------------------------------------------------------
    # Datapoint send (protocol-level)
    # ------------------------------------------------------------------

    async def _send_datapoints_v3(self, datapoint_ids: list[int]) -> None:
        data = bytearray()
        for dp_id in datapoint_ids:
            dp = self._datapoints[dp_id]
            value = dp._get_value()
            _LOGGER.debug(
                "%s: Sending DP update — id:%s type:%s value:%s",
                self.address, dp.id, dp.type.name, dp.value,
            )
            data += pack(">BBB", dp.id, int(dp.type.value), len(value))
            data += value
        await self._send_packet(GimdowBLECode.FUN_SENDER_DPS, data)

    async def _send_datapoints(self, datapoint_ids: list[int]) -> None:
        if self._protocol_version in (2, 3):
            await self._send_datapoints_v3(datapoint_ids)
        else:
            raise GimdowBLEDeviceError(0)
