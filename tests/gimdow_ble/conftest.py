"""Inner-package test configuration — all stubs provided by root conftest.py."""

from custom_components.gimdow_ble.gimdow_ble.datapoints import GimdowBLEDataPoints


class BatchableGimdowBLEDataPoints(GimdowBLEDataPoints):
    """Test-only subclass that adds begin_update/end_update batch helpers.

    Production code never uses batch mode; these helpers exist solely for
    tests that verify batching semantics in isolation.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._update_started: int = 0
        self._updated_datapoints: list[int] = []

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

    async def _update_from_user(self, dp_id: int) -> None:
        if self._update_started > 0:
            if dp_id in self._updated_datapoints:
                self._updated_datapoints.remove(dp_id)
            self._updated_datapoints.append(dp_id)
        else:
            await super()._update_from_user(dp_id)
