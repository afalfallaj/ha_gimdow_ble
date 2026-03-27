"""Diagnostic context dataclass for structured Gimdow BLE error reporting."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GimdowBLEDiagContext:
    """Snapshot of device + lock state at the moment of an event or error.

    Call ``log()`` inside any ``except`` block to emit a single, copy-pasteable
    log line containing the full state needed to reproduce or diagnose a bug.

    Example output::

        [DIAG] action=async_lock connected=True paired=True resolving=False
               dp_state={'dp47_lock_state': None, 'dp46_lock_cmd': True, ...}
               error='Echo timeout on DP 6'
               extra={'is_door_open': True, 'pending_reason': 'door_open_pending',
                      'is_locking': True, 'is_unlocking': False, ...}
    """

    timestamp: float
    address: str
    is_connected: bool
    is_paired: bool
    is_resolving: bool
    dp_state: dict[str, Any]
    action: str
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def log(self, logger: logging.Logger, level: int = logging.ERROR) -> None:
        """Emit a structured diagnostic log line."""
        logger.log(
            level,
            "[DIAG] addr=%s action=%s connected=%s paired=%s resolving=%s "
            "dp_state=%s error=%s extra=%s",
            self.address,
            self.action,
            self.is_connected,
            self.is_paired,
            self.is_resolving,
            self.dp_state,
            self.error,
            self.extra,
        )
