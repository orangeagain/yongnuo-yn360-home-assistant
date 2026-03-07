# Project: Yongnuo YN360 Home Assistant Integration

## Current Goal

**适配 YN150**：当前代码只支持 YN360，主要目标是让代码同时兼容 YN150 系列。

**已验证：YN150 与 YN360 的 BLE 协议基本兼容，RGB 命令相同，色温命令的 channel 字节不同（YN360=0x01, YN150Ultra RGB=0x00, YN150WY=0x09）。**

## Project Structure

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
| Color temp (YN150WY) | `AE AA 09 CW WW 56` | channel byte = 0x09 |
| Turn off | `AE A3 00 00 00 56` | All models |

### Color Temperature Protocol Details (verified 2026-03-07)

- YN360 uses channel=0x01, YN150 Ultra RGB uses channel=0x00, YN150WY uses channel=0x09
- CW/WW values range 0-99 (0x63 = max), NOT 0-255. Values > 99 are ignored
- On YN150WY, `AE A1` turns the light on but RGB values are ignored (no RGB LEDs)
- Reference: [Samuel Pinches YN360 BLE reverse engineering](https://samuelpinches.com.au/hacking/hacking-yn360-light-wand/), [kenkeiter/lantern](https://github.com/kenkeiter/lantern)

## Architecture

- Per-device persistent BLE connection via `bleak.BleakClient`
- Async worker task per device, coalesces rapid updates (latest-command-wins)
- Retry policy: no retry during high-speed stream, retry only for final (quiet) command
- Idle disconnect after 12s; immediate disconnect after turn_off for mobile app coexistence
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
- `AE AA 01 CW WW 56` (YN360 format, channel=0x01): **no response**
- `AE AA 00 CW WW 56` (channel=0x00): **no response** (previously incorrectly documented as working)
- `AE AA 09 CW WW 56` (channel=0x09): **works!**
  - `AE AA 09 63 00 56` - cool white max: OK
  - `AE AA 09 00 63 56` - warm white max: OK
- CW/WW range: 0-99 (0x00-0x63). Values 0xFF ignored by device

### Remaining Work

- Update integration to support color temperature mode (HA `COLOR_TEMP` color mode)
- Detect model by BLE device name to select correct channel byte (YN360=0x01, YN150Ultra RGB=0x00, YN150WY=0x09)
- Explore `fff3`/`fff4` characteristics for additional features (effects, etc.)

## Debug Tool

`debug_ble.py` - standalone BLE debug script (no HA dependency, uses `bleak`):
- `scan` - discover nearby BLE devices
- `services ADDRESS` - list GATT services/characteristics
- `sniff ADDRESS` - subscribe to all notify characteristics
- `write ADDRESS UUID HEX` - write raw bytes to a characteristic
- `probe ADDRESS` - try command types A0-AF interactively
- `probe-ct ADDRESS` - probe color-temperature commands (first attempt, light must be off)
- `probe-ct2 ADDRESS` - probe color-temp with light already ON, also tries fff3
- `scan-cmds ADDRESS` - brute-force scan all 256 command bytes (auto, watch for changes)
- `test-wy ADDRESS` - slow careful test for YN150WY color temperature
