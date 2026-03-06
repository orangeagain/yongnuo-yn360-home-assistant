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

CHARACTERISTIC_UUID = "f000aa61-0451-4000-b000-000000000000"
_LOGGER = logging.getLogger(__name__)


@dataclass
class PendingCommand:
    packet: bytes
    reason: str
    seq: int


class YongnuoYn360Device:
    """YN360 BLE transport with persistent connection + high-speed coalescing.

    Design goals:
    - Persistent BLE connection per device (fast repeated writes).
    - Keep only latest pending command (drop stale intermediate states).
    - High-speed burst: no retry.
    - Low-speed final command: retry enabled.
    """

    def __init__(self, hass: HomeAssistant, address: str):
        self.hass = hass
        self.address = address

        self._worker_task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()
        self._pending: PendingCommand | None = None

        self._client: BleakClient | None = None
        self._ble_device = None
        self._conn_lock = asyncio.Lock()

        # High-speed tuning
        self._coalesce_window = 0.02  # merge ultra-fast updates
        self._min_send_interval = 0.01  # pace writes lightly

        # Retry strategy:
        # - During high-speed command stream: no retry.
        # - Retry immediately only when this command is still the latest one.

        # Sequence number for detecting newer commands.
        self._seq = 0

        # Keep persistent connection while active; release after idle.
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
            self._worker_task = asyncio.create_task(self._worker(), name=f"yn360-worker-{self.address}")

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
        self._start_worker_if_needed()
        self._wake_event.set()

    def _has_newer_command(self, seq: int) -> bool:
        return self._pending is not None and self._pending.seq > seq

    async def _worker(self) -> None:
        while True:
            await self._wake_event.wait()
            self._wake_event.clear()

            # Tiny coalescing window to keep only most recent command.
            await asyncio.sleep(self._coalesce_window)

            while self._pending is not None:
                cmd = self._pending
                self._pending = None

                try:
                    await self._send_with_policy(cmd.packet, seq=cmd.seq)
                    _LOGGER.debug("Sent command to %s (%s)", self.address, cmd.reason)

                    # Release BLE ownership as soon as turn_off is delivered,
                    # so phone apps can reconnect immediately.
                    if cmd.reason == "turn_off":
                        await self._disconnect_client()
                        _LOGGER.debug("Disconnected BLE after turn_off for %s", self.address)
                except Exception as err:
                    # If newer command exists, stale command errors are ignored.
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
            return self._ble_device

        ble_device = async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device:
            self._ble_device = ble_device
            return self._ble_device

        for info in async_discovered_service_info(self.hass):
            if info.address == self.address:
                self._ble_device = info.device
                _LOGGER.info("Resolved device from fallback discovery: %s", info.device)
                return self._ble_device

        return None

    async def _ensure_connected(self) -> BleakClient:
        async with self._conn_lock:
            if self._client and self._client.is_connected:
                return self._client

            ble_device = await self._resolve_ble_device()
            if not ble_device:
                raise RuntimeError(f"BLE device {self.address} not found or not connectable")

            client = BleakClient(ble_device, timeout=4.0)
            await client.connect()
            self._client = client
            return client

    async def _disconnect_client(self) -> None:
        async with self._conn_lock:
            if self._client is None:
                return
            try:
                if self._client.is_connected:
                    await self._client.disconnect()
            finally:
                self._client = None

    async def _send_once(self, data: bytes) -> None:
        client = await self._ensure_connected()
        try:
            await client.write_gatt_char(CHARACTERISTIC_UUID, data, response=False)
        except Exception:
            # Connection may be stale; reconnect on next send.
            await self._disconnect_client()
            raise

    async def _send_with_policy(self, data: bytes, seq: int) -> None:
        # Always try once immediately.
        try:
            await self._send_once(data)
            return
        except Exception as first_error:
            last_error: Exception | None = first_error

        # If new commands are already queued, this is high-speed stream: do not retry.
        if self._has_newer_command(seq) or self._wake_event.is_set():
            _LOGGER.debug("Skip retry for %s due to newer command", self.address)
            return

        # Retry only while this command remains the latest one.
        for delay in (0.0, 0.12, 0.25, 0.45):
            if self._has_newer_command(seq) or self._wake_event.is_set():
                _LOGGER.debug("Abort retries for %s because newer command arrived", self.address)
                return

            if delay > 0:
                await asyncio.sleep(delay)

            try:
                await self._send_once(data)
                return
            except Exception as err:
                last_error = err

        raise RuntimeError(f"Failed to send final command to {self.address}: {last_error}")

    async def set_color(self, r: int, g: int, b: int, brightness: int):
        r = min(max(int(r * (brightness / 100)), 0), 255)
        g = min(max(int(g * (brightness / 100)), 0), 255)
        b = min(max(int(b * (brightness / 100)), 0), 255)
        packet = struct.pack(">BBBBBB", 0xAE, 0xA1, r, g, b, 0x56)
        self._enqueue_latest(packet, reason="set_color")

    async def turn_off(self):
        packet = struct.pack(">BBBBBB", 0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56)
        self._enqueue_latest(packet, reason="turn_off")
