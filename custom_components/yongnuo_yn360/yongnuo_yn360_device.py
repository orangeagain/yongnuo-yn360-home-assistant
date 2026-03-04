import asyncio
import struct
import logging
from dataclasses import dataclass

from bleak import BleakClient
from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.core import HomeAssistant

CHARACTERISTIC_UUID = "f000aa61-0451-4000-b000-000000000000"
_LOGGER = logging.getLogger(__name__)


@dataclass
class PendingCommand:
    packet: bytes
    reason: str


class YongnuoYn360Device:
    """YN360 BLE transport with command coalescing + serialized writes.

    Why:
    - HA automations/scenes may toggle a light quickly.
    - BLE writes cannot be safely parallelized for one peripheral.
    - Keeping only the latest pending command avoids stale writes backlog.
    """

    def __init__(self, hass: HomeAssistant, address: str):
        self.hass = hass
        self.address = address

        self._worker_task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._pending: PendingCommand | None = None

        # Small pacing to avoid hammering BLE stack under burst updates.
        self._min_send_interval = 0.08

    async def async_shutdown(self) -> None:
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    def _start_worker_if_needed(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker(), name=f"yn360-worker-{self.address}")

    async def _worker(self) -> None:
        while True:
            await self._wake_event.wait()
            self._wake_event.clear()

            while self._pending is not None:
                cmd = self._pending
                self._pending = None
                try:
                    await self._connect_and_send(cmd.packet)
                    _LOGGER.debug("Sent command to %s (%s): %s", self.address, cmd.reason, cmd.packet.hex())
                except Exception as err:
                    # If this failed but a newer command is already pending, keep moving.
                    if self._pending is None:
                        _LOGGER.warning("Command failed for %s (%s): %s", self.address, cmd.reason, err)
                        raise
                    _LOGGER.debug(
                        "Command failed for %s but newer command exists, skipping stale failure: %s",
                        self.address,
                        err,
                    )

                await asyncio.sleep(self._min_send_interval)

    def _enqueue_latest(self, packet: bytes, reason: str) -> None:
        self._pending = PendingCommand(packet=packet, reason=reason)
        self._start_worker_if_needed()
        self._wake_event.set()

    async def _resolve_ble_device(self):
        ble_device = async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device:
            return ble_device

        _LOGGER.debug("No BLE device resolved via async_ble_device_from_address")
        for info in async_discovered_service_info(self.hass):
            if info.address == self.address:
                _LOGGER.info("Resolved device from fallback discovery: %s", info.device)
                return info.device

        return None

    async def _connect_and_send(self, data: bytes) -> None:
        async with self._send_lock:
            ble_device = await self._resolve_ble_device()
            if not ble_device:
                raise RuntimeError(f"BLE device {self.address} not found or not connectable")

            # Short retries: high-frequency control should fail fast and let newer command win.
            retry_delays = (0.05, 0.15, 0.35)
            last_error: Exception | None = None

            for idx, delay in enumerate((*retry_delays, None), start=1):
                try:
                    async with BleakClient(ble_device, timeout=4.0) as client:
                        await client.write_gatt_char(CHARACTERISTIC_UUID, data, response=False)
                        return
                except Exception as err:
                    last_error = err
                    if delay is None:
                        break
                    _LOGGER.debug(
                        "Connection attempt %d failed for %s: %s (retrying in %.2fs)",
                        idx,
                        self.address,
                        err,
                        delay,
                    )
                    await asyncio.sleep(delay)

            raise RuntimeError(f"Failed to send to {self.address}: {last_error}")

    async def set_color(self, r: int, g: int, b: int, brightness: int):
        r = min(max(int(r * (brightness / 100)), 0), 255)
        g = min(max(int(g * (brightness / 100)), 0), 255)
        b = min(max(int(b * (brightness / 100)), 0), 255)
        packet = struct.pack(">BBBBBB", 0xAE, 0xA1, r, g, b, 0x56)
        self._enqueue_latest(packet, reason="set_color")

    async def turn_off(self):
        packet = struct.pack(">BBBBBB", 0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56)
        self._enqueue_latest(packet, reason="turn_off")
