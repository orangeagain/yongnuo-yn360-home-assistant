import logging
import struct

from .ble_dispatcher import BleOperationStep, YongnuoBleDispatcher
from .const import (
    DEFAULT_IDLE_DISCONNECT_SECONDS,
    MAX_COLOR_TEMP_CHANNEL,
    MAX_COLOR_TEMP_KELVIN,
    MIN_COLOR_TEMP_CHANNEL,
    MAX_WHITE_LEVEL,
    MIN_COLOR_TEMP_KELVIN,
)
from .models import get_model_profile

_LOGGER = logging.getLogger(__name__)


def _hex(data: bytes) -> str:
    return data.hex(" ").upper()


class YongnuoYn360Device:
    """YONGNUO protocol builder backed by the shared BLE dispatcher."""

    def __init__(
        self,
        address: str,
        model: str,
        dispatcher: YongnuoBleDispatcher,
        *,
        color_temp_channel: int | None = None,
        idle_disconnect_seconds: float = DEFAULT_IDLE_DISCONNECT_SECONDS,
    ):
        self.address = address
        self.profile = get_model_profile(model)
        self._dispatcher = dispatcher
        self._idle_disconnect_seconds = max(0.0, idle_disconnect_seconds)
        if color_temp_channel is None:
            self._color_temp_channel = self.profile.color_temp_channel
        else:
            self._color_temp_channel = max(
                MIN_COLOR_TEMP_CHANNEL,
                min(MAX_COLOR_TEMP_CHANNEL, int(color_temp_channel)),
            )

    async def async_shutdown(self) -> None:
        return

    async def _submit(
        self,
        reason: str,
        steps: tuple[BleOperationStep, ...],
        *,
        disconnect_after: bool = False,
    ) -> bool:
        return await self._dispatcher.async_submit(
            address=self.address,
            model_label=self.profile.label,
            reason=reason,
            steps=steps,
            idle_disconnect_seconds=self._idle_disconnect_seconds,
            disconnect_after=disconnect_after,
        )

    @staticmethod
    def _scale_rgb_channel(value: int, brightness: int) -> int:
        return min(max(int(value * (brightness / 100)), 0), 255)

    def _build_rgb_packet(self, r: int, g: int, b: int, brightness: int) -> bytes:
        if not self.profile.supports_rgb:
            raise ValueError(f"{self.profile.label} does not support RGB control")

        packet = struct.pack(
            ">BBBBBB",
            0xAE,
            0xA1,
            self._scale_rgb_channel(r, brightness),
            self._scale_rgb_channel(g, brightness),
            self._scale_rgb_channel(b, brightness),
            0x56,
        )
        _LOGGER.debug(
            "Built RGB packet for %s model=%s rgb=(%s,%s,%s) brightness_pct=%s packet=%s",
            self.address,
            self.profile.label,
            r,
            g,
            b,
            brightness,
            _hex(packet),
        )
        return packet

    def _build_color_temp_packet(self, color_temp_kelvin: int, brightness: int) -> bytes:
        if not self.profile.supports_color_temp or self._color_temp_channel is None:
            raise ValueError(f"{self.profile.label} does not support color temperature control")

        kelvin = min(max(color_temp_kelvin, MIN_COLOR_TEMP_KELVIN), MAX_COLOR_TEMP_KELVIN)
        brightness_ratio = max(0, min(brightness, 100)) / 100
        span = MAX_COLOR_TEMP_KELVIN - MIN_COLOR_TEMP_KELVIN
        cool_ratio = (kelvin - MIN_COLOR_TEMP_KELVIN) / span if span else 0
        warm_ratio = 1 - cool_ratio

        cool = round(MAX_WHITE_LEVEL * brightness_ratio * cool_ratio)
        warm = round(MAX_WHITE_LEVEL * brightness_ratio * warm_ratio)

        if brightness_ratio > 0 and cool == 0 and warm == 0:
            if cool_ratio >= warm_ratio:
                cool = 1
            else:
                warm = 1

        packet = struct.pack(
            ">BBBBBB",
            0xAE,
            0xAA,
            self._color_temp_channel,
            cool,
            warm,
            0x56,
        )
        _LOGGER.debug(
            "Built color-temp packet for %s model=%s kelvin=%s brightness_pct=%s ch=0x%02X cool=%s warm=%s packet=%s",
            self.address,
            self.profile.label,
            kelvin,
            brightness,
            self._color_temp_channel,
            cool,
            warm,
            _hex(packet),
        )
        return packet

    @staticmethod
    def _build_wake_packet() -> bytes:
        return struct.pack(">BBBBBB", 0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56)

    @staticmethod
    def _build_turn_off_packet() -> bytes:
        return struct.pack(">BBBBBB", 0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56)

    async def set_rgb(self, r: int, g: int, b: int, brightness: int) -> bool:
        return await self._submit(
            "set_rgb",
            (BleOperationStep(packet=self._build_rgb_packet(r, g, b, brightness)),),
        )

    async def set_color(self, r: int, g: int, b: int, brightness: int) -> bool:
        return await self.set_rgb(r, g, b, brightness)

    async def set_color_temperature(
        self,
        color_temp_kelvin: int,
        brightness: int,
        *,
        wake_before: bool = False,
        wake_delay_seconds: float = 0.0,
    ) -> bool:
        steps = []
        if wake_before:
            steps.append(
                BleOperationStep(
                    packet=self._build_wake_packet(),
                    delay_after_seconds=max(0.0, wake_delay_seconds),
                )
            )
        steps.append(
            BleOperationStep(
                packet=self._build_color_temp_packet(color_temp_kelvin, brightness)
            )
        )
        return await self._submit(
            "set_color_temperature_with_wake" if wake_before else "set_color_temperature",
            tuple(steps),
        )

    async def wake_up(self) -> bool:
        return await self._submit(
            "wake_up",
            (BleOperationStep(packet=self._build_wake_packet()),),
        )

    async def turn_off(self) -> bool:
        return await self._submit(
            "turn_off",
            (BleOperationStep(packet=self._build_turn_off_packet()),),
            disconnect_after=True,
        )
