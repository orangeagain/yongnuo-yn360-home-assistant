# Project: Yongnuo YN360 Home Assistant Integration

## Current Goal

**适配 YN150**：当前代码只支持 YN360，主要目标是让代码同时兼容 YN150 系列。

**已验证：YN150 与 YN360 的 BLE 协议基本兼容，RGB 命令相同，色温命令的 channel 字节不同（YN360=0x01, YN150Ultra RGB=0x00, YN150WY=0x0A）。**

**部署到服务器请参考deploy.md**

## Project Structure

```
YN150Ultra RGB [YN150Ultra RGB] (D0:32:34:39:6D:6F)
YN150Ultra RGB-V [YN150Ultra RGB] (DB:B9:85:86:42:60)
YN150WY [YN150WY] (D0:32:34:39:74:49)
```

```
custom_components/yongnuo_yn360/
  __init__.py          - HA integration setup, forwards to light platform
  const.py             - DOMAIN = "yongnuo_yn360"
  config_flow.py       - HA config flow (auto-discovery by BLE)
  light.py             - LightEntity implementation (RGB color mode)
  yongnuo_yn360_device.py - BLE transport layer (core protocol logic)
  manifest.json        - HA integration manifest
  translations/        - en.json, de.json
```

## BLE Protocol

- GATT Characteristic UUID: `f000aa61-0451-4000-b000-000000000000`
- Packet format: 6 bytes, fixed length, big-endian
  - Header: `0xAE`, Footer: `0x56`, second byte = command type

### Command Table

| Command | Format | Notes |
|---------|--------|-------|
| RGB color | `AE A1 RR GG BB 56` | RR/GG/BB: 0x00-0xFF |
| Color temp (YN360) | `AE AA 01 CW WW 56` | CW=cool white, WW=warm white, range 0-99 (0x00-0x63) |
| Color temp (YN150 Ultra RGB) | `AE AA 00 CW WW 56` | channel byte = 0x00 |
| Color temp (YN150WY) | `AE AA 0A CW WW 56` | channel byte = 0x0A |
| Turn off | `AE A3 00 00 00 56` | All models |

### Color Temperature Protocol Details (verified 2026-03-07)

- YN360 uses channel=0x01, YN150 Ultra RGB uses channel=0x00, YN150WY uses channel=0x0A
- CW/WW values range 0-99 (0x63 = max), NOT 0-255. Values > 99 are ignored
- On YN150WY, `AE A1` turns the light on but RGB values are ignored (no RGB LEDs)
- On YN150WY, a practical turn-on sequence from OFF is: send `AE A1 FF FF FF 56`, wait briefly (`0.5-1.0s` tested), then send `AE AA 0A CW WW 56`
- Reference: [Samuel Pinches YN360 BLE reverse engineering](https://samuelpinches.com.au/hacking/hacking-yn360-light-wand/), [kenkeiter/lantern](https://github.com/kenkeiter/lantern)

## Architecture

- Per-device persistent BLE connection via `bleak.BleakClient`
- In Home Assistant, prefer `bleak_retry_connector.establish_connection()` over raw `BleakClient.connect()` to avoid backend/connection-slot failures
- Async worker task per device, coalesces rapid updates (latest-command-wins)
- State updates should await actual worker completion; optimistic state changes can mask failed BLE writes
- Retry policy: no retry during high-speed stream, retry only for final (quiet) command
- Idle disconnect after 12s; immediate disconnect after turn_off for mobile app coexistence
- Disconnect cleanup may raise `EOFError` on some HA/BlueZ paths; treat it as a cleanup issue, not a command failure
- Brightness: HA 0-255 remapped to 0-100%, applied as RGB scaling (no separate brightness command)

## Key Dependencies

- `bleak` - BLE communication
- `homeassistant` - HA core APIs (bluetooth discovery, LightEntity, config entries)

## YN150 Compatibility Testing (2026-03-07)

### Discovered Devices

| Address | Name | Type |
|---------|------|------|
| `DB:B9:85:86:42:60` | YN150Ultra RGB | YN150 (tested) |
| `D0:32:34:39:6D:6F` | YN150Ultra RGB | YN150 |
| `D0:32:34:39:74:49` | YN150WY | YN150 color temp variant |

### YN150 GATT Services (from DB:B9:85:86:42:60)

- Service `f000aa60-0451-4000-b000-000000000000` (same as YN360):
  - `f000aa61` (write) - **command channel, same UUID as YN360**
  - `f000aa63` (notify) - status feedback from light
  - `0000fff3` (write) - additional write channel (purpose TBD)
  - `0000fff4` (notify + write) - bidirectional (purpose TBD)
  - `0000fff5` (read + write) - possibly config/firmware
- Service `02f00000-...-fe00` - unknown (possibly OTA/firmware)

### Protocol Compatibility Result

**YN150Ultra RGB** verified commands:
- `AE A1 FF 00 00 56` - red: OK
- `AE A1 00 FF 00 56` - green: OK
- `AE A1 00 00 FF 56` - blue: OK
- `AE AA 00 63 00 56` - cool white max: OK (channel=0x00)
- `AE A3 00 00 00 56` - turn off: OK

YN150Ultra RGB supports both RGB and color temperature (has both RGB and CW/WW LEDs).

### YN150WY Color Temperature Testing (2026-03-07)

**YN150WY (`D0:32:34:39:74:49`) color temp protocol verified:**

- GATT services identical to YN150Ultra RGB
- `fff5` reads as ASCII "CHAR5_VALUE" (placeholder, not useful)
- `AE A1 RR GG BB 56` turns light on, but RGB values ignored (WY has no RGB LEDs)
- `AE AA 01 CW WW 56` (YN360 format, channel=0x01): **no stable response**
- `AE AA 00 CW WW 56` (channel=0x00): **no stable response**
- `AE AA 09 CW WW 56` (channel=0x09): **latest retest not reproducible, do not use as default**
- `AE AA 0A CW WW 56` (channel=0x0A): **works in latest retest**
  - `AE AA 0A 63 00 56` - cool white max: OK
  - `AE AA 0A 00 63 56` - warm white max: OK
- CW/WW range: 0-99 (0x00-0x63). Values 0xFF ignored by device
- Practical HA integration note: when the light is OFF, sending only `AE AA 0A CW WW 56` may not visibly wake the lamp; reliable behavior was `AE A1 FF FF FF 56`, wait about `0.5s`, then `AE AA 0A CW WW 56`

### Remaining Work

- Verify whether all YN150WY units use channel `0x0A`, or whether some firmware revisions differ
- Consider adding a configurable color-temp channel override for field debugging
- Explore `fff3`/`fff4` characteristics for additional features (effects, etc.)

## Debug Tool

`debug_ble.py` - standalone BLE debug script (no HA dependency, uses `bleak`):
- `scan` - discover nearby BLE devices
- `services ADDRESS` - list GATT services/characteristics
- `sniff ADDRESS` - subscribe to all notify characteristics
- `write ADDRESS UUID HEX` - write raw bytes to a characteristic
- `wy-ct ADDRESS [CHANNEL]` - fixed OFF -> WAKE -> COOL -> WARM sequence (default channel `0x0A`)
- `probe ADDRESS` - try command types A0-AF interactively
- `probe-ct ADDRESS` - probe color-temperature commands (first attempt, light must be off)
- `probe-ct2 ADDRESS` - probe color-temp with light already ON, also tries fff3
- `scan-cmds ADDRESS` - brute-force scan all 256 command bytes (auto, watch for changes)
- `test-wy ADDRESS` - slow careful test for YN150WY color temperature
