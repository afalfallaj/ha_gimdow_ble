from __future__ import annotations


class GimdowBLEError(Exception):
    """Base class for Gimdow BLE errors."""


class GimdowBLEEnumValueError(GimdowBLEError):
    """Raised when value assigned to DP_ENUM datapoint has unexpected type."""

    def __init__(self) -> None:
        super().__init__("Value of DP_ENUM datapoint must be unsigned integer")


class GimdowBLEDataFormatError(GimdowBLEError):
    """Raised when data in Gimdow BLE structures formatted in wrong way."""

    def __init__(self) -> None:
        super().__init__("Incoming packet is formatted in wrong way")


class GimdowBLEDataCRCError(GimdowBLEError):
    """Raised when data packet has invalid CRC."""

    def __init__(self) -> None:
        super().__init__("Incoming packet has invalid CRC")


class GimdowBLEDataLengthError(GimdowBLEError):
    """Raised when data packet has invalid length."""

    def __init__(self) -> None:
        super().__init__("Incoming packet has invalid length")


class GimdowBLEDeviceError(GimdowBLEError):
    """Raised when Gimdow BLE device returned error in response to command."""

    def __init__(self, code: int) -> None:
        super().__init__(("BLE deice returned error code %s") % (code))
