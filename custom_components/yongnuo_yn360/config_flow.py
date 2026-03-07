from homeassistant import config_entries
from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_ADDRESS,
    CONF_IDLE_DISCONNECT_SECONDS,
    CONF_MODEL,
    DEFAULT_IDLE_DISCONNECT_SECONDS,
    DOMAIN,
    MAX_IDLE_DISCONNECT_SECONDS,
)
from .models import (
    get_discovery_name,
    get_discovery_info_for_address,
    get_model_profile,
    guess_model_from_discovery_info,
    is_likely_yongnuo_name,
)

YONGNUO_SERVICE_UUIDS = {
    "f000aa60-0451-4000-b000-000000000000",
}

class YongnuoYn360ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 0

    @staticmethod
    def async_get_options_flow(config_entry):
        return YongnuoYn360OptionsFlow(config_entry)

    @staticmethod
    def _is_likely_yongnuo_device(info) -> bool:
        advertisement = info.advertisement
        if advertisement:
            service_uuids = {uuid.lower() for uuid in (advertisement.service_uuids or [])}
            if service_uuids & YONGNUO_SERVICE_UUIDS:
                return True

        return is_likely_yongnuo_name(get_discovery_name(info))

    @staticmethod
    def _format_device_label(info) -> str:
        name = get_discovery_name(info) or "Unknown"
        profile = get_model_profile(guess_model_from_discovery_info(info))
        return f"{name} [{profile.label}] ({info.address})"

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            discovery_info = get_discovery_info_for_address(self.hass, address)
            detected_model = None
            if discovery_info is not None and get_discovery_name(discovery_info):
                detected_model = guess_model_from_discovery_info(discovery_info)

            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            data = {
                CONF_ADDRESS: address,
            }
            title = f"YONGNUO light ({address})"
            if detected_model:
                profile = get_model_profile(detected_model)
                data[CONF_MODEL] = profile.key
                title = f"{profile.label} ({address})"

            return self.async_create_entry(
                title=title,
                data=data,
            )

        discovered_infos = [
            info
            for info in async_discovered_service_info(self.hass)
            if self._is_likely_yongnuo_device(info)
        ]

        discovered_infos.sort(key=lambda info: (get_discovery_name(info) or "", info.address))

        if discovered_infos:
            address_selector = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": self._format_device_label(info), "value": info.address}
                        for info in discovered_infos
                    ],
                    mode="dropdown",
                    custom_value=True,
                )
            )
        else:
            errors["base"] = "no_devices_found"
            address_selector = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): address_selector,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )


class YongnuoYn360OptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_IDLE_DISCONNECT_SECONDS: float(
                        user_input[CONF_IDLE_DISCONNECT_SECONDS]
                    )
                },
            )

        idle_disconnect_seconds = float(
            self._config_entry.options.get(
                CONF_IDLE_DISCONNECT_SECONDS,
                DEFAULT_IDLE_DISCONNECT_SECONDS,
            )
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_IDLE_DISCONNECT_SECONDS,
                    default=idle_disconnect_seconds,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=MAX_IDLE_DISCONNECT_SECONDS,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
