import re

from homeassistant import config_entries
import voluptuous as vol
from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.helpers import selector

from .const import DOMAIN

# 已知 YN360 服务 UUID；其余型号不一定一致，所以不能只靠 UUID 过滤
YONGNUO_SERVICE_UUIDS = {
    "f000aa60-0451-4000-b000-000000000000",
}

# 命名规则：YN + 型号数字（例如 YN100 / YN150 / YN150Ultra RGB / YN150WY）
YONGNUO_NAME_PATTERNS = (
    re.compile(r"^YN\d+", re.IGNORECASE),
    re.compile(r"^YONGNUO", re.IGNORECASE),
)


class YongnuoYn360ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 0

    @staticmethod
    def _is_likely_yongnuo_device(info) -> bool:
        advertisement = info.advertisement
        if advertisement:
            service_uuids = {uuid.lower() for uuid in (advertisement.service_uuids or [])}
            if service_uuids & YONGNUO_SERVICE_UUIDS:
                return True

        name = (info.name or "").strip()
        return any(pattern.match(name) for pattern in YONGNUO_NAME_PATTERNS)

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            address = user_input["address"].strip().upper()
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"YONGNUO Light ({address})",
                data={"address": address},
            )

        discovered_infos = async_discovered_service_info(self.hass)
        devices = {
            info.address: f"{info.name or 'Unknown'} ({info.address})"
            for info in discovered_infos
            if self._is_likely_yongnuo_device(info)
        }

        # 即使自动发现不到，也允许手动输入 MAC 地址（兼容 YN100/YN150 等）
        if devices:
            options = [{"label": name, "value": address} for address, name in devices.items()]
            schema = vol.Schema(
                {
                    vol.Required("address"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            translation_key="address",
                            mode="dropdown",
                            custom_value=True,
                        )
                    )
                }
            )
        else:
            errors["base"] = "no_devices_found"
            schema = vol.Schema(
                {
                    vol.Required("address"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    )
                }
            )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )
