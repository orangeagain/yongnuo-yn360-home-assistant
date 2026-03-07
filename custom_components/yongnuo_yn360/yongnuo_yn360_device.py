import asyncio
import contextlib
import logging
import struct
from dataclasses import dataclass

from bleak import BleakClient
from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.core import HomeAssistant

from .const import MAX_COLOR_TEMP_KELVIN, MAX_WHITE_LEVEL, MIN_COLOR_TEMP_KELVIN
from .models import get_model_profile

CHARACTERISTIC_UUID = "f000aa61-0451-4000-b000-000000000000"
_LOGGER = logging.getLogger(__name__)


def _hex(data: bytes) -> str:
    return data.hex(" ").upper()


@dataclass
class PendingCommand:
    packet: bytes
    reason: str
    seq: int


class YongnuoYn360Device:
    """YONGNUO BLE transport with persistent connection + high-speed coalescing."""

    def __init__(self, hass: HomeAssistant, address: str, model: str):
        self.hass = hass
        self.address = address
        self.profile = get_model_profile(model)

        self._worker_task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()
        self._pending: PendingCommand | None = None

        self._client: BleakClient | None = None
        self._ble_device = None
        self._conn_lock = asyncio.Lock()

        self._coalesce_window = 0.02
        self._min_send_interval = 0.01

        self._seq = 0

        self._idle_disconnect_seconds = 12.0
        self._idle_disconnect_task: asyncio.Task | None = None

    async def async_shutdown(self) -> None:
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

        if self._idle_disconnect_task and not self._idle_disconnect_task.done():
            self._idle_disconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_disconnect_task

        await self._disconnect_client()

    def _start_worker_if_needed(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self._worker(),
                name=f"yongnuo-worker-{self.address}",
            )

    def _touch_idle_timer(self) -> None:
        if self._idle_disconnect_task and not self._idle_disconnect_task.done():
            self._idle_disconnect_task.cancel()
        self._idle_disconnect_task = asyncio.create_task(self._idle_disconnect_watchdog())

    async def _idle_disconnect_watchdog(self) -> None:
        try:
            await asyncio.sleep(self._idle_disconnect_seconds)
            await self._disconnect_client()
        except asyncio.CancelledError:
            raise

    def _enqueue_latest(self, packet: bytes, reason: str) -> None:
        self._seq += 1
        self._pending = PendingCommand(packet=packet, reason=reason, seq=self._seq)
        _LOGGER.debug(
            "Queue command for %s model=%s seq=%s reason=%s packet=%s",
            self.address,
            self.profile.label,
            self._seq,
            reason,
            _hex(packet),
        )
        self._start_worker_if_needed()
        self._wake_event.set()

    def _has_newer_command(self, seq: int) -> bool:
        return self._pending is not None and self._pending.seq > seq

    async def _worker(self) -> None:
        while True:
            await self._wake_event.wait()
            self._wake_event.clear()

            await asyncio.sleep(self._coalesce_window)

            while self._pending is not None:
                cmd = self._pending
                self._pending = None

                try:
                    await self._send_with_policy(cmd.packet, seq=cmd.seq)
                    _LOGGER.debug(
                        "Sent command to %s model=%s seq=%s reason=%s packet=%s",
                        self.address,
                        self.profile.label,
                        cmd.seq,
                        cmd.reason,
                        _hex(cmd.packet),
                    )

                    if cmd.reason == "turn_off":
                        await self._disconnect_client()
                        _LOGGER.debug("Disconnected BLE after turn_off for %s", self.address)
                except Exception as err:
                    if self._has_newer_command(cmd.seq) or self._wake_event.is_set():
                        _LOGGER.debug(
                            "Stale command failed for %s but newer command pending: %s",
                            self.address,
                            err,
                        )
                    else:
                        _LOGGER.warning(
                            "Command failed for %s (%s): %s",
                            self.address,
                            cmd.reason,
                            err,
                        )

                self._touch_idle_timer()
                await asyncio.sleep(self._min_send_interval)

    async def _resolve_ble_device(self):
        if self._ble_device is not None:
            _LOGGER.debug("Using cached BLE device for %s", self.address)
            return self._ble_device

        ble_device = async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device:
            self._ble_device = ble_device
            _LOGGER.debug("Resolved connectable BLE device for %s via bluetooth manager", self.address)
            return self._ble_device

        for info in async_discovered_service_info(self.hass):
            if info.address == self.address:
                self._ble_device = info.device
                _LOGGER.info("Resolved device for %s from fallback discovery: %s", self.address, info.device)
                return self._ble_device

        _LOGGER.debug("Failed to resolve BLE device for %s from discovery cache", self.address)
        return None

    async def _ensure_connected(self) -> BleakClient:
        async with self._conn_lock:
            if self._client and self._client.is_connected:
                _LOGGER.debug("BLE client already connected for %s", self.address)
                return self._client

            ble_device = await self._resolve_ble_device()
            if not ble_device:
                raise RuntimeError(f"BLE device {self.address} not found or not connectable")

            client = BleakClient(ble_device, timeout=4.0)
            _LOGGER.debug(
                "Connecting BLE client for %s model=%s name=%r",
                self.address,
                self.profile.label,
                getattr(ble_device, "name", None),
            )
            await client.connect()
            self._client = client
            _LOGGER.debug("BLE client connected for %s", self.address)
            return client

    async def _disconnect_client(self) -> None:
        async with self._conn_lock:
            if self._client is None:
                return
            try:
                if self._client.is_connected:
                    _LOGGER.debug("Disconnecting BLE client for %s", self.address)
                    await self._client.disconnect()
            finally:
                self._client = None

    async def _send_once(self, data: bytes) -> None:
        client = await self._ensure_connected()
        try:
            _LOGGER.debug(
                "Writing GATT char for %s characteristic=%s packet=%s",
                self.address,
                CHARACTERISTIC_UUID,
                _hex(data),
            )
            await client.write_gatt_char(CHARACTERISTIC_UUID, data, response=False)
        except Exception as err:
            _LOGGER.warning("GATT write failed for %s packet=%s: %s", self.address, _hex(data), err)
            await self._disconnect_client()
            raise

    async def _send_with_policy(self, data: bytes, seq: int) -> None:
        try:
            await self._send_once(data)
            return
        except Exception as first_error:
            last_error: Exception | None = first_error

        if self._has_newer_command(seq) or self._wake_event.is_set():
            _LOGGER.debug("Skip retry for %s due to newer command", self.address)
            return

        for delay in (0.0, 0.12, 0.25, 0.45):
            if self._has_newer_command(seq) or self._wake_event.is_set():
                _LOGGER.debug("Abort retries for %s because newer command arrived", self.address)
                return

            if delay > 0:
                await asyncio.sleep(delay)

            try:
                _LOGGER.debug(
                    "Retrying command for %s seq=%s after %.2fs packet=%s",
                    self.address,
                    seq,
                    delay,
                    _hex(data),
                )
                await self._send_once(data)
                return
            except Exception as err:
                last_error = err

        raise RuntimeError(f"Failed to send final command to {self.address}: {last_error}")

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
        if not self.profile.supports_color_temp or self.profile.color_temp_channel is None:
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
            self.profile.color_temp_channel,
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
            self.profile.color_temp_channel,
            cool,
            warm,
            _hex(packet),
        )
        return packet

    async def set_rgb(self, r: int, g: int, b: int, brightness: int) -> None:
        self._enqueue_latest(self._build_rgb_packet(r, g, b, brightness), reason="set_rgb")

    async def set_color(self, r: int, g: int, b: int, brightness: int) -> None:
        await self.set_rgb(r, g, b, brightness)

    async def set_color_temperature(self, color_temp_kelvin: int, brightness: int) -> None:
        self._enqueue_latest(
            self._build_color_temp_packet(color_temp_kelvin, brightness),
            reason="set_color_temperature",
        )

    async def turn_off(self) -> None:
        packet = struct.pack(">BBBBBB", 0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56)
        self._enqueue_latest(packet, reason="turn_off")
