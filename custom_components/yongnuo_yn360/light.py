import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .ble_dispatcher import YongnuoBleDispatcher
from .const import (
    CONF_ADDRESS,
    CONF_COLOR_TEMP_CHANNEL,
    CONF_IDLE_DISCONNECT_SECONDS,
    CONF_MODEL,
    DATA_BLE_DISPATCHER,
    DEFAULT_IDLE_DISCONNECT_SECONDS,
    DOMAIN,
    MAX_COLOR_TEMP_CHANNEL,
    MAX_COLOR_TEMP_KELVIN,
    MIN_COLOR_TEMP_CHANNEL,
    MIN_COLOR_TEMP_KELVIN,
)
from .models import async_guess_model_for_address, get_model_profile
from .yongnuo_yn360_device import YongnuoYn360Device

_LOGGER = logging.getLogger(__name__)


def remap_brightness(value: int) -> int:
    return max(1, min(100, round((value / 255) * 100)))


def clamp_kelvin(value: int) -> int:
    return min(max(value, MIN_COLOR_TEMP_KELVIN), MAX_COLOR_TEMP_KELVIN)


def clamp_color_temp_channel(value: int) -> int:
    return min(max(value, MIN_COLOR_TEMP_CHANNEL), MAX_COLOR_TEMP_CHANNEL)


class YongnuoLight(LightEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:led-strip"

    def __init__(
        self,
        address: str,
        model: str,
        dispatcher: YongnuoBleDispatcher,
        color_temp_channel: int | None,
        idle_disconnect_seconds: float,
    ):
        self._address = address
        self._profile = get_model_profile(model)
        self._color_temp_channel = (
            clamp_color_temp_channel(color_temp_channel)
            if color_temp_channel is not None
            else self._profile.color_temp_channel
        )
        self._attr_unique_id = f"yongnuo_{self._address.replace(':', '').lower()}"
        self._device = YongnuoYn360Device(
            address,
            self._profile.key,
            dispatcher,
            color_temp_channel=self._color_temp_channel,
            idle_disconnect_seconds=idle_disconnect_seconds,
        )
        self._is_on = False
        self._rgb_color = (255, 255, 255)
        self._brightness = 255
        self._color_temp_kelvin = (MIN_COLOR_TEMP_KELVIN + MAX_COLOR_TEMP_KELVIN) // 2
        self._color_mode = self._default_color_mode()
        _LOGGER.debug(
            "Initialized light entity for %s with model=%s rgb=%s ct=%s ct_channel=%s idle_disconnect_seconds=%.1f",
            self._address,
            self._profile.label,
            self._profile.supports_rgb,
            self._profile.supports_color_temp,
            (
                f"0x{self._color_temp_channel:02X}"
                if self._color_temp_channel is not None
                else "n/a"
            ),
            idle_disconnect_seconds,
        )

    def _default_color_mode(self) -> ColorMode:
        if self._profile.supports_color_temp:
            return ColorMode.COLOR_TEMP
        return ColorMode.RGB

    @staticmethod
    def _clamp_rgb_color(value) -> tuple[int, int, int] | None:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return None

        try:
            return tuple(max(0, min(255, int(channel))) for channel in value)
        except (TypeError, ValueError):
            return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None:
            return

        attributes = last_state.attributes

        brightness = attributes.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            try:
                self._brightness = max(1, min(255, int(brightness)))
            except (TypeError, ValueError):
                _LOGGER.debug("Ignoring invalid restored brightness for %s: %r", self._address, brightness)

        if self._profile.supports_rgb:
            rgb_color = self._clamp_rgb_color(attributes.get(ATTR_RGB_COLOR))
            if rgb_color is not None:
                self._rgb_color = rgb_color

        if self._profile.supports_color_temp:
            color_temp_kelvin = attributes.get(ATTR_COLOR_TEMP_KELVIN)
            if color_temp_kelvin is not None:
                try:
                    self._color_temp_kelvin = clamp_kelvin(int(color_temp_kelvin))
                except (TypeError, ValueError):
                    _LOGGER.debug(
                        "Ignoring invalid restored color temperature for %s: %r",
                        self._address,
                        color_temp_kelvin,
                    )

        restored_mode = attributes.get(ATTR_COLOR_MODE)
        if restored_mode in self.supported_color_modes:
            self._color_mode = restored_mode
        elif self._profile.supports_color_temp and ATTR_COLOR_TEMP_KELVIN in attributes:
            self._color_mode = ColorMode.COLOR_TEMP
        elif self._profile.supports_rgb and ATTR_RGB_COLOR in attributes:
            self._color_mode = ColorMode.RGB

        _LOGGER.debug(
            "Restored state for %s model=%s mode=%s brightness=%s rgb=%s kelvin=%s",
            self._address,
            self._profile.label,
            self._color_mode,
            self._brightness,
            self._rgb_color,
            self._color_temp_kelvin,
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

    @property
    def extra_state_attributes(self):
        if not self._profile.supports_color_temp or self._color_temp_channel is None:
            return None

        return {
            "color_temp_channel": self._color_temp_channel,
            "color_temp_channel_hex": f"0x{self._color_temp_channel:02X}",
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

        new_brightness = self._brightness
        new_rgb_color = self._rgb_color
        new_color_temp_kelvin = self._color_temp_kelvin
        new_color_mode = self._color_mode

        if ATTR_BRIGHTNESS in kwargs:
            new_brightness = kwargs[ATTR_BRIGHTNESS]

        if self._profile.supports_rgb and ATTR_RGB_COLOR in kwargs:
            new_rgb_color = tuple(kwargs[ATTR_RGB_COLOR])
            new_color_mode = ColorMode.RGB

        if self._profile.supports_color_temp:
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                new_color_temp_kelvin = clamp_kelvin(kwargs[ATTR_COLOR_TEMP_KELVIN])
                new_color_mode = ColorMode.COLOR_TEMP

        brightness_pct = remap_brightness(new_brightness)

        if new_color_mode == ColorMode.COLOR_TEMP and self._profile.supports_color_temp:
            _LOGGER.debug(
                "Applying color temperature to %s model=%s kelvin=%s brightness_pct=%s",
                self._address,
                self._profile.label,
                new_color_temp_kelvin,
                brightness_pct,
            )
            sent = await self._device.set_color_temperature(
                new_color_temp_kelvin,
                brightness_pct,
                wake_before=not self._is_on and not self._profile.supports_rgb,
                wake_delay_seconds=0.5,
            )
        else:
            r, g, b = new_rgb_color
            _LOGGER.debug(
                "Applying RGB to %s model=%s rgb=%s brightness_pct=%s",
                self._address,
                self._profile.label,
                (r, g, b),
                brightness_pct,
            )
            sent = await self._device.set_rgb(r, g, b, brightness_pct)

        if not sent:
            _LOGGER.warning(
                "Skipped state update for %s model=%s because BLE operation expired or timed out",
                self._address,
                self._profile.label,
            )
            return

        self._brightness = new_brightness
        self._rgb_color = new_rgb_color
        self._color_temp_kelvin = new_color_temp_kelvin
        self._color_mode = new_color_mode
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        _LOGGER.debug("turn_off requested for %s model=%s", self._address, self._profile.label)
        sent = await self._device.turn_off()
        if not sent:
            _LOGGER.warning(
                "Skipped turn_off state update for %s model=%s because BLE operation expired or timed out",
                self._address,
                self._profile.label,
            )
            return
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
    profile = get_model_profile(model)
    idle_disconnect_seconds = float(
        entry.options.get(
            CONF_IDLE_DISCONNECT_SECONDS,
            DEFAULT_IDLE_DISCONNECT_SECONDS,
        )
    )
    configured_color_temp_channel = entry.options.get(CONF_COLOR_TEMP_CHANNEL)
    color_temp_channel = None
    if profile.supports_color_temp and profile.color_temp_channel is not None:
        if configured_color_temp_channel is None:
            color_temp_channel = profile.color_temp_channel
        else:
            color_temp_channel = clamp_color_temp_channel(
                int(configured_color_temp_channel)
            )
    dispatcher = hass.data[DOMAIN][DATA_BLE_DISPATCHER]

    _LOGGER.debug(
        "Setting up light entry %s with model=%s ct_channel=%s idle_disconnect_seconds=%.1f",
        address,
        profile.label,
        (
            f"0x{color_temp_channel:02X}"
            if color_temp_channel is not None
            else "n/a"
        ),
        idle_disconnect_seconds,
    )
    async_add_entities(
        [
            YongnuoLight(
                address,
                model,
                dispatcher,
                color_temp_channel,
                idle_disconnect_seconds,
            )
        ]
    )
