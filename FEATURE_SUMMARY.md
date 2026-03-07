# Feature Summary

## Overview

This document summarizes the feature work added to the `yongnuo_yn360` Home Assistant custom integration.

Main goal:

- Extend the integration from a single YN360-only implementation to one integration that supports:
  - YN360
  - YN150
  - YN150RGB

Date:

- 2026-03-07

## New Functional Capabilities

### 1. Multi-model support

The integration now supports three Yongnuo light profiles:

- `yn360`
- `yn150`
- `yn150rgb`

Each model is handled as a protocol profile instead of hardcoding all behavior as YN360.

### 2. Dynamic light capability exposure

Entity capabilities are now model-dependent:

- `YN360`: RGB + color temperature
- `YN150RGB`: RGB + color temperature
- `YN150`: color temperature only

This means Home Assistant will expose the correct `supported_color_modes` for each configured light.

### 3. Color temperature support

Color temperature support was added with per-model channel mapping:

- `YN360` -> channel `0x01`
- `YN150RGB` -> channel `0x00`
- `YN150` -> channel `0x0A`

Packet format:

```text
AE AA <channel> <CW> <WW> 56
```

Behavior:

- Kelvin is mapped to cool-white / warm-white output
- White channel values are clamped to `0-99`
- Brightness is applied to the final CW/WW output

### 4. RGB support remains available where hardware supports it

RGB output continues to use:

```text
AE A1 R G B 56
```

Supported on:

- `YN360`
- `YN150RGB`

Not exposed on:

- `YN150`

### 5. Config flow now stores model type

The config entry now stores:

- `address`
- `model`

This allows the integration to restore the correct protocol behavior after restart without re-detecting everything from scratch.

### 6. Legacy config compatibility

Older config entries that only contained `address` are now handled automatically.

On setup:

- If `model` is missing, the integration tries to infer the model from discovered BLE information
- The config entry is updated in place with the inferred model

This avoids forcing users to delete and re-add existing devices in most cases.

## Architecture Changes

### New model registry

A new file was added:

- `custom_components/yongnuo_yn360/models.py`

It contains:

- model profile definitions
- discovery-name based model guessing
- selector options for the config flow
- helper methods for loading a model profile

This is the core abstraction that makes future model additions easier.

### BLE transport refactor

The BLE transport layer in `yongnuo_yn360_device.py` was kept structurally the same, but protocol encoding was made model-aware.

Kept:

- persistent BLE connection
- command worker
- latest-command-wins coalescing
- retry policy
- idle disconnect

Added:

- RGB packet builder per model capability
- color temperature packet builder per model channel
- validation for unsupported operations

### Light entity refactor

`light.py` was updated so the entity:

- loads a model profile
- exposes dynamic `supported_color_modes`
- exposes `color_temp_kelvin`
- switches between RGB and color temperature command paths
- reports correct device model metadata to Home Assistant

## File-level Summary

### `custom_components/yongnuo_yn360/models.py`

New file.

Purpose:

- define Yongnuo model profiles
- detect model from BLE name
- provide shared lookup helpers

### `custom_components/yongnuo_yn360/const.py`

Added constants for:

- `CONF_ADDRESS`
- `CONF_MODEL`
- model keys
- default model
- color temperature limits
- white channel max value

### `custom_components/yongnuo_yn360/config_flow.py`

Updated to:

- show model selection in setup UI
- label discovered devices with guessed model
- save `model` into config entries

### `custom_components/yongnuo_yn360/light.py`

Updated to:

- create entities from `address + model`
- support RGB and/or color temperature depending on profile
- support Kelvin-based control
- choose the correct command path automatically

### `custom_components/yongnuo_yn360/yongnuo_yn360_device.py`

Updated to:

- accept `model` at initialization
- build RGB packets only for RGB-capable models
- build color temperature packets with model-specific channel values
- keep the existing async worker and BLE session behavior

### `custom_components/yongnuo_yn360/__init__.py`

Updated to:

- migrate old entries without `model`
- save normalized entry data into `hass.data`

### `custom_components/yongnuo_yn360/manifest.json`

Updated integration metadata:

- name changed to generic `YONGNUO LED light`
- version bumped to `1.1.0`

### `custom_components/yongnuo_yn360/translations/en.json`
### `custom_components/yongnuo_yn360/translations/de.json`

Updated to match the new generic multi-model integration wording and model selector.

### `README.md`

Updated to document:

- multi-model support
- RGB vs color temperature support by model
- model selection during setup

## Protocol Mapping Summary

### Turn off

All models use:

```text
AE A3 00 00 00 56
```

### RGB

Used by:

- `YN360`
- `YN150RGB`

Packet:

```text
AE A1 RR GG BB 56
```

### Color temperature

Used by:

- `YN360`
- `YN150`
- `YN150RGB`

Packet:

```text
AE AA CH CW WW 56
```

Channel mapping:

- `YN360` -> `0x01`
- `YN150RGB` -> `0x00`
- `YN150` -> `0x0A`

## Deployment and Runtime Notes

### HAOS deployment

The latest code was deployed to:

- `homeassistant.local`
- path: `/config/custom_components/yongnuo_yn360`

Before deployment:

- the previous deployed version was backed up under `/config/custom_components/_backup/`

After deployment:

- Home Assistant Core was restarted successfully

### Server cleanup

The following cleanup was completed on HAOS:

- removed `__pycache__` from the deployed integration
- removed `__pycache__` from backup copies

### Validation performed

Local validation:

- Python syntax check via `python -m py_compile`
- JSON parsing check for manifest and translations

Runtime validation:

- Home Assistant Core restart completed successfully
- no deployment-time import traceback was observed after restart

## Known Follow-up Items

### 1. Deprecated Home Assistant constant

Runtime logs show that `ATTR_COLOR_TEMP` is deprecated in current Home Assistant.

The current implementation still accepts it for compatibility, but a future cleanup should move fully to Kelvin-only handling if strict forward compatibility is required.

### 2. BLE connection-slot warnings

Historical logs show warnings like:

- no backend with an available connection slot

This is not caused by the multi-model code itself. It indicates Bluetooth backend / proxy capacity limits and may need adapter-side tuning if it continues.

## Outcome

The integration is no longer limited to a single hardcoded YN360 implementation.

It now supports a shared multi-model architecture with:

- per-model protocol profiles
- dynamic HA capability exposure
- config persistence by model
- backward compatibility for older config entries
- deployed and restarted HAOS runtime
