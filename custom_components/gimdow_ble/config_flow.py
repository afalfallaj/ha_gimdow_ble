"""Config flow for Gimdow BLE integration."""

from __future__ import annotations

import logging
import pycountry
from typing import Any

import voluptuous as vol
from tuya_iot import AuthType

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlowWithConfigEntry,
)
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_DEVICE_ID,
    CONF_COUNTRY_CODE,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowHandler, FlowResult

from .gimdow_ble import SERVICE_UUID, GimdowBLEDeviceCredentials

from .const import (
    CONF_ACCESS_ID,
    CONF_ACCESS_SECRET,
    CONF_APP_TYPE,
    CONF_AUTH_TYPE,
    CONF_CATEGORY,
    CONF_DEVICE_NAME,
    CONF_ENDPOINT,
    CONF_FUNCTIONS,
    CONF_LOCAL_KEY,
    CONF_PRODUCT_ID,
    CONF_PRODUCT_MODEL,
    CONF_PRODUCT_NAME,
    CONF_STATUS_RANGE,
    CONF_UUID,
    CONF_DOOR_SENSOR,
    DOMAIN,
    SMARTLIFE_APP,
    TUYA_COUNTRIES,
    TUYA_RESPONSE_CODE,
    TUYA_RESPONSE_MSG,
    TUYA_RESPONSE_SUCCESS,
    TUYA_SMART_APP,
)
from .devices import GimdowBLEData, get_device_readable_name
from .cloud import HASSGimdowBLEDeviceManager

_LOGGER = logging.getLogger(__name__)


async def _try_login(
    manager: HASSGimdowBLEDeviceManager,
    user_input: dict[str, Any],
    errors: dict[str, str],
    placeholders: dict[str, Any],
) -> dict[str, Any] | None:
    response: dict[Any, Any] | None
    data: dict[str, Any]

    country = [
        country
        for country in TUYA_COUNTRIES
        if country.name == user_input[CONF_COUNTRY_CODE]
    ][0]

    data = {
        CONF_ENDPOINT: country.endpoint,
        CONF_AUTH_TYPE: AuthType.CUSTOM,
        CONF_ACCESS_ID: user_input[CONF_ACCESS_ID],
        CONF_ACCESS_SECRET: user_input[CONF_ACCESS_SECRET],
        CONF_USERNAME: user_input[CONF_USERNAME],
        CONF_PASSWORD: user_input[CONF_PASSWORD],
        CONF_COUNTRY_CODE: country.country_code,
    }

    for app_type in (TUYA_SMART_APP, SMARTLIFE_APP, ""):
        data[CONF_APP_TYPE] = app_type
        if app_type == "":
            data[CONF_AUTH_TYPE] = AuthType.CUSTOM
        else:
            data[CONF_AUTH_TYPE] = AuthType.SMART_HOME

        response = await manager._login(data, True)

        if response.get(TUYA_RESPONSE_SUCCESS, False):
            return data

    errors["base"] = "login_error"
    if response:
        placeholders.update(
            {
                TUYA_RESPONSE_CODE: response.get(TUYA_RESPONSE_CODE),
                TUYA_RESPONSE_MSG: response.get(TUYA_RESPONSE_MSG),
            }
        )

    return None


def _show_login_form(
    flow: FlowHandler,
    user_input: dict[str, Any],
    errors: dict[str, str],
    placeholders: dict[str, Any],
) -> FlowResult:
    """Shows the Tuya IOT platform login form."""
    if user_input is not None and user_input.get(CONF_COUNTRY_CODE) is not None:
        for country in TUYA_COUNTRIES:
            if country.country_code == user_input[CONF_COUNTRY_CODE]:
                user_input[CONF_COUNTRY_CODE] = country.name
                break

    def_country_name: str | None = None
    try:
        def_country = pycountry.countries.get(alpha_2=flow.hass.config.country)
        if def_country:
            def_country_name = def_country.name
    except Exception:
        pass

    sensor_default = user_input.get(CONF_DOOR_SENSOR)
    if sensor_default is None:
        sensor_default = vol.UNDEFINED

    schema = {
        vol.Required(
            CONF_COUNTRY_CODE,
            default=user_input.get(CONF_COUNTRY_CODE, def_country_name),
        ): vol.In(
            # We don't pass a dict {code:name} because country codes can be duplicate.
            [country.name for country in TUYA_COUNTRIES]
        ),
        vol.Required(
            CONF_ACCESS_ID, default=user_input.get(CONF_ACCESS_ID, "")
        ): str,
        vol.Required(
            CONF_ACCESS_SECRET,
            default=user_input.get(CONF_ACCESS_SECRET, ""),
        ): str,
        vol.Required(
            CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")
        ): str,
        vol.Required(
            CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")
        ): str,
        vol.Optional(
             CONF_DOOR_SENSOR,
             default=sensor_default,
        ): EntitySelector(
            EntitySelectorConfig(domain="binary_sensor")
        ),
    }

    return flow.async_show_form(
        step_id="login",
        data_schema=vol.Schema(schema),
        errors=errors,
        description_placeholders=placeholders,
    )


class GimdowBLEOptionsFlow(OptionsFlowWithConfigEntry):
    """Handle a Gimdow BLE options flow."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            _LOGGER.debug(f"Options flow init received user_input: {user_input}")
            options = dict(self.config_entry.options)
            
            door_sensor = user_input.get(CONF_DOOR_SENSOR)
            if door_sensor:
                _LOGGER.debug(f"Setting door sensor to: {door_sensor}")
                options[CONF_DOOR_SENSOR] = door_sensor
            else:
                _LOGGER.debug("Removing door sensor from options")
                options.pop(CONF_DOOR_SENSOR, None)

            _LOGGER.debug(f"Final options to save: {options}")
            return self.async_create_entry(title="", data=options)

        options = self.config_entry.options
        door_sensor_default = options.get(CONF_DOOR_SENSOR)
        if door_sensor_default is None:
            door_sensor_default = vol.UNDEFINED

        schema = {
            vol.Optional(
                CONF_DOOR_SENSOR,
                default=door_sensor_default,
            ): EntitySelector(
                EntitySelectorConfig(domain="binary_sensor")
            ),
        }

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
        )


class GimdowBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gimdow BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._data: dict[str, Any] = {}
        self._manager: HASSGimdowBLEDeviceManager | None = None
        self._get_device_info_error = False

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        if self._manager is None:
            self._manager = HASSGimdowBLEDeviceManager(self.hass, self._data)
        await self._manager.build_cache()
        self.context["title_placeholders"] = {
            "name": await get_device_readable_name(
                discovery_info,
                self._manager,
            )
        }
        return await self.async_step_login()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the user step."""
        if self._manager is None:
             self._manager = HASSGimdowBLEDeviceManager(self.hass, self._data)
        await self._manager.build_cache()
        return self.async_show_menu(step_id="user", menu_options=["login", "manual"])

    async def async_step_login(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the Tuya IOT platform login step."""
        data: dict[str, Any] | None = None
        errors: dict[str, str] = {}
        placeholders: dict[str, Any] = {}

        if user_input is not None:
            data = await _try_login(
                self._manager,
                user_input,
                errors,
                placeholders,
            )
            if data:
                self._data.update(data)
                if user_input.get(CONF_DOOR_SENSOR):
                    self._data[CONF_DOOR_SENSOR] = user_input[CONF_DOOR_SENSOR]
                return await self.async_step_device()

        if user_input is None:
            user_input = {}
            if self._discovery_info:
                await self._manager.get_device_credentials(
                    self._discovery_info.address,
                    False,
                    True,
                )
            if self._data is None or len(self._data) == 0:
                self._manager.get_login_from_cache()
            if self._data is not None and len(self._data) > 0:
                user_input.update(self._data)

        return _show_login_form(self, user_input, errors, placeholders)

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the user step to pick discovered device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]
            local_name = await get_device_readable_name(discovery_info, self._manager)
            await self.async_set_unique_id(
                discovery_info.address, raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            credentials = await self._manager.get_device_credentials(
                discovery_info.address, self._get_device_info_error, True
            )
            self._data[CONF_ADDRESS] = discovery_info.address
            if credentials is None:
                self._get_device_info_error = True
                errors["base"] = "device_not_registered"
            else:
                return self.async_create_entry(
                    title=local_name,
                    data={CONF_ADDRESS: discovery_info.address},
                    options=self._data,
                )

        if discovery := self._discovery_info:
            self._discovered_devices[discovery.address] = discovery
        else:
            current_addresses = self._async_current_ids()
            for discovery in async_discovered_service_info(self.hass):
                if (
                    discovery.address in current_addresses
                    or discovery.address in self._discovered_devices
                    or discovery.service_data is None
                    or not SERVICE_UUID in discovery.service_data.keys()
                ):
                    continue
                self._discovered_devices[discovery.address] = discovery

        if not self._discovered_devices:
            return self.async_abort(reason="no_unconfigured_devices")

        def_address: str
        if user_input:
            def_address = user_input.get(CONF_ADDRESS)
        else:
            def_address = list(self._discovered_devices)[0]

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDRESS,
                        default=def_address,
                    ): vol.In(
                        {
                            service_info.address: await get_device_readable_name(
                                service_info,
                                self._manager,
                            )
                            for service_info in self._discovered_devices.values()
                        }
                    ),
                },
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the manual entry step."""
        errors: dict[str, str] = {}
        if user_input is not None:
             address = user_input[CONF_ADDRESS]
             await self.async_set_unique_id(address, raise_on_progress=False)
             self._abort_if_unique_id_configured()
             
             # Create credentials dict
             creds = {
                 CONF_UUID: user_input[CONF_UUID],
                 CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY],
                 CONF_DEVICE_ID: user_input[CONF_DEVICE_ID],
                 CONF_DEVICE_NAME: user_input.get(CONF_DEVICE_NAME, "Gimdow Lock"),
                 CONF_CATEGORY: "jtmspro", # Default to Gimdow category
                 CONF_PRODUCT_ID: "rlyxv7pe", # Default product id
                 CONF_PRODUCT_MODEL: "A1 PRO MAX",
                 CONF_PRODUCT_NAME: "Gimdow Lock",
                 CONF_FUNCTIONS: [], # Manual entry assumes default functions or None
                 CONF_STATUS_RANGE: []
             }
             
             # Store in manager's cache effectively simulation a "cloud" device
             # Or just pass it to options?
             # The integration uses `HASSGimdowBLEDeviceManager` which mimics Tuya Cloud behavior.
             # We can just store these in the config entry data options.
             
             self._data.update(user_input)
             # Add credentials to manager data manually so they are saved
             self._manager.data.update({
                 f"{address}_credentials": creds
             })

             return self.async_create_entry(
                 title=user_input.get(CONF_DEVICE_NAME, "Gimdow Lock"),
                 data={CONF_ADDRESS: address},
                 options=self._manager.data,
             )

        # Populate address list
        current_addresses = self._async_current_ids()
        for discovery in async_discovered_service_info(self.hass):
             if (
                discovery.address in current_addresses
                or discovery.address in self._discovered_devices
                or discovery.service_data is None
                or not SERVICE_UUID in discovery.service_data.keys()
            ):
                continue
             self._discovered_devices[discovery.address] = discovery
             
        addresses = list(self._discovered_devices.keys())
        # Add any typed in address if previously failed validation etc? No.
        
        schema = {
            vol.Required(CONF_ADDRESS): vol.In(addresses) if addresses else str,
            vol.Required(CONF_UUID): str,
            vol.Required(CONF_LOCAL_KEY): str,
            vol.Required(CONF_DEVICE_ID): str,
            vol.Optional(CONF_DEVICE_NAME): str,
        }

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> GimdowBLEOptionsFlow:
        """Get the options flow for this handler."""
        return GimdowBLEOptionsFlow(config_entry)
