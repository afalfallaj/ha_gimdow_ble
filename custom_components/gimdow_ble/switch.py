"""The Gimdow BLE integration."""

from __future__ import annotations

from dataclasses import dataclass

import logging
from typing import Any, Callable

from homeassistant.components.switch import (
    SwitchEntityDescription,
    SwitchEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.dispatcher import async_dispatcher_send

from . import GimdowBLEConfigEntry
from .const import CONF_DOOR_SENSOR
from .devices import (
    GimdowBLECategoryMapping,
    GimdowBLEData,
    GimdowBLEEntity,
    GimdowBLEProductInfo,
    get_platform_mapping,
)
from .gimdow_ble import GimdowBLEDataPointType, GimdowBLEDevice

_LOGGER = logging.getLogger(__name__)


GimdowBLESwitchIsAvailable = (
    Callable[["GimdowBLESwitch", GimdowBLEProductInfo], bool] | None
)


@dataclass
class GimdowBLESwitchMapping:
    dp_id: int
    description: SwitchEntityDescription
    force_add: bool = True
    dp_type: GimdowBLEDataPointType | None = None
    is_available: GimdowBLESwitchIsAvailable = None


GimdowBLECategorySwitchMapping = GimdowBLECategoryMapping[GimdowBLESwitchMapping]

mapping: dict[str, GimdowBLECategorySwitchMapping] = {
    "jtmspro": GimdowBLECategorySwitchMapping(
        products={
            "rlyxv7pe": [  # Gimdow
                GimdowBLESwitchMapping(
                    dp_id=33,
                    description=SwitchEntityDescription(
                        key="auto_lock",
                        icon="mdi:lock-clock",
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
                GimdowBLESwitchMapping(
                    dp_id=78,
                    description=SwitchEntityDescription(
                        key="change_direction",
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
}


def get_mapping_by_device(device: GimdowBLEDevice) -> list[GimdowBLESwitchMapping]:
    return get_platform_mapping(mapping, device)


# ---------------------------------------------------------------------------
# Shared base — thin BLE DP switch (real hardware switch)
# ---------------------------------------------------------------------------


class GimdowBLESwitch(GimdowBLEEntity, SwitchEntity):
    """Representation of a Gimdow BLE Switch backed directly by a device DP."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLESwitchMapping,
        data: GimdowBLEData,
    ) -> None:
        super().__init__(coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._data = data

    @property
    def is_on(self) -> bool:
        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            return bool(datapoint.value)
        return False

    async def _write_dp(self, turn_on: bool) -> None:
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            GimdowBLEDataPointType.DT_BOOL,
            turn_on,
        )
        await datapoint.set_value(turn_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write_dp(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write_dp(False)

    @property
    def available(self) -> bool:
        result = super().available
        if result and self._mapping.is_available:
            result = self._mapping.is_available(self, self._product)
        return result


# ---------------------------------------------------------------------------
# Virtual auto-lock switch — HA owns the timing; hardware DP33 stays OFF
# ---------------------------------------------------------------------------


@dataclass
class _VirtualAutoLockExtraData(ExtraStoredData):
    """Persisted independently of entity availability.

    This switch's state lives only in HA (never on the device), but its
    availability still follows BLE connectivity. A lock that's disconnected
    (past its grace period) when HA stops would dump state="unavailable" —
    parsing plain .state on restore would silently discard it.
    """

    is_on: bool

    def as_dict(self) -> dict[str, Any]:
        return {"is_on": self.is_on}

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> _VirtualAutoLockExtraData | None:
        try:
            return cls(bool(restored["is_on"]))
        except KeyError:
            return None


class GimdowBLEVirtualAutoLockSwitch(GimdowBLEEntity, SwitchEntity, RestoreEntity):
    """Auto-lock switch whose on/off state lives in HA, not the device.

    When this switch is ON, HA manages the re-lock timer via
    GimdowBLELockManager. The hardware auto-lock DP (DP33) is always kept
    OFF so the device's own timer never fires independently.
    """

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLESwitchMapping,
        data: GimdowBLEData,
    ) -> None:
        super().__init__(coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._data = data
        # Tracks the last device-pushed DP33 value to detect power-cycle resets.
        self._last_dp33: bool | None = None

    @property
    def extra_restore_state_data(self) -> _VirtualAutoLockExtraData:
        return _VirtualAutoLockExtraData(self.is_on)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        if self._product.is_lock:
            if (last_extra := await self.async_get_last_extra_data()) is not None:
                restored = _VirtualAutoLockExtraData.from_dict(last_extra.as_dict())
                if restored is not None:
                    self._data.virtual_auto_lock = restored.is_on
                    async_dispatcher_send(
                        self.hass, self._data.virtual_auto_lock_signal
                    )
                    # Seed the DP cache so the re-assert logic in
                    # _handle_coordinator_update has a baseline to compare against.
                    self._device.datapoints.get_or_create(
                        self._mapping.dp_id,
                        GimdowBLEDataPointType.DT_BOOL,
                        False,
                    )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-assert DP33=False if the device pushes DP33=True after a power cycle."""
        if self._data.virtual_auto_lock:
            datapoint = self._device.datapoints[self._mapping.dp_id]
            current_dp33 = bool(datapoint.value) if datapoint else False
            if current_dp33 and not self._last_dp33:
                _LOGGER.warning(
                    "[%s] Device pushed DP33=True while virtual auto-lock is active "
                    "— re-asserting hardware auto-lock OFF",
                    self._device.address,
                )
                dp = self._device.datapoints.get_or_create(
                    self._mapping.dp_id,
                    GimdowBLEDataPointType.DT_BOOL,
                    False,
                )
                self._device._create_safe_task(dp.set_value(False))
            self._last_dp33 = current_dp33
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._data.virtual_auto_lock

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._data.virtual_auto_lock = True
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, self._data.virtual_auto_lock_signal)
        # Keep hardware auto-lock OFF so HA controls timing.
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            GimdowBLEDataPointType.DT_BOOL,
            False,
        )
        await datapoint.set_value(False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._data.virtual_auto_lock = False
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, self._data.virtual_auto_lock_signal)
        # Keep hardware auto-lock OFF.
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            GimdowBLEDataPointType.DT_BOOL,
            False,
        )
        await datapoint.set_value(False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GimdowBLEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gimdow BLE switches."""
    data = entry.runtime_data
    door_sensor = entry.options.get(CONF_DOOR_SENSOR)
    mappings = get_mapping_by_device(data.device)
    entities: list[GimdowBLESwitch | GimdowBLEVirtualAutoLockSwitch] = []
    for m in mappings:
        if m.force_add or data.device.datapoints.has_id(m.dp_id, m.dp_type):
            # The auto_lock switch becomes virtual when a door sensor is configured —
            # HA manages timing and keeps hardware DP33 always OFF.
            if m.description.key == "auto_lock" and door_sensor:
                entities.append(
                    GimdowBLEVirtualAutoLockSwitch(
                        data.coordinator,
                        data.device,
                        data.product,
                        m,
                        data=data,
                    )
                )
            else:
                entities.append(
                    GimdowBLESwitch(
                        data.coordinator,
                        data.device,
                        data.product,
                        m,
                        data=data,
                    )
                )
    async_add_entities(entities)
