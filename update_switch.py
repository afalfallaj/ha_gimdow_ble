import re

with open("custom_components/gimdow_ble/switch.py", "r") as f:
    content = f.read()

new_class = """class GimdowBLESwitch(GimdowBLEEntity, SwitchEntity, RestoreEntity):
    \"\"\"Representation of a Gimdow BLE Switch backed directly by a device DP.\"\"\"

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
        self._master_state: bool | None = None

    @property
    def extra_restore_state_data(self) -> _VirtualAutoLockExtraData:
        return _VirtualAutoLockExtraData(self.is_on)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._product.is_lock:
            if (last_extra := await self.async_get_last_extra_data()) is not None:
                restored = _VirtualAutoLockExtraData.from_dict(last_extra.as_dict())
                if restored is not None:
                    self._master_state = restored.is_on
                    self._device.datapoints.get_or_create(
                        self._mapping.dp_id,
                        GimdowBLEDataPointType.DT_BOOL,
                        restored.is_on,
                    )

    @callback
    def _handle_coordinator_update(self) -> None:
        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            device_state = bool(datapoint.value)
            
            if self._master_state is None:
                self._master_state = device_state
            elif device_state != self._master_state:
                _LOGGER.warning(
                    "[%s] Device pushed %s=%s, but HA master is %s. Overwriting device.",
                    self._device.address,
                    self._mapping.description.key,
                    device_state,
                    self._master_state
                )
                dp = self._device.datapoints.get_or_create(
                    self._mapping.dp_id,
                    GimdowBLEDataPointType.DT_BOOL,
                    self._master_state,
                )
                self._device._create_safe_task(dp.set_value(self._master_state))

        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        if self._master_state is not None:
            return self._master_state
        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            return bool(datapoint.value)
        return False

    async def _write_dp(self, turn_on: bool) -> None:
        self._master_state = turn_on
        self.async_write_ha_state()
        
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
"""

content = re.sub(r'class GimdowBLESwitch\(GimdowBLEEntity, SwitchEntity\):.*?(?=\n\n\n# -{75}\n# Virtual auto-lock switch)', new_class, content, flags=re.DOTALL)

with open("custom_components/gimdow_ble/switch.py", "w") as f:
    f.write(content)
