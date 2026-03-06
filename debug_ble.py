"""
YN150 BLE debug tool - standalone, no Home Assistant needed.

Usage:
  python debug_ble.py scan              # Scan for nearby BLE devices
  python debug_ble.py services ADDRESS  # List all GATT services/characteristics
  python debug_ble.py sniff ADDRESS     # Subscribe to all notify/indicate characteristics
  python debug_ble.py write ADDRESS UUID HEX  # Write raw hex bytes to a characteristic
  python debug_ble.py probe ADDRESS     # Try command types A0-AF interactively
  python debug_ble.py rainbow ADDRESS [FPS,FPS,...]  # Visual FPS test: find the real frame rate limit
  python debug_ble.py parallel ADDR,MODE,FPS [ADDR,MODE,FPS ...]  # Multi-light parallel test

Examples:
  python debug_ble.py scan
  python debug_ble.py services AA:BB:CC:DD:EE:FF
  python debug_ble.py sniff AA:BB:CC:DD:EE:FF
  python debug_ble.py write AA:BB:CC:DD:EE:FF f000aa61-0451-4000-b000-000000000000 AEA1FF000056
  python debug_ble.py probe AA:BB:CC:DD:EE:FF
  python debug_ble.py rainbow DB:B9:85:86:42:60 200,300,400,500
  python debug_ble.py parallel DB:B9:85:86:42:60,rgb,300 D0:32:34:39:6D:6F,rgb,300 D0:32:34:39:74:49,ct,100
"""

import asyncio
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic


def hex_dump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


async def cmd_scan(duration: float = 10.0):
    print(f"Scanning for {duration}s ...")
    devices = await BleakScanner.discover(timeout=duration, return_adv=True)
    # devices is dict {address: (BLEDevice, AdvertisementData)}
    entries = [(dev, adv) for dev, adv in devices.values()]
    entries.sort(key=lambda e: e[1].rssi if e[1].rssi is not None else -999, reverse=True)
    print(f"\nFound {len(entries)} devices:\n")
    print(f"{'ADDRESS':<20} {'RSSI':>5}  NAME")
    print("-" * 60)
    for dev, adv in entries:
        name = dev.name or adv.local_name or "(unknown)"
        rssi = adv.rssi if adv.rssi is not None else "?"
        print(f"{dev.address:<20} {rssi:>5}  {name}")


async def cmd_services(address: str):
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}\n")
        for service in client.services:
            print(f"Service: {service.uuid}  [{service.description}]")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  Char: {char.uuid}  [{props}]  handle={char.handle}")
                for desc in char.descriptors:
                    print(f"    Desc: {desc.uuid}  handle={desc.handle}")
            print()


async def cmd_sniff(address: str):
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        notifiable = []
        for service in client.services:
            for char in service.characteristics:
                if "notify" in char.properties or "indicate" in char.properties:
                    notifiable.append(char)

        if not notifiable:
            print("No notify/indicate characteristics found.")
            return

        print(f"\nSubscribing to {len(notifiable)} characteristic(s):\n")
        for char in notifiable:
            print(f"  {char.uuid}  [{', '.join(char.properties)}]")

        def make_callback(char_uuid: str):
            def callback(_sender: BleakGATTCharacteristic, data: bytearray):
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                print(f"[{ts}] {char_uuid}  len={len(data):>3}  {hex_dump(data)}")
            return callback

        for char in notifiable:
            await client.start_notify(char.uuid, make_callback(char.uuid))

        print("\nListening ... use phone app to control the light. Ctrl+C to stop.\n")
        try:
            while True:
                await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            print("\nStopping ...")
            for char in notifiable:
                try:
                    await client.stop_notify(char.uuid)
                except Exception:
                    pass


async def cmd_write(address: str, uuid: str, hex_str: str):
    data = bytes.fromhex(hex_str)
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")
        print(f"Writing to {uuid}: {hex_dump(data)} ({len(data)} bytes)")
        await client.write_gatt_char(uuid, data, response=False)
        print("Done.")


CHAR_CMD = "f000aa61-0451-4000-b000-000000000000"
CHAR_NOTIFY = "f000aa63-0451-4000-b000-000000000000"


async def cmd_rainbow(address: str, fps_list: list[int] | None = None):
    """Frame-drop detection via buffer drain test.

    Sends RED/BLUE stripes at target FPS for a fixed duration, then sends
    a GREEN marker. If the light turns green immediately, no buffering and
    the light keeps up. If delayed, the drain time reveals actual rate.

    Formula: actual_rate = total_commands / (send_duration + drain_time)
    """
    import time

    SEND_DURATION = 3.0  # seconds of stripe sending per test
    STRIPE_FRAMES = 15   # frames per RED/BLUE block
    IMMEDIATE_THRESHOLD = 1.0  # below this = "immediate" (reaction time)

    target_fps_list = fps_list or [60, 100, 150, 200, 250, 300, 400, 500]

    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        print("\n--- Turning light ON ---")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
        await asyncio.sleep(0.5)

        def busy_wait_until(target: float):
            while time.perf_counter() < target:
                pass

        RED = bytes([0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56])
        BLUE = bytes([0xAE, 0xA1, 0x00, 0x00, 0xFF, 0x56])
        GREEN = bytes([0xAE, 0xA1, 0x00, 0xFF, 0x00, 0x56])

        print()
        print("=" * 64)
        print("  Buffer Drain Frame-Drop Test")
        print(f"  Each test: {SEND_DURATION:.0f}s RED/BLUE stripes -> GREEN marker")
        print()
        print("  Watch the light:")
        print("    1. RED/BLUE flicker for 3 seconds")
        print("    2. Then GREEN is sent")
        print("    3. Press Enter the MOMENT light turns green")
        print()
        print("  If green is immediate -> light keeps up at this FPS")
        print("  If red/blue continues after 'GREEN sent' -> buffered!")
        print("=" * 64)

        results = []

        for target_fps in target_fps_list:
            total = int(target_fps * SEND_DURATION)
            interval = 1.0 / target_fps

            print(f"\n{'_'*64}")
            print(f"  {target_fps} FPS  ({total} commands in {SEND_DURATION:.0f}s, "
                  f"interval={interval*1000:.1f}ms)")
            print(f"{'_'*64}")

            # Reset to white between tests so user sees clear start
            await client.write_gatt_char(
                CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
            input("  Press Enter to start...")

            # Send RED/BLUE stripes
            errors = 0
            t_start = time.perf_counter()
            next_send = t_start

            for i in range(total):
                block = (i // STRIPE_FRAMES) % 2
                packet = RED if block == 0 else BLUE
                try:
                    await client.write_gatt_char(CHAR_CMD, packet, response=False)
                except Exception:
                    errors += 1
                next_send += interval
                busy_wait_until(next_send)

            # Send GREEN marker
            await client.write_gatt_char(CHAR_CMD, GREEN, response=False)
            t_green_sent = time.perf_counter()
            actual_send_time = t_green_sent - t_start
            actual_fps = total / actual_send_time if actual_send_time > 0 else 0

            print(f"\n  >>> GREEN SENT  (sent {total} cmds in {actual_send_time:.2f}s"
                  f" = {actual_fps:.0f} fps)")
            print(f"  >>> Press Enter when light turns GREEN <<<")

            input()
            t_green_seen = time.perf_counter()
            drain = t_green_seen - t_green_sent

            if drain < IMMEDIATE_THRESHOLD:
                print(f"  -> {drain:.1f}s (immediate) -> light handles >= {target_fps} fps")
                results.append((target_fps, total, drain, None))
            else:
                adjusted_drain = max(0, drain - 0.3)  # subtract reaction time
                effective = total / (SEND_DURATION + adjusted_drain)
                print(f"  -> drain={drain:.1f}s -> effective ~{effective:.0f} fps")
                results.append((target_fps, total, drain, effective))

            if errors:
                print(f"     (write errors: {errors})")

        # Summary
        print(f"\n{'='*64}")
        print("  SUMMARY")
        print(f"{'='*64}")
        print(f"  {'FPS':>6}  {'Sent':>6}  {'Drain':>7}  Result")
        print(f"  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*30}")

        effective_rates = []
        for fps, total, drain, effective in results:
            if effective is None:
                print(f"  {fps:>5d}   {total:>5d}   {drain:>5.1f}s   OK (>= {fps} fps)")
            else:
                print(f"  {fps:>5d}   {total:>5d}   {drain:>5.1f}s   "
                      f"buffered -> ~{effective:.0f} fps")
                effective_rates.append(effective)

        if effective_rates:
            avg_rate = sum(effective_rates) / len(effective_rates)
            print(f"\n  Estimated light processing rate: ~{avg_rate:.0f} fps")
        else:
            top_fps = target_fps_list[-1]
            print(f"\n  No buffering detected! Light handles >= {top_fps} fps")

        print()
        await client.write_gatt_char(
            CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)
        print("  Light off. Done.")


async def cmd_parallel(lights_config: list[tuple[str, str, int]]):
    """Parallel multi-light test with interleaved heap scheduler.

    Connects to multiple lights simultaneously, sends commands interleaved
    by a priority queue (earliest-deadline-first), then uses buffer drain
    (OFF marker) to detect if any light fell behind.

    Each light entry: (address, mode, fps) where mode is 'rgb' or 'ct'.
    """
    import heapq
    import time

    SEND_DURATION = 3.0
    STRIPE_FRAMES = 15  # frames per color block

    def busy_wait_until(target: float):
        while time.perf_counter() < target:
            pass

    RED = bytes([0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56])
    BLUE = bytes([0xAE, 0xA1, 0x00, 0x00, 0xFF, 0x56])
    GREEN = bytes([0xAE, 0xA1, 0x00, 0xFF, 0x00, 0x56])
    CT_COOL = bytes([0xAE, 0xAA, 0x00, 0x63, 0x00, 0x56])  # cool white max
    CT_WARM = bytes([0xAE, 0xAA, 0x00, 0x00, 0x63, 0x56])  # warm white max
    OFF = bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56])

    def make_rgb_stripe(frame):
        return RED if (frame // STRIPE_FRAMES) % 2 == 0 else BLUE

    def make_ct_stripe(frame):
        return CT_COOL if (frame // STRIPE_FRAMES) % 2 == 0 else CT_WARM

    # ── Connect to all lights ──
    print(f"\nConnecting to {len(lights_config)} lights...")
    clients = []
    lights = []
    try:
        for addr, mode, fps in lights_config:
            label = f"{'RGB' if mode == 'rgb' else 'CT'} @ {fps}fps"
            print(f"  {addr}  ({label})...", end="", flush=True)
            client = BleakClient(addr, timeout=10.0)
            await client.connect()
            print(" OK")
            clients.append(client)

            total = int(fps * SEND_DURATION)
            lights.append({
                "client": client,
                "addr": addr,
                "mode": mode,
                "fps": fps,
                "total": total,
                "interval": 1.0 / fps,
                "make_packet": make_rgb_stripe if mode == "rgb" else make_ct_stripe,
            })

        # ── Turn all on ──
        print("\nTurning all lights ON...")
        for light in lights:
            await light["client"].write_gatt_char(
                CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
        await asyncio.sleep(1.0)

        # ── Print test plan ──
        total_cmds = sum(l["total"] for l in lights)
        combined_fps = sum(l["fps"] for l in lights)
        print()
        print("=" * 64)
        print("  Parallel Multi-Light Test")
        print(f"  Duration: {SEND_DURATION:.0f}s  |  Combined: {combined_fps} cmd/s")
        print("=" * 64)
        for i, light in enumerate(lights):
            print(f"  Light {i+1}: {light['addr']}")
            print(f"           {light['mode'].upper()} @ {light['fps']} fps"
                  f" ({light['total']} commands)")
        print(f"  Total: {total_cmds} commands")
        print()
        print("  After sending, OFF is sent to all lights.")
        print("  If a light keeps flickering after 'OFF sent' -> buffer backlog.")
        print("=" * 64)

        input("\n  Press Enter to start...")

        # ── Interleaved heap scheduler ──
        # Heap entries: (scheduled_time, light_index, frame_number)
        heap: list[tuple[float, int, int]] = []
        t_start = time.perf_counter()
        for i in range(len(lights)):
            heapq.heappush(heap, (t_start, i, 0))

        errors = [0] * len(lights)
        sent = [0] * len(lights)

        while heap:
            next_time, idx, frame = heapq.heappop(heap)
            light = lights[idx]

            if frame >= light["total"]:
                continue

            busy_wait_until(next_time)

            packet = light["make_packet"](frame)
            try:
                await light["client"].write_gatt_char(CHAR_CMD, packet, response=False)
                sent[idx] += 1
            except Exception as e:
                errors[idx] += 1
                if errors[idx] <= 3:
                    print(f"\n    error light {idx+1}: {e}")

            # Schedule next frame for this light
            heapq.heappush(heap, (next_time + light["interval"], idx, frame + 1))

        t_send_done = time.perf_counter()
        send_elapsed = t_send_done - t_start

        # ── Send OFF to all ──
        for light in lights:
            await light["client"].write_gatt_char(CHAR_CMD, OFF, response=False)
        t_off_sent = time.perf_counter()

        # ── Report sending stats ──
        actual_total = sum(sent)
        print(f"\n{'─'*64}")
        print(f"  Sending complete: {actual_total} commands in {send_elapsed:.2f}s"
              f" = {actual_total/send_elapsed:.0f} cmd/s")
        if send_elapsed > SEND_DURATION * 1.1:
            print(f"  WARNING: took {send_elapsed:.1f}s instead of {SEND_DURATION:.0f}s"
                  f" - sender couldn't keep up!")
        print()

        for i, light in enumerate(lights):
            afps = sent[i] / send_elapsed if send_elapsed > 0 else 0
            status = "OK" if sent[i] == light["total"] else "BEHIND"
            print(f"  Light {i+1} ({light['addr']}):"
                  f"  {sent[i]}/{light['total']} sent"
                  f"  {afps:.0f}/{light['fps']} fps"
                  f"  errors={errors[i]}  [{status}]")

        # ── Buffer drain observation ──
        print(f"\n{'─'*64}")
        print(f"  >>> OFF sent to all lights <<<")
        print(f"  >>> Press Enter when LAST light turns off <<<")
        input()
        t_off_seen = time.perf_counter()
        drain = t_off_seen - t_off_sent

        print(f"\n{'='*64}")
        print("  RESULT")
        print(f"{'='*64}")
        if drain < 1.0:
            print(f"  Drain: {drain:.1f}s (immediate)")
            print(f"  -> All {len(lights)} lights handle {combined_fps} cmd/s combined!")
        else:
            adj_drain = max(0, drain - 0.3)
            effective = actual_total / (send_elapsed + adj_drain)
            print(f"  Drain: {drain:.1f}s (adjusted ~{adj_drain:.1f}s)")
            print(f"  -> Combined effective: ~{effective:.0f} cmd/s"
                  f" (target {combined_fps})")
            if effective < combined_fps * 0.9:
                print(f"  -> Bottleneck: BLE radio bandwidth shared across"
                      f" {len(lights)} connections")

    finally:
        print("\n  Disconnecting...")
        for client in clients:
            try:
                if client.is_connected:
                    await client.write_gatt_char(CHAR_CMD, OFF, response=False)
                    await client.disconnect()
            except Exception:
                pass
        print("  Done.")


async def cmd_probe(address: str):
    """Try each command type AE Ax with sample payloads, wait for user feedback."""
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        # Subscribe to notify to see any responses
        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  <- notify: {hex_dump(data)}")

        try:
            await client.start_notify(CHAR_NOTIFY, on_notify)
            print(f"Subscribed to notify on {CHAR_NOTIFY}")
        except Exception:
            print("(notify subscription failed, continuing without it)")

        print()
        print("Known: A1=RGB color, A3=turn off")
        print("Will test other command types. Watch the light!")
        print("Press Enter to send next, 'q' to quit, or type hex payload to try custom.\n")

        # Test matrix: command types to try, with sample payloads
        tests = [
            (0xA0, [0x80, 0x00, 0x00], "A0 with 128,0,0"),
            (0xA0, [0x00, 0x80, 0x00], "A0 with 0,128,0"),
            (0xA0, [0x00, 0x00, 0x80], "A0 with 0,0,128"),
            (0xA2, [0xFF, 0x00, 0x00], "A2 with 255,0,0 (maybe warm white?)"),
            (0xA2, [0x00, 0xFF, 0x00], "A2 with 0,255,0"),
            (0xA2, [0x00, 0x00, 0xFF], "A2 with 0,0,255"),
            (0xA2, [0x80, 0x80, 0x00], "A2 with 128,128,0"),
            (0xA2, [0xFF, 0xFF, 0x00], "A2 with 255,255,0"),
            (0xA2, [0x00, 0xFF, 0xFF], "A2 with 0,255,255"),
            (0xA4, [0xFF, 0x00, 0x00], "A4 with 255,0,0"),
            (0xA4, [0x00, 0xFF, 0x00], "A4 with 0,255,0"),
            (0xA4, [0x00, 0x00, 0xFF], "A4 with 0,0,255"),
            (0xA5, [0xFF, 0x00, 0x00], "A5 with 255,0,0"),
            (0xA5, [0x00, 0xFF, 0x00], "A5 with 0,255,0"),
            (0xA6, [0xFF, 0x00, 0x00], "A6 with 255,0,0"),
            (0xA7, [0xFF, 0x00, 0x00], "A7 with 255,0,0"),
            (0xA8, [0xFF, 0x00, 0x00], "A8 with 255,0,0"),
            (0xA9, [0xFF, 0x00, 0x00], "A9 with 255,0,0"),
            (0xAA, [0xFF, 0x00, 0x00], "AA with 255,0,0"),
            (0xAB, [0xFF, 0x00, 0x00], "AB with 255,0,0"),
            (0xAC, [0xFF, 0x00, 0x00], "AC with 255,0,0"),
            (0xAD, [0xFF, 0x00, 0x00], "AD with 255,0,0"),
            (0xAE, [0xFF, 0x00, 0x00], "AE with 255,0,0"),
            (0xAF, [0xFF, 0x00, 0x00], "AF with 255,0,0"),
        ]

        for cmd, payload, desc in tests:
            packet = bytes([0xAE, cmd] + payload + [0x56])
            user_input = input(f"[{desc}] Send {hex_dump(packet)} ? (Enter/q/hex): ").strip()
            if user_input.lower() == "q":
                break
            if user_input:
                # User typed custom hex
                try:
                    packet = bytes.fromhex(user_input)
                except ValueError:
                    print("  Invalid hex, skipping.")
                    continue

            print(f"  -> sending: {hex_dump(packet)}")
            await client.write_gatt_char(CHAR_CMD, packet, response=False)
            await asyncio.sleep(0.5)  # wait for notify response + visual check

        print("\nDone probing. Turning off light ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "scan":
        asyncio.run(cmd_scan())
    elif command == "services":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py services ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_services(sys.argv[2]))
    elif command == "sniff":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py sniff ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_sniff(sys.argv[2]))
    elif command == "write":
        if len(sys.argv) < 5:
            print("Usage: debug_ble.py write ADDRESS UUID HEX")
            sys.exit(1)
        asyncio.run(cmd_write(sys.argv[2], sys.argv[3], sys.argv[4]))
    elif command == "probe":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py probe ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_probe(sys.argv[2]))
    elif command == "rainbow":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py rainbow ADDRESS [FPS,FPS,...]")
            print("  e.g.: debug_ble.py rainbow AA:BB:CC:DD:EE:FF 100,150,200,250,300")
            sys.exit(1)
        fps_list = None
        if len(sys.argv) >= 4:
            fps_list = [int(x) for x in sys.argv[3].split(",")]
        asyncio.run(cmd_rainbow(sys.argv[2], fps_list))
    elif command == "parallel":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py parallel ADDR,MODE,FPS [ADDR,MODE,FPS ...]")
            print("  MODE: rgb or ct")
            print("  e.g.: debug_ble.py parallel DB:B9:85:86:42:60,rgb,300"
                  " D0:32:34:39:74:49,ct,100")
            sys.exit(1)
        import re
        ble_addr_re = re.compile(r'^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$')
        lights_config = []
        for arg in sys.argv[2:]:
            # Last two comma-separated fields are mode and fps
            parts = arg.rsplit(",", 2)
            if len(parts) != 3:
                print(f"Invalid format: {arg}")
                print("Expected: ADDRESS,MODE,FPS  (e.g. DB:B9:85:86:42:60,rgb,300)")
                sys.exit(1)
            addr, mode, fps_str = parts
            if not ble_addr_re.match(addr):
                print(f"Invalid BLE address: '{addr}'")
                print(f"  (from argument: {arg})")
                print(f"  Check that each argument is separated by a space.")
                sys.exit(1)
            mode = mode.lower()
            if mode not in ("rgb", "ct"):
                print(f"Unknown mode '{mode}'. Use 'rgb' or 'ct'.")
                sys.exit(1)
            lights_config.append((addr, mode, int(fps_str)))
        asyncio.run(cmd_parallel(lights_config))
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
