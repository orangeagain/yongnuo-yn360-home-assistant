import asyncio
import struct
import logging
import contextlib
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

        # Retry strategy: only low-speed/final command gets retries.
        self._idle_retry_threshold = 0.22
        self._last_enqueue_ts = 0.0

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
        self._pending = PendingCommand(packet=packet, reason=reason)
        self._last_enqueue_ts = asyncio.get_running_loop().time()
        self._start_worker_if_needed()
        self._wake_event.set()

    async def _worker(self) -> None:
        while True:
            await self._wake_event.wait()
            self._wake_event.clear()

            # Tiny coalescing window to keep only most recent command.
            await asyncio.sleep(self._coalesce_window)

            while self._pending is not None:
                cmd = self._pending
                self._pending = None

                now = asyncio.get_running_loop().time()
                idle_for = now - self._last_enqueue_ts
                allow_retry = idle_for >= self._idle_retry_threshold

                try:
                    await self._send_with_policy(cmd.packet, allow_retry=allow_retry)
                    _LOGGER.debug(
                        "Sent command to %s (%s), allow_retry=%s",
                        self.address,
                        cmd.reason,
                        allow_retry,
                    )
                except Exception as err:
                    # If newer command exists, stale command errors are ignored.
                    if self._pending is not None or self._wake_event.is_set():
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
            # Connection may be stale; reconnect once and rethrow to policy.
            await self._disconnect_client()
            raise

    async def _send_with_policy(self, data: bytes, allow_retry: bool) -> None:
        # High-speed mode: send once, no retries.
        if not allow_retry:
            await self._send_once(data)
            return

        # Low-speed/final command mode: retry the last command only.
        retry_delays = (0.12, 0.25, 0.45)
        last_error: Exception | None = None

        for delay in (*retry_delays, None):
            try:
                await self._send_once(data)
                return
            except Exception as err:
                last_error = err

                # If a new command appears, stop retrying stale command immediately.
                if self._pending is not None or self._wake_event.is_set():
                    _LOGGER.debug(
                        "Abort retries for %s because newer command arrived",
                        self.address,
                    )
                    return

                if delay is None:
                    break
                await asyncio.sleep(delay)

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
