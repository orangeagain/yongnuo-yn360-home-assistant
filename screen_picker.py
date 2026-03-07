"""
Screen color picker -> YN RGB light sync tool (Windows only).

Picks the pixel color under the mouse cursor and sends it to a YN RGB light
in real-time via BLE. Uses DXGI Desktop Duplication (via dxcam) to capture
hardware-accelerated content (games, videos, etc.).

Architecture:
  Thread A (dxcam)  : captures screen frames in background
  Thread B (sampler): reads pixel under cursor at 60 FPS -> shared color tuple
  Main loop (BLE)   : sends latest color via BLE at ~100 FPS

Usage:
  python screen_picker.py ADDRESS
  python screen_picker.py DB:B9:85:86:42:60

Requirements: bleak, dxcam
  pip install bleak dxcam
"""

import asyncio
import ctypes
import ctypes.wintypes
import struct
import sys
import threading
import time

# NOTE: do NOT import dxcam at module level -- it initializes COM (STA)
# which conflicts with bleak's WinRT backend (needs MTA).

from bleak import BleakClient

CHAR_CMD = "f000aa61-0451-4000-b000-000000000000"
COLOR_SAMPLE_FPS = 60
# BLE send interval. 10ms = ~100fps. With response=False this is the pace at
# which we feed the OS BLE buffer. One command every 10ms keeps the buffer
# shallow (0-1 pending) so there's no stale-command backlog.
BLE_SEND_INTERVAL = 0.005  # 5ms = ~200fps

# Boost Windows timer resolution from default 15.6ms to 1ms,
# so asyncio.sleep(0.01) actually sleeps ~10ms, not ~15ms.
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

# DPI awareness for correct cursor coordinates
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def get_cursor_pos() -> tuple[int, int]:
    point = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


class ScreenSampler:
    """Screen capture + color sampling, fully runs in background threads.

    Thread A: dxcam captures screen frames (DXGI Desktop Duplication).
    Thread B: reads pixel under cursor at COLOR_SAMPLE_FPS, updates
              self.latest_rgb (a tuple, thread-safe via GIL).

    The main async loop only ever reads self.latest_rgb -- no numpy,
    no frame access, just a tuple read.
    """

    def __init__(self):
        self.latest_rgb: tuple[int, int, int] = (0, 0, 0)
        self.sample_count: int = 0
        self._frame = None
        self._running = True
        self._error = None
        ready = threading.Event()
        # Thread A: dxcam capture
        self._capture_thread = threading.Thread(
            target=self._capture_loop, args=(ready,), daemon=True
        )
        self._capture_thread.start()
        ready.wait()
        if self._error:
            raise self._error
        # Thread B: pixel color sampling
        self._sampler_thread = threading.Thread(
            target=self._sampler_loop, daemon=True
        )
        self._sampler_thread.start()

    def _capture_loop(self, ready: threading.Event):
        try:
            import dxcam
            camera = dxcam.create(output_color="RGB")
            camera.start(target_fps=COLOR_SAMPLE_FPS, video_mode=True)
            ready.set()
            while self._running:
                frame = camera.get_latest_frame()
                if frame is not None:
                    self._frame = frame
                time.sleep(0.001)
            camera.stop()
        except Exception as e:
            self._error = e
            ready.set()

    def _sampler_loop(self):
        interval = 1.0 / COLOR_SAMPLE_FPS
        while self._running:
            frame = self._frame
            if frame is not None:
                x, y = get_cursor_pos()
                h, w = frame.shape[:2]
                x = max(0, min(x, w - 1))
                y = max(0, min(y, h - 1))
                r, g, b = frame[y, x]
                self.latest_rgb = (int(r), int(g), int(b))
                self.sample_count += 1
            time.sleep(interval)

    def stop(self):
        self._running = False
        self._capture_thread.join(timeout=2)
        self._sampler_thread.join(timeout=2)


class BleConnection:
    """Auto-reconnecting BLE connection."""

    def __init__(self, address: str):
        self.address = address
        self._client: BleakClient | None = None

    async def connect(self):
        if self._client and self._client.is_connected:
            return
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._client = BleakClient(self.address, timeout=10.0)
        await self._client.connect()
        print(f"\n  BLE connected: {self.address}")

    async def write(self, data: bytes) -> bool:
        """Write with auto-reconnect. Returns True on success."""
        for attempt in range(3):
            try:
                if not self._client or not self._client.is_connected:
                    await self.connect()
                await self._client.write_gatt_char(CHAR_CMD, data, response=False)
                return True
            except Exception:
                self._client = None
                if attempt < 2:
                    await asyncio.sleep(0.3 * (attempt + 1))
        return False

    async def disconnect(self):
        if self._client:
            try:
                if self._client.is_connected:
                    await self._client.disconnect()
            except Exception:
                pass
            self._client = None


async def run(address: str):
    # Connect BLE first, before any dxcam/COM interaction
    ble = BleConnection(address)
    print(f"Connecting to {address} ...")
    await ble.connect()
    print()
    print("Screen Color Picker -> BLE Light")
    print("Move your mouse over any pixel on screen.")
    print("The light will follow the color in real-time.")
    print("Press Ctrl+C to stop.")
    print()

    # Start screen capture + color sampling in background threads
    screen = ScreenSampler()
    print(f"  Capture: {COLOR_SAMPLE_FPS} FPS")
    print(f"  BLE interval: {BLE_SEND_INTERVAL*1000:.0f}ms "
          f"(~{1/BLE_SEND_INTERVAL:.0f} fps max)")
    print()

    send_count = 0
    t_start = time.perf_counter()

    try:
        while True:
            r, g, b = screen.latest_rgb
            packet = struct.pack(">BBBBBB", 0xAE, 0xA1, r, g, b, 0x56)
            await ble.write(packet)
            send_count += 1

            if send_count % 30 == 0:
                elapsed = time.perf_counter() - t_start
                cap_fps = screen.sample_count / elapsed if elapsed > 0 else 0
                print(
                    f"\r  BLE: {send_count/elapsed:.0f} fps  "
                    f"Capture: {cap_fps:.0f} fps  "
                    f"Color: #{r:02X}{g:02X}{b:02X}  ",
                    end="",
                    flush=True,
                )

            # Pace writes so OS BLE buffer stays shallow (0-1 pending).
            # asyncio.sleep yields to event loop, keeping BLE callbacks alive.
            await asyncio.sleep(BLE_SEND_INTERVAL)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        screen.stop()
        elapsed = time.perf_counter() - t_start
        if elapsed > 0 and send_count > 0:
            print(
                f"\n\nStopped. {send_count} BLE writes in {elapsed:.1f}s "
                f"= {send_count/elapsed:.0f} fps"
            )
        try:
            await ble.write(bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]))
            print("Light off.")
        except Exception:
            pass
        await ble.disconnect()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    address = sys.argv[1]

    try:
        asyncio.run(run(address))
    except KeyboardInterrupt:
        print("\nBye.")


if __name__ == "__main__":
    main()
