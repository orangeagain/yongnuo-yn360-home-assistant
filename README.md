# YONGNUO YN360 LED video light custom component for Home Assistant

Control your YN360 LED video lights directly from Home Assistant.

The YN360 is a Bluetooth LE device, thus it is necessary to be in proximity of a Bluetooth radio attached to Home Assistant (either directly or by ESP32 Bluetooth proxy).

## Installation
Copy contents of custom_components/yongnuo_yn360/ to custom_components/yongnuo_yn360/ in your Home Assistant config folder.

## Installation using HACS
HACS is a community store for Home Assistant. Add this repository to HACS and install "YONGNUO YN360 LED video light" from there.

## Features

- Power on/off
- RGB color control
- Full config flow with auto-discovery
- Persistent BLE connection per light (low latency for repeated updates)
- High-speed command coalescing (only latest command is kept)
- Multi-light support (add one config entry per light)
- Adaptive retry policy optimized for speed and stability
- On turn_off, immediately releases BLE connection so mobile apps can take over quickly

## High-speed mode architecture

This integration now uses a per-device command worker:

- **One worker per light**: each configured MAC address has an independent command pipeline.
- **Persistent BLE session per light**: avoids reconnecting on every single command.
- **Serialized writes per light**: prevents command races and out-of-order writes.
- **Latest-command wins**: during rapid changes, intermediate commands are dropped so the light converges quickly to the newest state.

This is designed for fast scene transitions, slider drags, and high-frequency automations.

## Retry behavior (important)

Retry strategy is intentionally asymmetric:

- Every command is attempted once immediately.
- If command traffic is still active (newer command appears), retries are skipped.
- A command is retried **only if it remains the latest command** for a quiet window.
- If a newer command arrives while retrying, retries for the older command are aborted immediately.

In short:

- **High-speed changes** -> no retry (favor responsiveness)
- **Low-speed final state** -> retry enabled (favor reliability)

## Multi-light behavior

Run "Add Integration" repeatedly and select/enter each light MAC address.
Each configured address becomes an independent Home Assistant light entity.

For setups like 3 lights:

- Lights operate in parallel across devices.
- Within each single light, writes remain serialized for correctness.

## Mobile coexistence

To support smoother handoff between Home Assistant and mobile app control:

- After a successful `turn_off` command, this integration disconnects BLE immediately.
- On the next `turn_on`/color command from Home Assistant, it reconnects automatically.

This allows your phone app to reclaim the BLE link faster after Home Assistant turns the light off.

## Performance notes and limits

- Real-world throughput depends on BLE radio quality, interference, and proxy/adapter capabilities.
- If many BLE devices share one adapter, peak update rate may be constrained by the adapter.
- Persistent connections are released after an idle period to reduce background BLE load.

## Requirements

- Bluetooth is available in Home Assistant instance (either directly or by proxy)
- Home Assistant 2025.7+

## Acknowledgments

This integration is heavily inspired by and based on the original [Lantern](https://github.com/kenkeiter/lantern) project by @kenkeiter, which reverse-engineered the Bluetooth protocol used by YONGNUO LED lights.
