import re

with open("custom_components/gimdow_ble/select.py", "r") as f:
    content = f.read()

# Change value_mapping typehint
content = content.replace("value_mapping: dict[str, str] | None = None", "value_mapping: dict[Any, str] | None = None")

# Add the new mapping
new_mapping = """                    dp_id=31,
                    description=SelectEntityDescription(
                        key="beep_volume",
                        options=[
                            "mute",
                            "normal",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
                GimdowBLESelectMapping(
                    dp_id=78,
                    description=SelectEntityDescription(
                        key="change_direction",
                        options=[
                            "right_hand",
                            "left_hand",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                    dp_type=GimdowBLEDataPointType.DT_BOOL,
                    value_mapping={
                        True: "right_hand",
                        False: "left_hand",
                    }
                ),"""
content = content.replace('                    dp_id=31,\n                    description=SelectEntityDescription(\n                        key="beep_volume",\n                        options=[\n                            "mute",\n                            "normal",\n                        ],\n                        entity_category=EntityCategory.CONFIG,\n                    ),\n                ),', new_mapping)

new_class = """class GimdowBLESelect(GimdowBLEEntity, SelectEntity, RestoreEntity):
    \"\"\"Representation of a Gimdow BLE select.\"\"\"

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device: GimdowBLEDevice,
        product: GimdowBLEProductInfo,
        mapping: GimdowBLESelectMapping,
    ) -> None:
        super().__init__(coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._attr_options = mapping.description.options
        self._master_option: str | None = None

    @property
    def extra_restore_state_data(self) -> _SelectExtraData:
        return _SelectExtraData(self.current_option)

    def _get_raw_from_option(self, value: str) -> Any:
        if self._mapping.value_mapping:
            for k, v in self._mapping.value_mapping.items():
                if v == value:
                    return k
        elif value in self._attr_options:
            return self._attr_options.index(value)
        return None

    def _get_option_from_raw(self, value: Any) -> str | None:
        if self._mapping.value_mapping:
            return self._mapping.value_mapping.get(value)
        elif isinstance(value, int) and 0 <= value < len(self._attr_options):
            return self._attr_options[value]
        elif isinstance(value, str):
            return value
        return None

    def _get_dp_type(self) -> GimdowBLEDataPointType:
        if self._mapping.value_mapping:
            return self._mapping.dp_type or GimdowBLEDataPointType.DT_STRING
        return self._mapping.dp_type or GimdowBLEDataPointType.DT_ENUM

    async def async_added_to_hass(self) -> None:
        \"\"\"Handle entity which will be added.\"\"\"
        await super().async_added_to_hass()

        if self._product.is_lock:
            if (last_extra := await self.async_get_last_extra_data()) is not None:
                restored = _SelectExtraData.from_dict(last_extra.as_dict())
                value = restored.option if restored is not None else None

                if value is not None:
                    self._master_option = value
                    raw_value = self._get_raw_from_option(value)
                    if raw_value is not None:
                        self._device.datapoints.get_or_create(
                            self._mapping.dp_id,
                            self._get_dp_type(),
                            raw_value,
                        )

    @callback
    def _handle_coordinator_update(self) -> None:
        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            device_option = self._get_option_from_raw(datapoint.value)
            
            if self._master_option is None:
                self._master_option = device_option
            elif device_option is not None and device_option != self._master_option:
                _LOGGER.warning(
                    "[%s] Device pushed %s=%s, but HA master is %s. Overwriting device.",
                    self._device.address,
                    self._mapping.description.key,
                    device_option,
                    self._master_option
                )
                raw_value = self._get_raw_from_option(self._master_option)
                if raw_value is not None:
                    dp = self._device.datapoints.get_or_create(
                        self._mapping.dp_id, self._get_dp_type(), raw_value
                    )
                    self._device._create_safe_task(dp.set_value(raw_value))
                    
        self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:
        \"\"\"Return the selected entity option to represent the entity state.\"\"\"
        if self._master_option is not None:
            return self._master_option
            
        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            return self._get_option_from_raw(datapoint.value)
        return None

    async def async_select_option(self, value: str) -> None:
        \"\"\"Change the selected option.\"\"\"
        if value in self._attr_options:
            self._master_option = value
            self.async_write_ha_state()
            
            raw_value = self._get_raw_from_option(value)
            if raw_value is not None:
                datapoint = self._device.datapoints.get_or_create(
                    self._mapping.dp_id,
                    self._get_dp_type(),
                    raw_value,
                )
                if datapoint:
                    await datapoint.set_value(raw_value)
"""

content = re.sub(r'class GimdowBLESelect\(GimdowBLEEntity, SelectEntity, RestoreEntity\):.*?(?=\n\n\nasync def async_setup_entry)', new_class, content, flags=re.DOTALL)

with open("custom_components/gimdow_ble/select.py", "w") as f:
    f.write(content)
