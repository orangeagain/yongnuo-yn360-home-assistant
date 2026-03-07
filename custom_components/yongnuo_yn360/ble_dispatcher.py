from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from bleak import BleakClient
from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.core import HomeAssistant

try:
    from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
except ImportError:  # pragma: no cover - fallback for older HA runtimes
    BleakClientWithServiceCache = BleakClient
    establish_connection = None

from .const import (
    BLE_DISCONNECT_TIMEOUT_SECONDS,
    CHARACTERISTIC_UUID,
    DEFAULT_IDLE_DISCONNECT_SECONDS,
    DEFAULT_OPERATION_MAX_AGE_SECONDS,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


def _hex(data: bytes) -> str:
    return data.hex(" ").upper()


@dataclass(frozen=True, slots=True)
class BleOperationStep:
    packet: bytes
    delay_after_seconds: float = 0.0


@dataclass(slots=True)
class QueuedBleOperation:
    address: str
    model_label: str
    reason: str
    steps: tuple[BleOperationStep, ...]
    future: asyncio.Future[bool]
    created_monotonic: float
    max_age_seconds: float
    timeout_seconds: float
    idle_disconnect_seconds: float
    disconnect_after: bool


class YongnuoBleDispatcher:
    """Global single-file BLE dispatcher for all Yongnuo devices."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._queue: asyncio.Queue[QueuedBleOperation] = asyncio.Queue()
        self._worker_task = asyncio.create_task(
            self._worker(),
            name="yongnuo-ble-dispatcher",
        )
        self._connection_lock = asyncio.Lock()
        self._idle_disconnect_task: asyncio.Task | None = None
        self._client: BleakClient | None = None
        self._client_address: str | None = None
        self._ble_devices: dict[str, object] = {}

    async def async_shutdown(self) -> None:
        idle_disconnect_task = self._idle_disconnect_task
        self._cancel_idle_disconnect()
        if idle_disconnect_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await idle_disconnect_task

        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

        while not self._queue.empty():
            queued = self._queue.get_nowait()
            if not queued.future.done():
                queued.future.cancel()

        await self._disconnect_current()

    async def async_submit(
        self,
        *,
        address: str,
        model_label: str,
        reason: str,
        steps: tuple[BleOperationStep, ...],
        idle_disconnect_seconds: float = DEFAULT_IDLE_DISCONNECT_SECONDS,
        max_age_seconds: float = DEFAULT_OPERATION_MAX_AGE_SECONDS,
        timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
        disconnect_after: bool = False,
    ) -> bool:
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        await self._queue.put(
            QueuedBleOperation(
                address=address,
                model_label=model_label,
                reason=reason,
                steps=steps,
                future=future,
                created_monotonic=asyncio.get_running_loop().time(),
                max_age_seconds=max(0.0, max_age_seconds),
                timeout_seconds=max(0.1, timeout_seconds),
                idle_disconnect_seconds=max(0.0, idle_disconnect_seconds),
                disconnect_after=disconnect_after,
            )
        )
        _LOGGER.debug(
            "Queued BLE operation for %s model=%s reason=%s queue_size=%s",
            address,
            model_label,
            reason,
            self._queue.qsize(),
        )
        return await future

    async def _worker(self) -> None:
        current_operation: QueuedBleOperation | None = None

        try:
            while True:
                current_operation = await self._queue.get()
                self._cancel_idle_disconnect()

                age_seconds = asyncio.get_running_loop().time() - current_operation.created_monotonic
                if current_operation.max_age_seconds > 0 and age_seconds > current_operation.max_age_seconds:
                    _LOGGER.warning(
                        "Dropped stale BLE operation for %s model=%s reason=%s age=%.2fs max_age=%.2fs",
                        current_operation.address,
                        current_operation.model_label,
                        current_operation.reason,
                        age_seconds,
                        current_operation.max_age_seconds,
                    )
                    await self._disconnect_current()
                    if not current_operation.future.done():
                        current_operation.future.set_result(False)
                    current_operation = None
                    continue

                try:
                    async with asyncio.timeout(current_operation.timeout_seconds):
                        for step in current_operation.steps:
                            await self._write_packet(
                                current_operation.address,
                                current_operation.model_label,
                                step.packet,
                            )
                            if step.delay_after_seconds > 0:
                                await asyncio.sleep(step.delay_after_seconds)
                except TimeoutError:
                    _LOGGER.warning(
                        "Dropped slow BLE operation for %s model=%s reason=%s timeout=%.2fs",
                        current_operation.address,
                        current_operation.model_label,
                        current_operation.reason,
                        current_operation.timeout_seconds,
                    )
                    await self._disconnect_current()
                    if not current_operation.future.done():
                        current_operation.future.set_result(False)
                except Exception as err:
                    _LOGGER.warning(
                        "BLE operation failed for %s model=%s reason=%s: %s",
                        current_operation.address,
                        current_operation.model_label,
                        current_operation.reason,
                        err,
                    )
                    await self._disconnect_current()
                    if not current_operation.future.done():
                        current_operation.future.set_exception(err)
                else:
                    _LOGGER.debug(
                        "Completed BLE operation for %s model=%s reason=%s steps=%s",
                        current_operation.address,
                        current_operation.model_label,
                        current_operation.reason,
                        len(current_operation.steps),
                    )
                    if not current_operation.future.done():
                        current_operation.future.set_result(True)
                finally:
                    if current_operation is not None:
                        if current_operation.disconnect_after or (
                            current_operation.idle_disconnect_seconds <= 0
                        ):
                            await self._disconnect_current()
                        else:
                            self._schedule_idle_disconnect(
                                current_operation.address,
                                current_operation.idle_disconnect_seconds,
                            )

                current_operation = None
        except asyncio.CancelledError:
            if current_operation is not None and not current_operation.future.done():
                current_operation.future.cancel()
            raise

    def _cancel_idle_disconnect(self) -> None:
        if self._idle_disconnect_task and not self._idle_disconnect_task.done():
            self._idle_disconnect_task.cancel()
        self._idle_disconnect_task = None

    def _schedule_idle_disconnect(self, address: str, delay_seconds: float) -> None:
        if delay_seconds <= 0 or self._client is None:
            return

        self._cancel_idle_disconnect()
        self._idle_disconnect_task = asyncio.create_task(
            self._idle_disconnect_watchdog(address, delay_seconds),
            name=f"yongnuo-idle-disconnect-{address}",
        )

    async def _idle_disconnect_watchdog(self, address: str, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            async with self._connection_lock:
                if self._client is None or self._client_address != address:
                    return
                _LOGGER.debug(
                    "Disconnecting idle BLE client for %s after %.1fs",
                    address,
                    delay_seconds,
                )
                await self._disconnect_locked()
        except asyncio.CancelledError:
            raise

    async def _resolve_ble_device(self, address: str):
        cached = self._ble_devices.get(address)
        if cached is not None:
            return cached

        ble_device = async_ble_device_from_address(self.hass, address, connectable=True)
        if ble_device is not None:
            self._ble_devices[address] = ble_device
            return ble_device

        for info in async_discovered_service_info(self.hass):
            if info.address == address:
                self._ble_devices[address] = info.device
                return info.device

        return None

    async def _connect_locked(self, address: str, model_label: str) -> BleakClient:
        if (
            self._client is not None
            and self._client.is_connected
            and self._client_address == address
        ):
            return self._client

        if self._client is not None:
            await self._disconnect_locked()

        ble_device = await self._resolve_ble_device(address)
        if ble_device is not None:
            client_name = getattr(ble_device, "name", None) or model_label
            _LOGGER.debug(
                "Connecting BLE client for %s model=%s name=%r",
                address,
                model_label,
                getattr(ble_device, "name", None),
            )
            if establish_connection is not None:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    client_name,
                    max_attempts=4,
                    timeout=4.0,
                )
            else:
                _LOGGER.debug(
                    "bleak-retry-connector unavailable for %s, falling back to BleakClient.connect()",
                    address,
                )
                client = BleakClient(ble_device, timeout=4.0)
                await client.connect()
        else:
            _LOGGER.warning(
                "BLE device %s missing from HA discovery cache; falling back to direct-address connect",
                address,
            )
            client = BleakClient(address, timeout=4.0)
            await client.connect()

        self._client = client
        self._client_address = address
        _LOGGER.debug("BLE client connected for %s", address)
        return client

    async def _write_packet(self, address: str, model_label: str, packet: bytes) -> None:
        async with self._connection_lock:
            client = await self._connect_locked(address, model_label)
            _LOGGER.debug(
                "Writing GATT char for %s characteristic=%s packet=%s",
                address,
                CHARACTERISTIC_UUID,
                _hex(packet),
            )
            try:
                await client.write_gatt_char(CHARACTERISTIC_UUID, packet, response=False)
            except Exception:
                await self._disconnect_locked()
                raise

    async def _disconnect_current(self) -> None:
        async with self._connection_lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        if self._client is None:
            self._client_address = None
            return

        try:
            if self._client.is_connected:
                _LOGGER.debug("Disconnecting BLE client for %s", self._client_address)
                try:
                    async with asyncio.timeout(BLE_DISCONNECT_TIMEOUT_SECONDS):
                        await self._client.disconnect()
                except TimeoutError:
                    _LOGGER.warning(
                        "Timed out disconnecting BLE client for %s after %.1fs",
                        self._client_address,
                        BLE_DISCONNECT_TIMEOUT_SECONDS,
                    )
                except Exception as err:
                    _LOGGER.debug(
                        "Ignoring disconnect error for %s: %s",
                        self._client_address,
                        err,
                    )
        finally:
            self._client = None
            self._client_address = None
