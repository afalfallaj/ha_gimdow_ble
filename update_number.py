import re

with open("custom_components/gimdow_ble/number.py", "r") as f:
    content = f.read()

new_class = """class GimdowBLENumber(GimdowBLEEntity, RestoreNumber):
    \"\"\"Representation of a Gimdow BLE Number.\"\"\"

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLENumberMapping,
        data: GimdowBLEData,
    ) -> None:
        super().__init__(coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._data = data
        self._attr_mode = mapping.mode
        self._master_value: float | None = None

    async def async_added_to_hass(self) -> None:
        \"\"\"Handle entity which will be added.\"\"\"
        await super().async_added_to_hass()

        if self._product.is_lock:
            # Restore via extra_restore_state_data (native_value), not the
            # stringified .state — an entity that was unavailable when HA
            # stopped (e.g. a sleeping BLE lock past its disconnect grace
            # period) dumps state="unavailable", which float()-parsing would
            # silently discard. extra_restore_state_data is captured
            # independently of availability, so it survives that case.
            if (last_data := await self.async_get_last_number_data()) is not None:
                if last_data.native_value is not None:
                    self._master_value = last_data.native_value
                    # Calculate raw value based on coefficient to store in device cache
                    # The property logic divides by coefficient, so we multiply here.
                    raw_value = int(last_data.native_value * self._mapping.coefficient)

                    self._device.datapoints.get_or_create(
                        self._mapping.dp_id,
                        GimdowBLEDataPointType.DT_VALUE,
                        raw_value,
                    )

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._mapping.getter:
            self.async_write_ha_state()
            return
            
        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            device_value = datapoint.value / self._mapping.coefficient
            
            if self._master_value is None:
                self._master_value = device_value
            elif device_value != self._master_value:
                _LOGGER.warning(
                    "[%s] Device pushed %s=%s, but HA master is %s. Overwriting device.",
                    self._device.address,
                    self._mapping.description.key,
                    device_value,
                    self._master_value
                )
                raw_value = int(self._master_value * self._mapping.coefficient)
                dp = self._device.datapoints.get_or_create(
                    self._mapping.dp_id,
                    GimdowBLEDataPointType.DT_VALUE,
                    raw_value,
                )
                self._device._create_safe_task(dp.set_value(raw_value))

        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        \"\"\"Return the entity value to represent the entity state.\"\"\"
        if self._mapping.getter:
            return self._mapping.getter(self, self._product)

        if self._master_value is None:
            datapoint = self._device.datapoints[self._mapping.dp_id]
            if datapoint:
                return datapoint.value / self._mapping.coefficient
            return None

        return self._master_value

    async def async_set_native_value(self, value: float) -> None:
        \"\"\"Set new value.\"\"\"
        if self._mapping.setter:
            self._mapping.setter(self, self._product, value)
            return
            
        self._master_value = value
        self.async_write_ha_state()
        
        int_value = int(value * self._mapping.coefficient)
        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            GimdowBLEDataPointType.DT_VALUE,
            int_value,
        )
        if datapoint:
            await datapoint.set_value(int_value)

        if self._mapping.send_time_signal:
            async_dispatcher_send(self.hass, self._data.virtual_auto_lock_time_signal)

    @property
    def available(self) -> bool:
        \"\"\"Return if entity is available.\"\"\"
        result = super().available
        if result and self._mapping.is_available:
            result = self._mapping.is_available(self, self._product)
        return result
"""

content = re.sub(r'class GimdowBLENumber\(GimdowBLEEntity, RestoreNumber\):.*?(?=\n\n\nasync def async_setup_entry)', new_class, content, flags=re.DOTALL)

with open("custom_components/gimdow_ble/number.py", "w") as f:
    f.write(content)
