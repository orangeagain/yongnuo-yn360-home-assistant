from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
)
from .yongnuo_yn360_device import YongnuoYn360Device
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


def remap_brightness(value: int) -> int:
    return max(1, min(100, round((value / 255) * 100)))


class YongnuoLight(LightEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:led-strip"

    def __init__(self, hass: HomeAssistant, address: str):
        self._address = address
        self._attr_unique_id = f"yongnuo_{self._address.replace(':', '').lower()}"
        self._device = YongnuoYn360Device(hass, address)
        self._is_on = False
        self._rgb_color = (255, 255, 255)
        self._brightness = 255

    @property
    def is_on(self):
        return self._is_on

    @property
    def brightness(self):
        return self._brightness

    @property
    def rgb_color(self):
        return self._rgb_color

    @property
    def supported_color_modes(self):
        return {ColorMode.RGB}

    @property
    def color_mode(self):
        return ColorMode.RGB

    @property
    def device_info(self):
        return {
            "identifiers": {("YONGNUO", self._address)},
            "name": f"YN360 LED video light ({self._address})",
            "manufacturer": "YONGNUO",
            "model": "YN360 LED video light",
            "via_device": None,
        }

    async def async_turn_on(self, **kwargs):
        r, g, b = self._rgb_color
        brightness_pct = remap_brightness(self._brightness)

        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            self._rgb_color = (r, g, b)

        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
            brightness_pct = remap_brightness(self._brightness)

        self._is_on = True
        await self._device.set_color(r, g, b, brightness_pct)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self._device.turn_off()
        self._is_on = False
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        await self._device.async_shutdown()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    address = entry.data["address"]
    async_add_entities([YongnuoLight(hass, address)])
