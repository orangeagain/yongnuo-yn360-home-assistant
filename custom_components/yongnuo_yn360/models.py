from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.core import HomeAssistant

from .const import DEFAULT_MODEL, MODEL_YN150, MODEL_YN150RGB, MODEL_YN360

_NON_ALNUM_RE = re.compile(r"[^0-9A-Z]+")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class YongnuoModelProfile:
    key: str
    label: str
    device_model: str
    supports_rgb: bool
    supports_color_temp: bool
    color_temp_channel: int | None


MODEL_PROFILES: dict[str, YongnuoModelProfile] = {
    MODEL_YN360: YongnuoModelProfile(
        key=MODEL_YN360,
        label="YN360",
        device_model="YN360 LED video light",
        supports_rgb=True,
        supports_color_temp=True,
        color_temp_channel=0x01,
    ),
    MODEL_YN150: YongnuoModelProfile(
        key=MODEL_YN150,
        label="YN150WY",
        device_model="YN150 WY bi-color LED video light",
        supports_rgb=False,
        supports_color_temp=True,
        color_temp_channel=0x0A,
    ),
    MODEL_YN150RGB: YongnuoModelProfile(
        key=MODEL_YN150RGB,
        label="YN150Ultra RGB",
        device_model="YN150 Ultra RGB LED video light",
        supports_rgb=True,
        supports_color_temp=True,
        color_temp_channel=0x00,
    ),
}

def get_model_profile(model: str | None) -> YongnuoModelProfile:
    return MODEL_PROFILES.get((model or "").strip().lower(), MODEL_PROFILES[DEFAULT_MODEL])


def get_discovery_name(info: Any) -> str:
    name = (getattr(info, "name", None) or "").strip()
    if name:
        return name

    advertisement = getattr(info, "advertisement", None)
    return (getattr(advertisement, "local_name", None) or "").strip()


def normalize_name(name: str | None) -> str:
    return _NON_ALNUM_RE.sub("", (name or "").upper())


def is_likely_yongnuo_name(name: str | None) -> bool:
    normalized = normalize_name(name)
    return normalized.startswith("YN") or normalized.startswith("YONGNUO")


def guess_model_from_name(name: str | None) -> str:
    if not name:
        return DEFAULT_MODEL

    normalized = normalize_name(name)

    if "150" in normalized and "WY" in normalized:
        return MODEL_YN150

    if "150" in normalized and ("RGB" in normalized or "ULTRA" in normalized):
        return MODEL_YN150RGB

    if normalized.startswith("YN150"):
        return MODEL_YN150

    return MODEL_YN360


def guess_model_from_discovery_info(info: Any) -> str:
    return guess_model_from_name(get_discovery_name(info))


def get_discovery_info_for_address(hass: HomeAssistant, address: str) -> Any | None:
    normalized_address = address.strip().upper()
    fallback_info = None

    for info in async_discovered_service_info(hass):
        if info.address != normalized_address:
            continue

        if get_discovery_name(info):
            return info

        if fallback_info is None:
            fallback_info = info

    return fallback_info


async def async_detect_model_for_address(hass: HomeAssistant, address: str) -> str | None:
    info = get_discovery_info_for_address(hass, address)
    if info is None:
        _LOGGER.debug("Model detection skipped for %s: no discovery info", address)
        return None

    name = get_discovery_name(info)
    if not name:
        _LOGGER.debug("Model detection skipped for %s: discovery info has no name", address)
        return None

    model = guess_model_from_discovery_info(info)
    _LOGGER.debug(
        "Detected model for %s from BLE name %r -> %s",
        address,
        name,
        get_model_profile(model).label,
    )
    return model


async def async_guess_model_for_address(hass: HomeAssistant, address: str) -> str:
    return await async_detect_model_for_address(hass, address) or DEFAULT_MODEL
