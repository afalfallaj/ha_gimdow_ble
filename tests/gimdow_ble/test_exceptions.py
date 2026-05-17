"""Tests for GimdowBLE exception classes.

Verifies message content, attribute storage, and inheritance hierarchy
for the exceptions that are actually raised in production code.
"""

from __future__ import annotations

import pytest

from custom_components.gimdow_ble.gimdow_ble.exceptions import (
    GimdowBLEDataCRCError,
    GimdowBLEDataFormatError,
    GimdowBLEDataLengthError,
    GimdowBLEDeviceError,
    GimdowBLEEnumValueError,
    GimdowBLEError,
)


class TestBaseError:
    def test_is_exception(self) -> None:
        assert issubclass(GimdowBLEError, Exception)


class TestSimpleErrors:
    def test_enum_value_error_message(self) -> None:
        e = GimdowBLEEnumValueError()
        assert "unsigned integer" in str(e)

    def test_data_format_error_message(self) -> None:
        e = GimdowBLEDataFormatError()
        assert "formatted" in str(e)

    def test_data_crc_error_message(self) -> None:
        e = GimdowBLEDataCRCError()
        assert "CRC" in str(e)

    def test_data_length_error_message(self) -> None:
        e = GimdowBLEDataLengthError()
        assert "length" in str(e)

    def test_device_error_message_contains_code(self) -> None:
        e = GimdowBLEDeviceError(42)
        assert "42" in str(e)

    def test_device_error_zero_code(self) -> None:
        e = GimdowBLEDeviceError(0)
        assert "0" in str(e)
