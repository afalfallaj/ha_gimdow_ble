from __future__ import annotations

import asyncio
from typing import Any

# All mutable session state. Initialized in main() after the event loop starts.
# Other modules import this module and access attributes as `state.event_queue`, etc.
event_queue: asyncio.Queue  # consumed by event_printer (live display)
_assert_queue: asyncio.Queue  # consumed by assert_dp — independent copy of every event
_assert_active: bool = False  # True while assert_dp owns _assert_queue
last_dp_values: dict[int, Any] = {}
session_start: float = 0.0
