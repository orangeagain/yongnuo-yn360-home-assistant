"""
YN150 BLE debug tool - standalone, no Home Assistant needed.

Usage:
  python debug_ble.py scan              # Scan for nearby BLE devices
  python debug_ble.py services ADDRESS  # List all GATT services/characteristics
  python debug_ble.py sniff ADDRESS     # Subscribe to all notify/indicate characteristics
  python debug_ble.py write ADDRESS UUID HEX  # Write raw hex bytes to a characteristic
  python debug_ble.py wy-ct ADDRESS [CHANNEL]  # Fixed CT sequence: OFF -> WAKE -> COOL -> WARM (default CH=0A)
  python debug_ble.py probe ADDRESS     # Try command types A0-AF interactively
  python debug_ble.py rainbow ADDRESS [FPS,FPS,...]  # Visual FPS test: find the real frame rate limit
  python debug_ble.py parallel ADDR,MODE,FPS [ADDR,MODE,FPS ...]  # Multi-light parallel test
  python debug_ble.py sync [ADDR1 ADDR2 ...]  # Sync test: connect all lights, show CW/WW/RGB together

Examples:
  python debug_ble.py scan
  python debug_ble.py services AA:BB:CC:DD:EE:FF
  python debug_ble.py sniff AA:BB:CC:DD:EE:FF
  python debug_ble.py write AA:BB:CC:DD:EE:FF f000aa61-0451-4000-b000-000000000000 AEA1FF000056
  python debug_ble.py wy-ct D0:32:34:39:74:49
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


async def cmd_wy_ct(address: str, channel: int = 0x0A):
    """Run a fixed color-temperature sequence in one connection."""
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=20.0) as client:
        print(f"Connected: {client.is_connected}")

        async def send(label: str, packet: bytes, hold: float) -> None:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{ts}] -> {label}: {hex_dump(packet)}")
            await client.write_gatt_char(CHAR_CMD, packet, response=False)
            await asyncio.sleep(hold)

        await send("OFF", bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), 1.5)
        await send("WAKE", bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), 2.0)
        await send(
            f"COOL ch=0x{channel:02X}",
            bytes([0xAE, 0xAA, channel, 0x63, 0x00, 0x56]),
            5.0,
        )
        await send(
            f"WARM ch=0x{channel:02X}",
            bytes([0xAE, 0xAA, channel, 0x00, 0x63, 0x56]),
            0.5,
        )
        print(f"Sequence complete. Light left ON at WARM ch=0x{channel:02X}.")


CHAR_CMD = "f000aa61-0451-4000-b000-000000000000"
CHAR_NOTIFY = "f000aa63-0451-4000-b000-000000000000"
GAP_DEVICE_NAME = "00002a00-0000-1000-8000-00805f9b34fb"


def detect_model_from_name(name: str | None) -> tuple[str, int, bool]:
    """Returns (model_name, ct_channel, has_rgb)."""
    if not name:
        return ("Unknown (YN360?)", 0x01, True)

    normalized = "".join(ch for ch in name.upper() if ch.isalnum())
    if "150" in normalized and "WY" in normalized:
        return ("YN150WY", 0x0A, False)
    if "150" in normalized:
        return ("YN150Ultra RGB", 0x00, True)
    return ("YN360", 0x01, True)


async def read_device_name(client: BleakClient) -> str | None:
    try:
        name_bytes = await client.read_gatt_char(GAP_DEVICE_NAME)
    except Exception:
        return None
    return name_bytes.decode("utf-8", errors="replace")


async def cmd_rainbow(address: str, fps_list: list[int] | None = None):
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  <- notify: {hex_dump(data)}")

        try:
            await client.start_notify(CHAR_NOTIFY, on_notify)
            print(f"Subscribed to notify on {CHAR_NOTIFY}")
        except Exception:
            print("(notify subscription failed, continuing without it)")

        print()
        print("Color-temperature probe for YN150WY")
        print("Watch the light! Press Enter to send next, 'q' to quit, 's' to skip group.\n")

        # Group 1: Try each command byte with a single brightness-like payload
        # Hypothesis: AE Ax BRIGHTNESS 00 00 56 (one channel white)
        tests = []

        # Try command bytes A2, A4-AF with single-value payloads (brightness)
        for cmd in [0xA2, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF]:
            tests.append((cmd, [0xFF, 0x00, 0x00], f"cmd {cmd:02X}: brightness=FF (max)"))

        # Group 2: Two-channel warm+cool (on promising cmd bytes, filled in after group 1)
        # Hypothesis: AE Ax WARM COOL 00 56
        for cmd in [0xA2, 0xA4, 0xA5, 0xA6]:
            tests.append((cmd, [0xFF, 0x00, 0x00], f"cmd {cmd:02X}: warm=FF cool=00"))
            tests.append((cmd, [0x00, 0xFF, 0x00], f"cmd {cmd:02X}: warm=00 cool=FF"))
            tests.append((cmd, [0x80, 0x80, 0x00], f"cmd {cmd:02X}: warm=80 cool=80"))
            tests.append((cmd, [0xFF, 0xFF, 0x00], f"cmd {cmd:02X}: warm=FF cool=FF"))

        # Group 3: Color temp as single value + brightness
        # Hypothesis: AE Ax CT_HIGH CT_LOW BRIGHTNESS 56
        # or: AE Ax BRIGHTNESS CT 00 56
        for cmd in [0xA2, 0xA4, 0xA5, 0xA6]:
            tests.append((cmd, [0xFF, 0x20, 0x00], f"cmd {cmd:02X}: val1=FF val2=20 (CT+bright?)"))
            tests.append((cmd, [0xFF, 0x40, 0x00], f"cmd {cmd:02X}: val1=FF val2=40"))
            tests.append((cmd, [0xFF, 0x60, 0x00], f"cmd {cmd:02X}: val1=FF val2=60"))
            tests.append((cmd, [0xFF, 0x80, 0x00], f"cmd {cmd:02X}: val1=FF val2=80"))
            tests.append((cmd, [0xFF, 0xA0, 0x00], f"cmd {cmd:02X}: val1=FF val2=A0"))
            tests.append((cmd, [0xFF, 0xFF, 0x00], f"cmd {cmd:02X}: val1=FF val2=FF"))

        # Group 4: Try different packet lengths (4, 5, 7 bytes)
        for cmd in [0xA2, 0xA4]:
            tests.append(("raw", [0xAE, cmd, 0xFF, 0x56], f"4-byte: AE {cmd:02X} FF 56"))
            tests.append(("raw", [0xAE, cmd, 0xFF, 0x80, 0x56], f"5-byte: AE {cmd:02X} FF 80 56"))
            tests.append(("raw", [0xAE, cmd, 0xFF, 0x80, 0x00, 0x00, 0x56], f"7-byte: AE {cmd:02X} FF 80 00 00 56"))

        for entry in tests:
            if entry[0] == "raw":
                packet = bytes(entry[1])
                desc = entry[2]
            else:
                cmd, payload, desc = entry
                packet = bytes([0xAE, cmd] + payload + [0x56])

            user_input = input(f"[{desc}] Send {hex_dump(packet)} ? (Enter/q/s): ").strip()
            if user_input.lower() == "q":
                break
            if user_input.lower() == "s":
                continue

            print(f"  -> sending: {hex_dump(packet)}")
            await client.write_gatt_char(CHAR_CMD, packet, response=False)
            await asyncio.sleep(1.0)  # longer wait for visual check

        print("\nDone. Turning off light ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)


async def cmd_test_wy(address: str):
    """Slow, careful test for YN150WY color temperature via A1 command."""
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  <- notify: {hex_dump(data)}")

        try:
            await client.start_notify(CHAR_NOTIFY, on_notify)
        except Exception:
            pass

        tests = [
            # Group A: Does A1 RGB mapping affect WY color temp?
            ("A1: R=FF G=00 B=00  (red only)",      [0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56]),
            ("A1: R=00 G=FF B=00  (green only)",     [0xAE, 0xA1, 0x00, 0xFF, 0x00, 0x56]),
            ("A1: R=00 G=00 B=FF  (blue only)",      [0xAE, 0xA1, 0x00, 0x00, 0xFF, 0x56]),
            ("A1: R=FF G=FF B=FF  (all max)",        [0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]),
            ("A1: R=20 G=00 B=00  (dim red)",        [0xAE, 0xA1, 0x20, 0x00, 0x00, 0x56]),
            ("A1: R=80 G=00 B=00  (mid red)",        [0xAE, 0xA1, 0x80, 0x00, 0x00, 0x56]),
            ("A1: R=00 G=20 B=00  (dim green)",      [0xAE, 0xA1, 0x00, 0x20, 0x00, 0x56]),
            ("A1: R=00 G=80 B=00  (mid green)",      [0xAE, 0xA1, 0x00, 0x80, 0x00, 0x56]),

            # Group B: A1 with range 0-99 (like YN360 white mode)
            ("A1: R=63 G=00 B=00  (99 warm?)",       [0xAE, 0xA1, 0x63, 0x00, 0x00, 0x56]),
            ("A1: R=00 G=63 B=00  (99 cool?)",       [0xAE, 0xA1, 0x00, 0x63, 0x00, 0x56]),
            ("A1: R=63 G=63 B=00  (both 99)",        [0xAE, 0xA1, 0x63, 0x63, 0x00, 0x56]),

            # Group C: AA with light ON (retry)
            ("AA: ch=0A cool=63 warm=00",            [0xAE, 0xAA, 0x0A, 0x63, 0x00, 0x56]),
            ("AA: ch=0A cool=00 warm=63",            [0xAE, 0xAA, 0x0A, 0x00, 0x63, 0x56]),
            ("AA: ch=00 cool=63 warm=00",            [0xAE, 0xAA, 0x00, 0x63, 0x00, 0x56]),
            ("AA: ch=01 cool=63 warm=00",            [0xAE, 0xAA, 0x01, 0x63, 0x00, 0x56]),

            # Group D: Try fff3 for color temp
            ("fff3: AA ch=0A cool=63 warm=00",       "fff3"),
            ("fff3: A1 R=63 G=00 B=00",             "fff3-a1"),

            # Group E: Query state - write then read
            ("Read state from fff4",                 "read-fff4"),
        ]

        CHAR_FFF3 = "0000fff3-0000-1000-8000-00805f9b34fb"
        CHAR_FFF4 = "0000fff4-0000-1000-8000-00805f9b34fb"

        print("\nSlow test - look CAREFULLY at the light color temperature and brightness.")
        print("After each command, describe what you see: warmer? cooler? brighter? dimmer? same?\n")

        for desc, packet in tests:
            user_input = input(f"[{desc}] Press Enter to send, 's' skip, 'q' quit: ").strip()
            if user_input.lower() == "q":
                break
            if user_input.lower() == "s":
                continue

            if packet == "fff3":
                p = bytes([0xAE, 0xAA, 0x0A, 0x63, 0x00, 0x56])
                print(f"  -> fff3: {hex_dump(p)}")
                await client.write_gatt_char(CHAR_FFF3, p, response=False)
            elif packet == "fff3-a1":
                p = bytes([0xAE, 0xA1, 0x63, 0x00, 0x00, 0x56])
                print(f"  -> fff3: {hex_dump(p)}")
                await client.write_gatt_char(CHAR_FFF3, p, response=False)
            elif packet == "read-fff4":
                try:
                    data = await client.read_gatt_char(CHAR_FFF4)
                    print(f"  <- fff4: {hex_dump(data)}")
                except Exception as e:
                    print(f"  <- fff4 read failed: {e}")
            else:
                print(f"  -> aa61: {hex_dump(bytes(packet))}")
                await client.write_gatt_char(CHAR_CMD, bytes(packet), response=False)

            await asyncio.sleep(2.0)

        print("\nTurning off ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)


async def cmd_scan_cmds(address: str):
    """Brute-force scan all 256 command bytes. Light must be ON to see changes."""
    CHAR_FFF3 = "0000fff3-0000-1000-8000-00805f9b34fb"
    CHAR_FFF4 = "0000fff4-0000-1000-8000-00805f9b34fb"
    CHAR_FFF5 = "0000fff5-0000-1000-8000-00805f9b34fb"

    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  <- notify {_sender.uuid}: {hex_dump(data)}")

        for cuuid in [CHAR_NOTIFY, CHAR_FFF4]:
            try:
                await client.start_notify(cuuid, on_notify)
                print(f"Subscribed to {cuuid}")
            except Exception:
                pass

        # Read fff5
        try:
            data = await client.read_gatt_char(CHAR_FFF5)
            print(f"Read fff5: {hex_dump(data)}")
        except Exception as e:
            print(f"Read fff5 failed: {e}")

        # Turn light on
        print("\n--- Turning light ON ---")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56]), response=False)
        await asyncio.sleep(1.0)

        # Phase 1: Scan all cmd bytes on aa61 with payload 01 63 00
        print("\n=== Phase 1: All cmd bytes 0x00-0xFF on aa61, payload 01 63 00 ===")
        print("Watch the light! Press Ctrl+C when you see a change.\n")
        try:
            for cmd in range(256):
                if cmd in (0xA1, 0xA3):  # skip known on/off
                    continue
                packet = bytes([0xAE, cmd, 0x01, 0x63, 0x00, 0x56])
                print(f"  cmd=0x{cmd:02X}  {hex_dump(packet)}", end="\r")
                await client.write_gatt_char(CHAR_CMD, packet, response=False)
                await asyncio.sleep(0.15)
        except KeyboardInterrupt:
            print(f"\n\n*** Stopped at cmd=0x{cmd:02X} ***")
            input("Press Enter to continue to Phase 2...")

        # Re-turn on
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56]), response=False)
        await asyncio.sleep(1.0)

        # Phase 2: Scan all cmd bytes on aa61 with payload 63 63 00
        print("\n=== Phase 2: All cmd bytes 0x00-0xFF on aa61, payload 63 63 00 ===")
        try:
            for cmd in range(256):
                if cmd in (0xA1, 0xA3):
                    continue
                packet = bytes([0xAE, cmd, 0x63, 0x63, 0x00, 0x56])
                print(f"  cmd=0x{cmd:02X}  {hex_dump(packet)}", end="\r")
                await client.write_gatt_char(CHAR_CMD, packet, response=False)
                await asyncio.sleep(0.15)
        except KeyboardInterrupt:
            print(f"\n\n*** Stopped at cmd=0x{cmd:02X} ***")
            input("Press Enter to continue to Phase 3...")

        # Re-turn on
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56]), response=False)
        await asyncio.sleep(1.0)

        # Phase 3: Same scan on fff3
        print("\n=== Phase 3: All cmd bytes 0x00-0xFF on fff3, payload 01 63 00 ===")
        try:
            for cmd in range(256):
                packet = bytes([0xAE, cmd, 0x01, 0x63, 0x00, 0x56])
                print(f"  cmd=0x{cmd:02X}  {hex_dump(packet)}", end="\r")
                await client.write_gatt_char(CHAR_FFF3, packet, response=False)
                await asyncio.sleep(0.15)
        except KeyboardInterrupt:
            print(f"\n\n*** Stopped at cmd=0x{cmd:02X} ***")
            input("Press Enter to continue to Phase 4...")

        # Phase 4: Non-AE headers on aa61
        print("\n=== Phase 4: Different headers on aa61 ===")
        headers = [0x00, 0x01, 0xAA, 0xAB, 0xAF, 0xBE, 0xCA, 0xEE, 0xFF]
        try:
            for hdr in headers:
                for cmd in [0xA1, 0xA2, 0xAA, 0x01, 0x02]:
                    packet = bytes([hdr, cmd, 0x01, 0x63, 0x00, 0x56])
                    print(f"  hdr=0x{hdr:02X} cmd=0x{cmd:02X}  {hex_dump(packet)}", end="\r")
                    await client.write_gatt_char(CHAR_CMD, packet, response=False)
                    await asyncio.sleep(0.15)
        except KeyboardInterrupt:
            print(f"\n\n*** Stopped at hdr=0x{hdr:02X} cmd=0x{cmd:02X} ***")

        print("\n\nDone scanning. Turning off ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)


async def cmd_probe_ct2(address: str):
    """Probe color-temp with light already ON. Also tries fff3 characteristic."""
    CHAR_FFF3 = "0000fff3-0000-1000-8000-00805f9b34fb"

    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  <- notify: {hex_dump(data)}")

        for cuuid in [CHAR_NOTIFY, "0000fff4-0000-1000-8000-00805f9b34fb"]:
            try:
                await client.start_notify(cuuid, on_notify)
                print(f"Subscribed to notify on {cuuid}")
            except Exception:
                pass

        # Step 1: Turn on the light first
        print("\n--- Step 1: Turning light ON with AE A1 FF 00 00 56 ---")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56]), response=False)
        await asyncio.sleep(1.0)
        input("Light should be on. Press Enter to start probing...")

        tests = []

        # Group A: Try A1 with different "RGB" values - maybe R=warm, G=cool on WY
        tests.append(("aa61", "A1: R=FF G=00 B=00 (warm only?)",
                       [0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56]))
        tests.append(("aa61", "A1: R=00 G=FF B=00 (cool only?)",
                       [0xAE, 0xA1, 0x00, 0xFF, 0x00, 0x56]))
        tests.append(("aa61", "A1: R=00 G=00 B=FF (blue channel?)",
                       [0xAE, 0xA1, 0x00, 0x00, 0xFF, 0x56]))
        tests.append(("aa61", "A1: R=FF G=FF B=00 (warm+cool?)",
                       [0xAE, 0xA1, 0xFF, 0xFF, 0x00, 0x56]))
        tests.append(("aa61", "A1: R=80 G=80 B=00 (half warm+cool?)",
                       [0xAE, 0xA1, 0x80, 0x80, 0x00, 0x56]))
        tests.append(("aa61", "A1: R=FF G=FF B=FF (all max?)",
                       [0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]))

        # Group B: Other cmd bytes on aa61 (light is now ON)
        for cmd in [0xA0, 0xA2, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9]:
            tests.append(("aa61", f"cmd {cmd:02X}: FF 00 00 (on aa61, light ON)",
                           [0xAE, cmd, 0xFF, 0x00, 0x00, 0x56]))

        # Group C: Same commands on fff3 characteristic
        tests.append(("fff3", "A1 FF 00 00 on fff3",
                       [0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56]))
        tests.append(("fff3", "A1 00 FF 00 on fff3",
                       [0xAE, 0xA1, 0x00, 0xFF, 0x00, 0x56]))
        for cmd in [0xA0, 0xA2, 0xA4, 0xA5, 0xA6]:
            tests.append(("fff3", f"cmd {cmd:02X}: FF 00 00 on fff3",
                           [0xAE, cmd, 0xFF, 0x00, 0x00, 0x56]))
            tests.append(("fff3", f"cmd {cmd:02X}: 00 FF 00 on fff3",
                           [0xAE, cmd, 0x00, 0xFF, 0x00, 0x56]))

        # Group D: Non-AE headers on both characteristics
        for char_label, char_uuid in [("aa61", CHAR_CMD), ("fff3", CHAR_FFF3)]:
            tests.append((char_label, f"header 00: 00 A1 FF 00 00 56 on {char_label}",
                           [0x00, 0xA1, 0xFF, 0x00, 0x00, 0x56]))
            tests.append((char_label, f"header FF: FF A1 FF 00 00 56 on {char_label}",
                           [0xFF, 0xA1, 0xFF, 0x00, 0x00, 0x56]))
            # Plain bytes without framing
            tests.append((char_label, f"raw 2-byte FF 00 on {char_label}",
                           [0xFF, 0x00]))
            tests.append((char_label, f"raw 2-byte 00 FF on {char_label}",
                           [0x00, 0xFF]))
            tests.append((char_label, f"raw 4-byte FF 80 00 00 on {char_label}",
                           [0xFF, 0x80, 0x00, 0x00]))

        for char_label, desc, packet_list in tests:
            char_uuid = CHAR_CMD if char_label == "aa61" else CHAR_FFF3
            packet = bytes(packet_list)
            user_input = input(f"[{desc}] Send {hex_dump(packet)} ? (Enter/q/s): ").strip()
            if user_input.lower() == "q":
                break
            if user_input.lower() == "s":
                continue

            print(f"  -> {char_label}: {hex_dump(packet)}")
            await client.write_gatt_char(char_uuid, packet, response=False)
            await asyncio.sleep(1.0)

        print("\nDone. Turning off ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)


async def cmd_auto_rgb(address: str, delay: float = 1.0):
    """Automatic RGB test - cycles through colors and brightness levels."""
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  <- notify: {hex_dump(data)}")

        try:
            await client.start_notify(CHAR_NOTIFY, on_notify)
        except Exception:
            pass

        async def send(r, g, b, desc=""):
            packet = bytes([0xAE, 0xA1, r, g, b, 0x56])
            tag = f"  [{desc}]" if desc else ""
            print(f"  RGB({r:3d},{g:3d},{b:3d})  {hex_dump(packet)}{tag}")
            await client.write_gatt_char(CHAR_CMD, packet, response=False)
            await asyncio.sleep(delay)

        steps = list(range(0, 256, 17)) + [255]  # 0,17,34,...,255

        # --- Test 1: Red ramp ---
        print("\n=== Test 1/5: Red ramp (green=0, blue=0) ===")
        for v in steps:
            await send(v, 0, 0, f"red={v}")

        # --- Test 2: Green ramp ---
        print("\n=== Test 2/5: Green ramp (red=0, blue=0) ===")
        for v in steps:
            await send(0, v, 0, f"green={v}")

        # --- Test 3: Blue ramp ---
        print("\n=== Test 3/5: Blue ramp (red=0, green=0) ===")
        for v in steps:
            await send(0, 0, v, f"blue={v}")

        # --- Test 4: Hue rotation at max brightness ---
        print("\n=== Test 4/5: Hue rotation (12 steps) ===")
        hue_steps = [
            (255, 0, 0, "red"),
            (255, 128, 0, "orange"),
            (255, 255, 0, "yellow"),
            (128, 255, 0, "chartreuse"),
            (0, 255, 0, "green"),
            (0, 255, 128, "spring"),
            (0, 255, 255, "cyan"),
            (0, 128, 255, "azure"),
            (0, 0, 255, "blue"),
            (128, 0, 255, "violet"),
            (255, 0, 255, "magenta"),
            (255, 0, 128, "rose"),
        ]
        for r, g, b, name in hue_steps:
            await send(r, g, b, name)

        # --- Test 5: White brightness ramp ---
        print("\n=== Test 5/5: White brightness ramp (R=G=B) ===")
        for v in steps:
            await send(v, v, v, f"white={v}")

        print("\nDone. Turning off ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)
        print("RGB auto-test complete.")


async def cmd_probe_ct_ch(address: str):
    """Probe all 256 channel bytes for AE AA command to find color temp channel."""
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  <- notify: {hex_dump(data)}")

        try:
            await client.start_notify(CHAR_NOTIFY, on_notify)
        except Exception:
            pass

        # Turn on the light first
        print("\n--- Turning light ON (AE A1 FF FF FF 56) ---")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
        await asyncio.sleep(1.5)

        # Phase 1: scan all channel bytes with CW=99 WW=0 (should go cool white)
        print("\n=== Phase 1: AE AA [ch] 63 00 56 (cool white max) ===")
        print("Watch for the light to change to COOL white. Ctrl+C when you see a change.\n")
        sent_log = []
        try:
            for ch in range(256):
                packet = bytes([0xAE, 0xAA, ch, 0x63, 0x00, 0x56])
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                line = f"  [{ts}] ch=0x{ch:02X}  {hex_dump(packet)}"
                print(line)
                sent_log.append(line)
                await client.write_gatt_char(CHAR_CMD, packet, response=False)
                await asyncio.sleep(0.3)
        except KeyboardInterrupt:
            print(f"\n*** Stopped at ch=0x{ch:02X} ***")
            print(f"--- Last 5 commands before Ctrl+C ---")
            for l in sent_log[-5:]:
                print(l)
            input("\nPress Enter to continue to Phase 2...")

        # Reset: turn on again
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
        await asyncio.sleep(1.5)

        # Phase 2: scan all channel bytes with CW=0 WW=99 (should go warm white)
        print("\n=== Phase 2: AE AA [ch] 00 63 56 (warm white max) ===")
        print("Watch for the light to change to WARM white. Ctrl+C when you see a change.\n")
        sent_log = []
        try:
            for ch in range(256):
                packet = bytes([0xAE, 0xAA, ch, 0x00, 0x63, 0x56])
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                line = f"  [{ts}] ch=0x{ch:02X}  {hex_dump(packet)}"
                print(line)
                sent_log.append(line)
                await client.write_gatt_char(CHAR_CMD, packet, response=False)
                await asyncio.sleep(0.3)
        except KeyboardInterrupt:
            print(f"\n*** Stopped at ch=0x{ch:02X} ***")
            print(f"--- Last 5 commands before Ctrl+C ---")
            for l in sent_log[-5:]:
                print(l)
            input("\nPress Enter to continue to Phase 3...")

        # Reset
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
        await asyncio.sleep(1.5)

        # Phase 3: try different command bytes (not just AA) with channel=0x02
        print("\n=== Phase 3: AE [cmd] 02 63 00 56 (try other cmd bytes, ch=0x02) ===")
        print("Ctrl+C when you see a change.\n")
        sent_log = []
        try:
            for cmd in range(256):
                if cmd in (0xA1, 0xA3):
                    continue
                packet = bytes([0xAE, cmd, 0x02, 0x63, 0x00, 0x56])
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                line = f"  [{ts}] cmd=0x{cmd:02X}  {hex_dump(packet)}"
                print(line)
                sent_log.append(line)
                await client.write_gatt_char(CHAR_CMD, packet, response=False)
                await asyncio.sleep(0.3)
        except KeyboardInterrupt:
            print(f"\n*** Stopped at cmd=0x{cmd:02X} ***")
            print(f"--- Last 5 commands before Ctrl+C ---")
            for l in sent_log[-5:]:
                print(l)

        print("\nDone. Turning off ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)


async def cmd_auto_ct(address: str, delay: float = 1.5):
    """Automatic color temperature test - cycles CW/WW on YN150WY(0x0A) and YN360(0x01)."""
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  <- notify: {hex_dump(data)}")

        try:
            await client.start_notify(CHAR_NOTIFY, on_notify)
        except Exception:
            pass

        async def send_ct(channel, cw, ww, desc=""):
            packet = bytes([0xAE, 0xAA, channel, cw, ww, 0x56])
            tag = f"  [{desc}]" if desc else ""
            print(f"  ch={channel:02X} CW={cw:2d} WW={ww:2d}  {hex_dump(packet)}{tag}")
            await client.write_gatt_char(CHAR_CMD, packet, response=False)
            await asyncio.sleep(delay)

        ct_steps = list(range(0, 100, 10)) + [99]  # 0,10,20,...,90,99

        # First, turn on the light (some models need A1 to wake up)
        print("\n--- Turning light ON ---")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
        await asyncio.sleep(1.0)

        for ch, ch_name in [(0x0A, "YN150WY"), (0x01, "YN360")]:
            print(f"\n{'='*60}")
            print(f"  Channel 0x{ch:02X} ({ch_name})")
            print(f"{'='*60}")

            # --- Cool white ramp ---
            print(f"\n--- Cool white ramp (WW=0) [ch=0x{ch:02X}] ---")
            for v in ct_steps:
                await send_ct(ch, v, 0, f"cool={v}")

            # --- Warm white ramp ---
            print(f"\n--- Warm white ramp (CW=0) [ch=0x{ch:02X}] ---")
            for v in ct_steps:
                await send_ct(ch, 0, v, f"warm={v}")

            # --- Both ramp up together ---
            print(f"\n--- Both ramp up (CW=WW) [ch=0x{ch:02X}] ---")
            for v in ct_steps:
                await send_ct(ch, v, v, f"both={v}")

            # --- Cross-fade: cool→warm ---
            print(f"\n--- Cross-fade cool->warm [ch=0x{ch:02X}] ---")
            for i in range(11):
                cw = 99 - i * 10
                ww = i * 10
                if cw < 0:
                    cw = 0
                if ww > 99:
                    ww = 99
                await send_ct(ch, cw, ww, f"cool={cw} warm={ww}")

            # --- Cross-fade: warm→cool ---
            print(f"\n--- Cross-fade warm->cool [ch=0x{ch:02X}] ---")
            for i in range(11):
                ww = 99 - i * 10
                cw = i * 10
                if ww < 0:
                    ww = 0
                if cw > 99:
                    cw = 99
                await send_ct(ch, cw, ww, f"cool={cw} warm={ww}")

        print("\nDone. Turning off ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)
        print("Color-temp auto-test complete.")


async def cmd_auto_test(address: str, delay: float = 1.5):
    """Combined auto-test: color temperature first, then RGB. Like YN150WY test style."""
    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  <- notify: {hex_dump(data)}")

        try:
            await client.start_notify(CHAR_NOTIFY, on_notify)
        except Exception:
            pass

        async def send_ct(channel, cw, ww, desc=""):
            packet = bytes([0xAE, 0xAA, channel, cw, ww, 0x56])
            tag = f"  [{desc}]" if desc else ""
            print(f"  ch={channel:02X} CW={cw:2d} WW={ww:2d}  {hex_dump(packet)}{tag}")
            await client.write_gatt_char(CHAR_CMD, packet, response=False)
            await asyncio.sleep(delay)

        async def send_rgb(r, g, b, desc=""):
            packet = bytes([0xAE, 0xA1, r, g, b, 0x56])
            tag = f"  [{desc}]" if desc else ""
            print(f"  RGB({r:3d},{g:3d},{b:3d})  {hex_dump(packet)}{tag}")
            await client.write_gatt_char(CHAR_CMD, packet, response=False)
            await asyncio.sleep(delay)

        ct_steps = list(range(0, 100, 10)) + [99]  # 0,10,20,...,90,99

        # ============================================================
        # Part 1: Color Temperature
        # ============================================================
        print("\n" + "=" * 60)
        print("  PART 1: Color Temperature Test")
        print("=" * 60)

        # Turn on light first
        print("\n--- Turning light ON ---")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
        await asyncio.sleep(1.0)

        for ch, ch_name in [(0x0A, "YN150WY"), (0x01, "YN360")]:
            print(f"\n{'─'*60}")
            print(f"  Channel 0x{ch:02X} ({ch_name})")
            print(f"{'─'*60}")

            # Cool white ramp
            print(f"\n--- Cool white ramp (WW=0) [ch=0x{ch:02X}] ---")
            for v in ct_steps:
                await send_ct(ch, v, 0, f"cool={v}")

            # Warm white ramp
            print(f"\n--- Warm white ramp (CW=0) [ch=0x{ch:02X}] ---")
            for v in ct_steps:
                await send_ct(ch, 0, v, f"warm={v}")

            # Both ramp up together
            print(f"\n--- Both ramp up (CW=WW) [ch=0x{ch:02X}] ---")
            for v in ct_steps:
                await send_ct(ch, v, v, f"both={v}")

            # Cross-fade: cool -> warm
            print(f"\n--- Cross-fade cool->warm [ch=0x{ch:02X}] ---")
            for i in range(11):
                cw = min(99, max(0, 99 - i * 10))
                ww = min(99, max(0, i * 10))
                await send_ct(ch, cw, ww, f"cool={cw} warm={ww}")

        # ============================================================
        # Part 2: RGB
        # ============================================================
        print("\n" + "=" * 60)
        print("  PART 2: RGB Test")
        print("=" * 60)

        rgb_steps = list(range(0, 256, 17)) + [255]  # 0,17,34,...,255

        # Red ramp
        print("\n--- Red ramp (G=0, B=0) ---")
        for v in rgb_steps:
            await send_rgb(v, 0, 0, f"red={v}")

        # Green ramp
        print("\n--- Green ramp (R=0, B=0) ---")
        for v in rgb_steps:
            await send_rgb(0, v, 0, f"green={v}")

        # Blue ramp
        print("\n--- Blue ramp (R=0, G=0) ---")
        for v in rgb_steps:
            await send_rgb(0, 0, v, f"blue={v}")

        # Hue rotation
        print("\n--- Hue rotation (12 steps) ---")
        hue_steps = [
            (255, 0, 0, "red"),
            (255, 128, 0, "orange"),
            (255, 255, 0, "yellow"),
            (128, 255, 0, "chartreuse"),
            (0, 255, 0, "green"),
            (0, 255, 128, "spring"),
            (0, 255, 255, "cyan"),
            (0, 128, 255, "azure"),
            (0, 0, 255, "blue"),
            (128, 0, 255, "violet"),
            (255, 0, 255, "magenta"),
            (255, 0, 128, "rose"),
        ]
        for r, g, b, name in hue_steps:
            await send_rgb(r, g, b, name)

        # White brightness ramp
        print("\n--- White brightness ramp (R=G=B) ---")
        for v in rgb_steps:
            await send_rgb(v, v, v, f"white={v}")

        # Done
        print("\nDone. Turning off ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)
        print("Auto-test complete.")


async def cmd_speed_test(address: str):
    """BLE speed/throughput benchmark - find the limits of command rate."""
    import colorsys
    import time

    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        # Turn on the light
        print("\n--- Turning light ON ---")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56]), response=False)
        await asyncio.sleep(0.5)

        def rgb_from_hue(hue: float) -> tuple[int, int, int]:
            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            return int(r * 255), int(g * 255), int(b * 255)

        # ============================================================
        # Phase 1: Raw throughput (write_without_response, no delay)
        # ============================================================
        print("\n" + "=" * 60)
        print("  Phase 1: Raw throughput (no delay, fire-and-forget)")
        print("=" * 60)

        N = 200
        print(f"\nSending {N} RGB commands as fast as possible...")
        errors = 0
        t0 = time.perf_counter()
        for i in range(N):
            hue = (i / N) % 1.0
            r, g, b = rgb_from_hue(hue)
            packet = bytes([0xAE, 0xA1, r, g, b, 0x56])
            try:
                await client.write_gatt_char(CHAR_CMD, packet, response=False)
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  error #{errors} at i={i}: {e}")
        t1 = time.perf_counter()
        elapsed = t1 - t0
        rate = N / elapsed if elapsed > 0 else 0
        print(f"\n  Sent: {N - errors}/{N}  Errors: {errors}")
        print(f"  Time: {elapsed:.3f}s  Rate: {rate:.1f} cmd/s")
        print(f"  Avg interval: {elapsed / N * 1000:.1f}ms per command")
        await asyncio.sleep(0.5)

        # ============================================================
        # Phase 2: Interval sweep - find the minimum reliable interval
        # ============================================================
        print("\n" + "=" * 60)
        print("  Phase 2: Interval sweep (find minimum reliable interval)")
        print("=" * 60)

        intervals_ms = [0, 2, 5, 10, 15, 20, 30, 50]
        CMDS_PER_INTERVAL = 60

        results = []
        for interval_ms in intervals_ms:
            interval_s = interval_ms / 1000.0
            errors = 0
            write_times = []

            print(f"\n  --- interval={interval_ms}ms, sending {CMDS_PER_INTERVAL} commands ---")
            t0 = time.perf_counter()
            for i in range(CMDS_PER_INTERVAL):
                hue = (i / CMDS_PER_INTERVAL) % 1.0
                r, g, b = rgb_from_hue(hue)
                packet = bytes([0xAE, 0xA1, r, g, b, 0x56])
                wt0 = time.perf_counter()
                try:
                    await client.write_gatt_char(CHAR_CMD, packet, response=False)
                except Exception:
                    errors += 1
                wt1 = time.perf_counter()
                write_times.append((wt1 - wt0) * 1000)  # ms
                if interval_s > 0:
                    await asyncio.sleep(interval_s)
            t1 = time.perf_counter()
            elapsed = t1 - t0
            actual_rate = CMDS_PER_INTERVAL / elapsed if elapsed > 0 else 0
            avg_write = sum(write_times) / len(write_times)
            max_write = max(write_times)
            min_write = min(write_times)

            print(f"    OK: {CMDS_PER_INTERVAL - errors}/{CMDS_PER_INTERVAL}  "
                  f"Rate: {actual_rate:.1f} cmd/s  "
                  f"Write avg/min/max: {avg_write:.1f}/{min_write:.1f}/{max_write:.1f}ms")

            results.append({
                "interval_ms": interval_ms,
                "rate": actual_rate,
                "errors": errors,
                "avg_write_ms": avg_write,
                "max_write_ms": max_write,
            })
            await asyncio.sleep(0.3)

        print("\n  Summary:")
        print(f"  {'Interval':>10} {'Rate':>10} {'Errors':>8} {'Avg write':>10} {'Max write':>10}")
        print(f"  {'-'*10} {'-'*10} {'-'*8} {'-'*10} {'-'*10}")
        for r in results:
            print(f"  {r['interval_ms']:>8}ms {r['rate']:>8.1f}/s {r['errors']:>8} "
                  f"{r['avg_write_ms']:>8.1f}ms {r['max_write_ms']:>8.1f}ms")

        # ============================================================
        # Phase 3: Visual rainbow test at different speeds
        # ============================================================
        print("\n" + "=" * 60)
        print("  Phase 3: Visual rainbow (watch for smooth vs choppy)")
        print("=" * 60)
        print("  Smooth = all commands received. Choppy/jumping = commands dropped.")

        RAINBOW_STEPS = 360  # full hue circle
        speeds = [
            (50, "50ms - slow baseline (20 fps)"),
            (20, "20ms - medium (50 fps)"),
            (10, "10ms - fast (100 fps)"),
            (5,  " 5ms - very fast (200 fps)"),
            (0,  " 0ms - maximum speed"),
        ]

        for delay_ms, label in speeds:
            print(f"\n  --- Rainbow: {label} ---")
            delay_s = delay_ms / 1000.0
            errors = 0
            t0 = time.perf_counter()
            for i in range(RAINBOW_STEPS):
                hue = i / RAINBOW_STEPS
                r, g, b = rgb_from_hue(hue)
                packet = bytes([0xAE, 0xA1, r, g, b, 0x56])
                try:
                    await client.write_gatt_char(CHAR_CMD, packet, response=False)
                except Exception:
                    errors += 1
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
            t1 = time.perf_counter()
            elapsed = t1 - t0
            actual_rate = RAINBOW_STEPS / elapsed if elapsed > 0 else 0
            print(f"    {RAINBOW_STEPS} steps in {elapsed:.2f}s = {actual_rate:.1f} cmd/s"
                  f"  errors={errors}")
            await asyncio.sleep(0.5)

        # ============================================================
        # Phase 4: Round-trip latency (write_with_response)
        # ============================================================
        print("\n" + "=" * 60)
        print("  Phase 4: Round-trip latency (write WITH response)")
        print("=" * 60)

        N_RTT = 30
        print(f"\n  Sending {N_RTT} commands with response=True...")
        rtt_times = []
        errors = 0
        for i in range(N_RTT):
            hue = (i / N_RTT) % 1.0
            r, g, b = rgb_from_hue(hue)
            packet = bytes([0xAE, 0xA1, r, g, b, 0x56])
            wt0 = time.perf_counter()
            try:
                await client.write_gatt_char(CHAR_CMD, packet, response=True)
                wt1 = time.perf_counter()
                rtt_times.append((wt1 - wt0) * 1000)
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"    error #{errors}: {e}")

        if rtt_times:
            avg_rtt = sum(rtt_times) / len(rtt_times)
            min_rtt = min(rtt_times)
            max_rtt = max(rtt_times)
            # sort for percentiles
            sorted_rtt = sorted(rtt_times)
            p50 = sorted_rtt[len(sorted_rtt) // 2]
            p95 = sorted_rtt[int(len(sorted_rtt) * 0.95)]
            max_rate_rtt = 1000 / avg_rtt if avg_rtt > 0 else 0
            print(f"\n  RTT (ms): avg={avg_rtt:.1f}  min={min_rtt:.1f}  max={max_rtt:.1f}"
                  f"  p50={p50:.1f}  p95={p95:.1f}")
            print(f"  Theoretical max rate (with response): {max_rate_rtt:.1f} cmd/s")
            print(f"  Errors: {errors}/{N_RTT}")
        else:
            print(f"  All {N_RTT} writes failed - device may not support write-with-response")

        # ============================================================
        # Done
        # ============================================================
        print("\n" + "=" * 60)
        print("  DONE - Turning off")
        print("=" * 60)
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)
        print("\nTips for interpreting results:")
        print("  - Phase 1 rate = raw BLE write throughput (OS + BLE stack limit)")
        print("  - Phase 2 shows if adding delay changes error rate")
        print("  - Phase 3: watch the light! Smooth rainbow = all commands received")
        print("  - Phase 4 RTT = true BLE round-trip, sets hard upper bound")
        print("  - If Phase 1 rate >> Phase 4 rate, writes are buffered/queued")
        print("  - Practical limit for smooth control ≈ Phase 4 rate")


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
    OFF = bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56])

    def make_rgb_stripe(frame):
        return RED if (frame // STRIPE_FRAMES) % 2 == 0 else BLUE

    def make_ct_stripe(frame, channel):
        return (
            bytes([0xAE, 0xAA, channel, 0x63, 0x00, 0x56])
            if (frame // STRIPE_FRAMES) % 2 == 0
            else bytes([0xAE, 0xAA, channel, 0x00, 0x63, 0x56])
        )

    # ── Connect to all lights ──
    print(f"\nConnecting to {len(lights_config)} lights...")
    clients = []
    lights = []
    try:
        for addr, mode, fps in lights_config:
            print(f"  {addr}  ({'RGB' if mode == 'rgb' else 'CT'} @ {fps}fps)...", end="", flush=True)
            client = BleakClient(addr, timeout=10.0)
            await client.connect()
            dev_name = await read_device_name(client)
            model, ct_ch, _ = detect_model_from_name(dev_name)
            label = f"{'RGB' if mode == 'rgb' else f'CT ch=0x{ct_ch:02X}'} @ {fps}fps"
            print(f" OK  name={dev_name!r}  model={model}  {label}")
            clients.append(client)

            total = int(fps * SEND_DURATION)
            lights.append({
                "client": client,
                "addr": addr,
                "name": dev_name,
                "model": model,
                "ct_ch": ct_ch,
                "mode": mode,
                "fps": fps,
                "total": total,
                "interval": 1.0 / fps,
                "make_packet": (
                    make_rgb_stripe
                    if mode == "rgb"
                    else (lambda frame, channel=ct_ch: make_ct_stripe(frame, channel))
                ),
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
            detail = (
                f"{light['mode'].upper()} @ {light['fps']} fps"
                if light["mode"] == "rgb"
                else f"CT ch=0x{light['ct_ch']:02X} @ {light['fps']} fps"
            )
            print(f"           {detail} ({light['total']} commands)")
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


async def cmd_probe_wy_ct(address: str):
    """Systematic YN150WY color temperature probe.

    Tests AA command with all channel values 0x00-0x0F on both CHAR_CMD and fff3,
    pausing after each step so the user can visually observe the light.
    """
    CHAR_FFF3 = "0000fff3-0000-1000-8000-00805f9b34fb"
    CHAR_FFF4 = "0000fff4-0000-1000-8000-00805f9b34fb"

    print(f"Connecting to {address} ...")
    async with BleakClient(address, timeout=10.0) as client:
        print(f"Connected: {client.is_connected}")

        def on_notify(_sender: BleakGATTCharacteristic, data: bytearray):
            print(f"  <- notify: {hex_dump(data)}")

        try:
            await client.start_notify(CHAR_NOTIFY, on_notify)
        except Exception:
            pass

        # Also try to subscribe to fff4 notifications
        try:
            await client.start_notify(CHAR_FFF4, on_notify)
            print(f"  (also subscribed to fff4 notify)")
        except Exception:
            pass

        async def send_and_wait(char_uuid, packet, desc):
            print(f"\n  {desc}")
            print(f"    -> {hex_dump(packet)}")
            await client.write_gatt_char(char_uuid, packet, response=False)
            await asyncio.sleep(0.3)
            input("    Press Enter for next...")

        # Step 0: Turn on with A1
        print("\n" + "=" * 60)
        print("  YN150WY Color Temperature Probe")
        print("=" * 60)
        print("\n--- Step 0: Turn light ON with A1 ---")
        await client.write_gatt_char(
            CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
        await asyncio.sleep(1.0)
        print("  Light should be ON now.")

        # Step 1: AA command on CHAR_CMD with channel 0x00 - 0x0F
        print("\n" + "=" * 60)
        print("  Step 1: AA on CHAR_CMD, channel 0x00-0x0F, CW=99 WW=0")
        print("=" * 60)
        for ch in range(0x10):
            packet = bytes([0xAE, 0xAA, ch, 0x63, 0x00, 0x56])
            await send_and_wait(CHAR_CMD, packet, f"ch=0x{ch:02X} CW=99 WW=0")

        # Step 2: AA command on CHAR_CMD with channel 0x00 - 0x0F, warm white
        print("\n" + "=" * 60)
        print("  Step 2: AA on CHAR_CMD, channel 0x00-0x0F, CW=0 WW=99")
        print("=" * 60)
        for ch in range(0x10):
            packet = bytes([0xAE, 0xAA, ch, 0x00, 0x63, 0x56])
            await send_and_wait(CHAR_CMD, packet, f"ch=0x{ch:02X} CW=0 WW=99")

        # Step 3: Try AA on fff3
        print("\n" + "=" * 60)
        print("  Step 3: AA on fff3, channel 0x00-0x03")
        print("=" * 60)
        for ch in range(0x04):
            packet = bytes([0xAE, 0xAA, ch, 0x63, 0x00, 0x56])
            await send_and_wait(CHAR_FFF3, packet, f"fff3 ch=0x{ch:02X} CW=99 WW=0")
        for ch in range(0x04):
            packet = bytes([0xAE, 0xAA, ch, 0x00, 0x63, 0x56])
            await send_and_wait(CHAR_FFF3, packet, f"fff3 ch=0x{ch:02X} CW=0 WW=99")

        # Step 4: Try other command bytes (A2, A4-A9, AB-AF) for color temp
        print("\n" + "=" * 60)
        print("  Step 4: Other cmd bytes on CHAR_CMD (CW=99 in byte3)")
        print("=" * 60)
        for cmd in [0xA2, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF]:
            packet = bytes([0xAE, cmd, 0x00, 0x63, 0x00, 0x56])
            await send_and_wait(CHAR_CMD, packet, f"cmd=0x{cmd:02X} byte2=0x00 byte3=0x63 byte4=0x00")

        # Step 5: Try CW/WW with values > 99 (0x64-0xFF range)
        print("\n" + "=" * 60)
        print("  Step 5: AA ch=0x00, high CW values (100, 128, 200, 255)")
        print("=" * 60)
        for cw in [100, 128, 200, 255]:
            packet = bytes([0xAE, 0xAA, 0x00, cw, 0x00, 0x56])
            await send_and_wait(CHAR_CMD, packet, f"ch=0x00 CW={cw} WW=0")

        print("\n--- Done. Turning off ---")
        await client.write_gatt_char(
            CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)
        print("Probe complete.")


async def cmd_sync(addresses: list[str]):
    """Sync test: connect to multiple lights simultaneously, show CW/WW/RGB together.

    Auto-detects model by BLE device name to use the correct color temp channel:
      - YN150WY -> channel 0x0A
      - YN150Ultra RGB -> channel 0x00
      - YN360 (or unknown) -> channel 0x01
    """

    DEFAULT_ADDRS = [
        "DB:B9:85:86:42:60",   # YN150Ultra RGB
        "D0:32:34:39:6D:6F",   # YN150Ultra RGB
        "D0:32:34:39:74:49",   # YN150WY
    ]

    if not addresses:
        addresses = DEFAULT_ADDRS

    HOLD = 3.0  # seconds to hold each test phase

    print(f"\nConnecting to {len(addresses)} lights...")
    clients = []
    lights = []

    try:
        for addr in addresses:
            print(f"  {addr} ...", end="", flush=True)
            client = BleakClient(addr, timeout=10.0)
            await client.connect()
            dev_name = await read_device_name(client)
            model, ct_ch, has_rgb = detect_model_from_name(dev_name)
            print(f" OK  name={dev_name!r}  model={model}  ct_ch=0x{ct_ch:02X}  rgb={has_rgb}")
            clients.append(client)
            lights.append({
                "client": client,
                "addr": addr,
                "name": dev_name,
                "model": model,
                "ct_ch": ct_ch,
                "has_rgb": has_rgb,
            })

        async def send_all(packets: list[tuple[dict, bytes]]):
            """Send a packet to each light (list of (light, packet) pairs)."""
            for light, packet in packets:
                await light["client"].write_gatt_char(CHAR_CMD, packet, response=False)

        async def send_all_same(packet: bytes):
            """Send the same packet to all lights."""
            for light in lights:
                await light["client"].write_gatt_char(CHAR_CMD, packet, response=False)

        rgb_lights = [l for l in lights if l["has_rgb"]]

        # Print test plan
        print()
        print("=" * 64)
        print("  Sync Test - All Lights Simultaneous")
        print("=" * 64)
        for i, l in enumerate(lights):
            cap = "RGB + CT" if l["has_rgb"] else "CT only"
            print(f"  {i+1}. {l['addr']}  {l['model']}  ({cap})")
        print()
        print(f"  Each phase holds {HOLD:.0f}s for visual observation.")
        print("=" * 64)

        # === Phase 0: Turn all on ===
        print("\n--- Turning all lights ON ---")
        await send_all_same(bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]))
        await asyncio.sleep(1.0)

        # === Phase 1: Cool White Max ===
        print("\n>>> Phase 1: Cool White Max (CW=99, WW=0) <<<")
        await send_all([
            (l, bytes([0xAE, 0xAA, l["ct_ch"], 0x63, 0x00, 0x56]))
            for l in lights
        ])
        print(f"    Holding {HOLD:.0f}s...")
        await asyncio.sleep(HOLD)

        # === Phase 2: Warm White Max ===
        print("\n>>> Phase 2: Warm White Max (CW=0, WW=99) <<<")
        await send_all([
            (l, bytes([0xAE, 0xAA, l["ct_ch"], 0x00, 0x63, 0x56]))
            for l in lights
        ])
        print(f"    Holding {HOLD:.0f}s...")
        await asyncio.sleep(HOLD)

        # === Phase 3: Cool + Warm both 50% ===
        print("\n>>> Phase 3: Mixed (CW=50, WW=50) <<<")
        await send_all([
            (l, bytes([0xAE, 0xAA, l["ct_ch"], 0x32, 0x32, 0x56]))
            for l in lights
        ])
        print(f"    Holding {HOLD:.0f}s...")
        await asyncio.sleep(HOLD)

        # === Phase 4: Cross-fade cool -> warm ===
        print("\n>>> Phase 4: Cross-fade Cool -> Warm (5 steps) <<<")
        cross_steps = [(99, 0), (75, 25), (50, 50), (25, 75), (0, 99)]
        for cw, ww in cross_steps:
            print(f"    CW={cw:2d} WW={ww:2d}")
            await send_all([
                (l, bytes([0xAE, 0xAA, l["ct_ch"], cw, ww, 0x56]))
                for l in lights
            ])
            await asyncio.sleep(2.0)

        # === Phase 5-7: RGB (only for lights with RGB) ===
        if rgb_lights:
            print(f"\n>>> Phase 5: RED (RGB lights only, {len(rgb_lights)} lights) <<<")
            for l in rgb_lights:
                await l["client"].write_gatt_char(
                    CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0x00, 0x00, 0x56]), response=False)
            print(f"    Holding {HOLD:.0f}s...")
            await asyncio.sleep(HOLD)

            print(f"\n>>> Phase 6: GREEN <<<")
            for l in rgb_lights:
                await l["client"].write_gatt_char(
                    CHAR_CMD, bytes([0xAE, 0xA1, 0x00, 0xFF, 0x00, 0x56]), response=False)
            print(f"    Holding {HOLD:.0f}s...")
            await asyncio.sleep(HOLD)

            print(f"\n>>> Phase 7: BLUE <<<")
            for l in rgb_lights:
                await l["client"].write_gatt_char(
                    CHAR_CMD, bytes([0xAE, 0xA1, 0x00, 0x00, 0xFF, 0x56]), response=False)
            print(f"    Holding {HOLD:.0f}s...")
            await asyncio.sleep(HOLD)

            # === Phase 8: Hue rotation on RGB lights ===
            print(f"\n>>> Phase 8: Hue rotation (RGB lights) <<<")
            hue_steps = [
                (255, 0, 0, "red"),
                (255, 128, 0, "orange"),
                (255, 255, 0, "yellow"),
                (0, 255, 0, "green"),
                (0, 255, 255, "cyan"),
                (0, 0, 255, "blue"),
                (128, 0, 255, "violet"),
                (255, 0, 255, "magenta"),
            ]
            for r, g, b, name in hue_steps:
                print(f"    {name} ({r},{g},{b})")
                for l in rgb_lights:
                    await l["client"].write_gatt_char(
                        CHAR_CMD, bytes([0xAE, 0xA1, r, g, b, 0x56]), response=False)
                await asyncio.sleep(1.5)

        # === Done ===
        print("\n--- Turning all lights OFF ---")
        await send_all_same(bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]))
        print("\nSync test complete!")

    finally:
        print("  Disconnecting...")
        for client in clients:
            try:
                if client.is_connected:
                    await client.write_gatt_char(
                        CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)
                    await client.disconnect()
            except Exception:
                pass
        print("  Done.")


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
    elif command == "wy-ct":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py wy-ct ADDRESS [CHANNEL]")
            sys.exit(1)
        channel = 0x0A
        if len(sys.argv) >= 4:
            channel = int(sys.argv[3], 16)
        asyncio.run(cmd_wy_ct(sys.argv[2], channel))
    elif command == "probe":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py probe ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_probe(sys.argv[2]))
    elif command == "probe-ct":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py probe-ct ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_probe_ct(sys.argv[2]))
    elif command == "probe-ct2":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py probe-ct2 ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_probe_ct2(sys.argv[2]))
    elif command == "scan-cmds":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py scan-cmds ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_scan_cmds(sys.argv[2]))
    elif command == "test-wy":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py test-wy ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_test_wy(sys.argv[2]))
    elif command == "probe-ct-ch":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py probe-ct-ch ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_probe_ct_ch(sys.argv[2]))
    elif command == "auto-test":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py auto-test ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_auto_test(sys.argv[2]))
    elif command == "auto-rgb":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py auto-rgb ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_auto_rgb(sys.argv[2]))
    elif command == "auto-ct":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py auto-ct ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_auto_ct(sys.argv[2]))
    elif command == "speed-test":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py speed-test ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_speed_test(sys.argv[2]))
    elif command == "rainbow":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py rainbow ADDRESS [FPS,FPS,...]")
            print("  e.g.: debug_ble.py rainbow AA:BB:CC:DD:EE:FF 100,150,200,250,300")
            sys.exit(1)
        fps_list = None
        if len(sys.argv) >= 4:
            fps_list = [int(x) for x in sys.argv[3].split(",")]
        asyncio.run(cmd_rainbow(sys.argv[2], fps_list))
    elif command == "probe-wy":
        if len(sys.argv) < 3:
            print("Usage: debug_ble.py probe-wy ADDRESS")
            sys.exit(1)
        asyncio.run(cmd_probe_wy_ct(sys.argv[2]))
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
    elif command == "sync":
        addrs = sys.argv[2:] if len(sys.argv) > 2 else []
        asyncio.run(cmd_sync(addrs))
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
