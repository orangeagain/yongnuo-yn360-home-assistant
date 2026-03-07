import asyncio
import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ADDRESS,
    CONF_IDLE_DISCONNECT_SECONDS,
    CONF_MODEL,
    DATA_BLE_SLOT_SEMAPHORE,
    DEFAULT_IDLE_DISCONNECT_SECONDS,
    DOMAIN,
    MAX_COLOR_TEMP_KELVIN,
    MIN_COLOR_TEMP_KELVIN,
)
from .models import async_guess_model_for_address, get_model_profile
from .yongnuo_yn360_device import YongnuoYn360Device

_LOGGER = logging.getLogger(__name__)


def remap_brightness(value: int) -> int:
    return max(1, min(100, round((value / 255) * 100)))


def clamp_kelvin(value: int) -> int:
    return min(max(value, MIN_COLOR_TEMP_KELVIN), MAX_COLOR_TEMP_KELVIN)


class YongnuoLight(LightEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:led-strip"

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        model: str,
        slot_limiter: asyncio.Semaphore,
        idle_disconnect_seconds: float,
    ):
        self._address = address
        self._profile = get_model_profile(model)
        self._attr_unique_id = f"yongnuo_{self._address.replace(':', '').lower()}"
        self._device = YongnuoYn360Device(
            hass,
            address,
            self._profile.key,
            slot_limiter=slot_limiter,
            idle_disconnect_seconds=idle_disconnect_seconds,
        )
        self._is_on = False
        self._rgb_color = (255, 255, 255)
        self._brightness = 255
        self._color_temp_kelvin = (MIN_COLOR_TEMP_KELVIN + MAX_COLOR_TEMP_KELVIN) // 2
        self._color_mode = (
            ColorMode.RGB if self._profile.supports_rgb else ColorMode.COLOR_TEMP
        )
        _LOGGER.debug(
            "Initialized light entity for %s with model=%s rgb=%s ct=%s idle_disconnect_seconds=%.1f",
            self._address,
            self._profile.label,
            self._profile.supports_rgb,
            self._profile.supports_color_temp,
            idle_disconnect_seconds,
        )

    @property
    def is_on(self):
        return self._is_on

    @property
    def brightness(self):
        return self._brightness

    @property
    def rgb_color(self):
        if not self._profile.supports_rgb:
            return None
        return self._rgb_color

    @property
    def color_temp_kelvin(self):
        if not self._profile.supports_color_temp:
            return None
        return self._color_temp_kelvin

    @property
    def min_color_temp_kelvin(self):
        if not self._profile.supports_color_temp:
            return None
        return MIN_COLOR_TEMP_KELVIN

    @property
    def max_color_temp_kelvin(self):
        if not self._profile.supports_color_temp:
            return None
        return MAX_COLOR_TEMP_KELVIN

    @property
    def supported_color_modes(self):
        modes = set()
        if self._profile.supports_rgb:
            modes.add(ColorMode.RGB)
        if self._profile.supports_color_temp:
            modes.add(ColorMode.COLOR_TEMP)
        return modes

    @property
    def color_mode(self):
        return self._color_mode

    @property
    def device_info(self):
        return {
            "identifiers": {("YONGNUO", self._address)},
            "name": f"{self._profile.label} ({self._address})",
            "manufacturer": "YONGNUO",
            "model": self._profile.device_model,
            "via_device": None,
        }

    async def async_turn_on(self, **kwargs):
        _LOGGER.debug(
            "turn_on requested for %s model=%s kwargs=%s current_mode=%s current_brightness=%s",
            self._address,
            self._profile.label,
            kwargs,
            self._color_mode,
            self._brightness,
        )

        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]

        if self._profile.supports_rgb and ATTR_RGB_COLOR in kwargs:
            self._rgb_color = tuple(kwargs[ATTR_RGB_COLOR])
            self._color_mode = ColorMode.RGB

        if self._profile.supports_color_temp:
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                self._color_temp_kelvin = clamp_kelvin(kwargs[ATTR_COLOR_TEMP_KELVIN])
                self._color_mode = ColorMode.COLOR_TEMP

        brightness_pct = remap_brightness(self._brightness)

        if self._color_mode == ColorMode.COLOR_TEMP and self._profile.supports_color_temp:
            if not self._is_on and not self._profile.supports_rgb:
                _LOGGER.debug(
                    "Waking color-temp-only light %s model=%s before CT command",
                    self._address,
                    self._profile.label,
                )
                await self._device.wake_up()
                # YN150WY needs a brief settle time after A1 before AA CT packets apply.
                await asyncio.sleep(0.5)
            _LOGGER.debug(
                "Applying color temperature to %s model=%s kelvin=%s brightness_pct=%s",
                self._address,
                self._profile.label,
                self._color_temp_kelvin,
                brightness_pct,
            )
            await self._device.set_color_temperature(self._color_temp_kelvin, brightness_pct)
        else:
            r, g, b = self._rgb_color
            _LOGGER.debug(
                "Applying RGB to %s model=%s rgb=%s brightness_pct=%s",
                self._address,
                self._profile.label,
                (r, g, b),
                brightness_pct,
            )
            await self._device.set_rgb(r, g, b, brightness_pct)

        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        _LOGGER.debug("turn_off requested for %s model=%s", self._address, self._profile.label)
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
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, entry.data)
    address = entry_data[CONF_ADDRESS]
    model = entry_data.get(CONF_MODEL)
    if not model:
        model = await async_guess_model_for_address(hass, address)
    idle_disconnect_seconds = float(
        entry.options.get(
            CONF_IDLE_DISCONNECT_SECONDS,
            DEFAULT_IDLE_DISCONNECT_SECONDS,
        )
    )
    slot_limiter = hass.data[DOMAIN][DATA_BLE_SLOT_SEMAPHORE]

    _LOGGER.debug(
        "Setting up light entry %s with model=%s idle_disconnect_seconds=%.1f",
        address,
        get_model_profile(model).label,
        idle_disconnect_seconds,
    )
    async_add_entities(
        [
            YongnuoLight(
                hass,
                address,
                model,
                slot_limiter,
                idle_disconnect_seconds,
            )
        ]
    )
