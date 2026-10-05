"""HA-only auto-lock controls for the A1 PRO; never write hardware settings."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntityDescription, RestoreNumber
from homeassistant.components.number.const import NumberMode
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import UnitOfTime
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .devices import GimdowBLEData, GimdowBLEEntity
from .gimdow_ble import GimdowBLEDevice


def supports_ha_auto_lock(device: GimdowBLEDevice) -> bool:
    """Match only the product lacking supported hardware auto-lock settings."""
    return device.category == "ms" and device.product_id == "uueg45qu"


@dataclass
class _HAAutoLockData(ExtraStoredData):
    enabled: bool

    def as_dict(self) -> dict[str, bool]:
        return {"enabled": self.enabled}


class GimdowBLEHAAutoLockSwitch(GimdowBLEEntity, SwitchEntity, RestoreEntity):
    """Enable HA auto-lock; the lock manager enforces the door-sensor requirement."""

    def __init__(self, data: GimdowBLEData) -> None:
        super().__init__(
            data.coordinator,
            data.device,
            data.product,
            SwitchEntityDescription(
                key="ha_auto_lock",
                name="HA auto-lock",
                icon="mdi:lock-clock",
                entity_category=EntityCategory.CONFIG,
            ),
        )
        self._data = data

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self._data.virtual_auto_lock

    @property
    def extra_restore_state_data(self) -> _HAAutoLockData:
        return _HAAutoLockData(self.is_on)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        extra = await self.async_get_last_extra_data()
        if extra is not None:
            enabled = extra.as_dict().get("enabled")
            if isinstance(enabled, bool):
                self._data.virtual_auto_lock = enabled
        async_dispatcher_send(self.hass, self._data.virtual_auto_lock_signal)

    async def _set_enabled(self, enabled: bool) -> None:
        self._data.virtual_auto_lock = enabled
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, self._data.virtual_auto_lock_signal)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_enabled(False)


class GimdowBLEHAAutoLockDelay(GimdowBLEEntity, RestoreNumber):
    """Persist the local timer delay without creating a synthetic device DP36."""

    def __init__(self, data: GimdowBLEData) -> None:
        super().__init__(
            data.coordinator,
            data.device,
            data.product,
            NumberEntityDescription(
                key="ha_auto_lock_delay",
                name="HA auto-lock delay",
                icon="mdi:timer-lock",
                entity_category=EntityCategory.CONFIG,
                native_min_value=1,
                native_max_value=1800,
                native_step=1,
                native_unit_of_measurement=UnitOfTime.SECONDS,
            ),
        )
        self._data = data
        self._attr_mode = NumberMode.BOX
        data.ha_auto_lock_delay = data.auto_lock_delay_fallback

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int | None:
        return self._data.ha_auto_lock_delay

    @staticmethod
    def _validate(value: float) -> int:
        if not math.isfinite(value) or not 1 <= value <= 1800 or int(value) != value:
            raise ValueError(
                "Auto-lock delay must be a whole number from 1 to 1800 seconds"
            )
        return int(value)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        restored = await self.async_get_last_number_data()
        if restored is not None and restored.native_value is not None:
            try:
                self._data.ha_auto_lock_delay = self._validate(restored.native_value)
            except (TypeError, ValueError, OverflowError):
                pass
        async_dispatcher_send(self.hass, self._data.virtual_auto_lock_time_signal)

    async def async_set_native_value(self, value: float) -> None:
        self._data.ha_auto_lock_delay = self._validate(value)
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, self._data.virtual_auto_lock_time_signal)
