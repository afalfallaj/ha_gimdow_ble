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
        super().__init__(("BLE device returned error code %s") % (code))


# ---------------------------------------------------------------------------
# Timeout exceptions
# ---------------------------------------------------------------------------

class GimdowBLETimeoutError(GimdowBLEError):
    """Operation timed out waiting for a device response."""

    def __init__(self, operation: str, timeout: float) -> None:
        super().__init__(f"Timeout after {timeout}s waiting for: {operation}")
        self.operation = operation
        self.timeout = timeout


class GimdowBLEEchoTimeoutError(GimdowBLETimeoutError):
    """Device did not echo a control datapoint within the timeout window."""

    def __init__(self, dp_id: int, timeout: float) -> None:
        super().__init__(f"echo of DP {dp_id}", timeout)
        self.dp_id = dp_id


class GimdowBLEStateTimeoutError(GimdowBLETimeoutError):
    """Device did not reach the expected lock state within the timeout window."""

    def __init__(self, target_state: str, timeout: float) -> None:
        super().__init__(f"state transition to '{target_state}'", timeout)
        self.target_state = target_state


# ---------------------------------------------------------------------------
# Connection exceptions
# ---------------------------------------------------------------------------

class GimdowBLEConnectionError(GimdowBLEError):
    """BLE connection failed or was lost during an operation."""

    def __init__(self, address: str, reason: str) -> None:
        super().__init__(f"[{address}] Connection error: {reason}")
        self.address = address
        self.reason = reason


# ---------------------------------------------------------------------------
# Resolution exceptions
# ---------------------------------------------------------------------------

class GimdowBLEResolutionAbortedError(GimdowBLEError):
    """Unknown state resolution was aborted before completing."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Unknown state resolution aborted: {reason}")
        self.reason = reason
