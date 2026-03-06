"""
YN150 BLE debug tool - standalone, no Home Assistant needed.

Usage:
  python debug_ble.py scan              # Scan for nearby BLE devices
  python debug_ble.py services ADDRESS  # List all GATT services/characteristics
  python debug_ble.py sniff ADDRESS     # Subscribe to all notify/indicate characteristics
  python debug_ble.py write ADDRESS UUID HEX  # Write raw hex bytes to a characteristic
  python debug_ble.py probe ADDRESS     # Try command types A0-AF interactively
  python debug_ble.py probe-ct ADDRESS  # Probe color-temperature commands (YN150WY)
  python debug_ble.py auto-rgb ADDRESS  # Auto-test RGB colors (YN150 Ultra RGB / YN360)
  python debug_ble.py auto-ct ADDRESS   # Auto-test color temperature (YN150WY + YN360)

Examples:
  python debug_ble.py scan
  python debug_ble.py services AA:BB:CC:DD:EE:FF
  python debug_ble.py sniff AA:BB:CC:DD:EE:FF
  python debug_ble.py write AA:BB:CC:DD:EE:FF f000aa61-0451-4000-b000-000000000000 AEA1FF000056
  python debug_ble.py probe AA:BB:CC:DD:EE:FF
  python debug_ble.py auto-rgb DB:B9:85:86:42:60   # YN150 Ultra RGB
  python debug_ble.py auto-ct D0:32:34:39:74:49     # YN150WY
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


async def cmd_probe_ct(address: str):
    """Probe color-temperature commands on YN150WY-style lights."""
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
            ("AA: ch=01 cool=63 warm=00",            [0xAE, 0xAA, 0x01, 0x63, 0x00, 0x56]),
            ("AA: ch=01 cool=00 warm=63",            [0xAE, 0xAA, 0x01, 0x00, 0x63, 0x56]),
            ("AA: ch=00 cool=63 warm=00",            [0xAE, 0xAA, 0x00, 0x63, 0x00, 0x56]),
            ("AA: ch=02 cool=63 warm=00",            [0xAE, 0xAA, 0x02, 0x63, 0x00, 0x56]),

            # Group D: Try fff3 for color temp
            ("fff3: AA ch=01 cool=63 warm=00",       "fff3"),
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
                p = bytes([0xAE, 0xAA, 0x01, 0x63, 0x00, 0x56])
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

        # Scan all channel bytes, alternating full cool/warm with max values
        print("\n=== Scanning AE AA [ch] — cool(FF,00) vs warm(00,FF) ===")
        print("Watch for any change. Ctrl+C to stop.\n")
        try:
            for ch in range(256):
                p1 = bytes([0xAE, 0xAA, ch, 0xFF, 0x00, 0x56])
                print(f"  ch=0x{ch:02X} cool  {hex_dump(p1)}")
                await client.write_gatt_char(CHAR_CMD, p1, response=False)
                await asyncio.sleep(0.08)
                p2 = bytes([0xAE, 0xAA, ch, 0x00, 0xFF, 0x56])
                print(f"  ch=0x{ch:02X} warm  {hex_dump(p2)}")
                await client.write_gatt_char(CHAR_CMD, p2, response=False)
                await asyncio.sleep(0.08)
        except KeyboardInterrupt:
            print(f"\n*** Stopped at ch=0x{ch:02X} ***")
            input("Press Enter to continue...")

        # Reset
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA1, 0xFF, 0xFF, 0xFF, 0x56]), response=False)
        await asyncio.sleep(1.0)

        # Scan all command bytes (not just AA)
        print("\n=== Scanning AE [cmd] — try all cmd bytes with 02 FF 00 ===")
        print("Ctrl+C to stop.\n")
        try:
            for cmd in range(256):
                if cmd in (0xA1, 0xA3):
                    continue
                packet = bytes([0xAE, cmd, 0x02, 0xFF, 0x00, 0x56])
                print(f"  cmd=0x{cmd:02X}  {hex_dump(packet)}")
                await client.write_gatt_char(CHAR_CMD, packet, response=False)
                await asyncio.sleep(0.08)
        except KeyboardInterrupt:
            print(f"\n*** Stopped at cmd=0x{cmd:02X} ***")

        print("\nDone. Turning off ...")
        await client.write_gatt_char(CHAR_CMD, bytes([0xAE, 0xA3, 0x00, 0x00, 0x00, 0x56]), response=False)


async def cmd_auto_ct(address: str, delay: float = 1.5):
    """Automatic color temperature test - cycles CW/WW on both channel 0x00 and 0x01."""
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

        for ch, ch_name in [(0x00, "YN150WY"), (0x01, "YN360")]:
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
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
