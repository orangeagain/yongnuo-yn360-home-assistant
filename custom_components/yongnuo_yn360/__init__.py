import asyncio
import logging

from .const import (
    CONF_ADDRESS,
    CONF_MODEL,
    DATA_BLE_SLOT_SEMAPHORE,
    DEFAULT_MODEL,
    DOMAIN,
    SHARED_BLE_CONNECTION_LIMIT,
)
from .models import async_detect_model_for_address, get_model_profile

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass, config):
    return True

async def async_setup_entry(hass, config_entry):
    data = dict(config_entry.data)
    configured_model = data.get(CONF_MODEL)
    detected_model = await async_detect_model_for_address(hass, data[CONF_ADDRESS])
    desired_title = None

    if detected_model:
        if configured_model and configured_model != detected_model:
            _LOGGER.info(
                "Correcting model for %s from %s to %s based on BLE name",
                data[CONF_ADDRESS],
                get_model_profile(configured_model).label,
                get_model_profile(detected_model).label,
            )
        elif configured_model is None:
            _LOGGER.debug(
                "Setting detected model for %s to %s",
                data[CONF_ADDRESS],
                get_model_profile(detected_model).label,
            )
        data[CONF_MODEL] = detected_model
        desired_title = f"{get_model_profile(detected_model).label} ({data[CONF_ADDRESS]})"
    elif configured_model is None:
        _LOGGER.debug(
            "No BLE name available for %s, falling back to default model %s",
            data[CONF_ADDRESS],
            get_model_profile(DEFAULT_MODEL).label,
        )
        data[CONF_MODEL] = DEFAULT_MODEL

    if data != dict(config_entry.data) or (
        desired_title is not None and desired_title != config_entry.title
    ):
        update_kwargs = {"data": data}
        if desired_title is not None and desired_title != config_entry.title:
            update_kwargs["title"] = desired_title
        hass.config_entries.async_update_entry(config_entry, **update_kwargs)

    hass.data.setdefault(DOMAIN, {})
    if DATA_BLE_SLOT_SEMAPHORE not in hass.data[DOMAIN]:
        hass.data[DOMAIN][DATA_BLE_SLOT_SEMAPHORE] = asyncio.Semaphore(
            SHARED_BLE_CONNECTION_LIMIT
        )
    hass.data[DOMAIN][config_entry.entry_id] = data

    await hass.config_entries.async_forward_entry_setups(config_entry, ["light"])
    return True

async def async_unload_entry(hass, config_entry):
    unloaded = await hass.config_entries.async_unload_platforms(config_entry, ["light"])
    if unloaded:
        domain_data = hass.data.get(DOMAIN)
        if domain_data is not None:
            domain_data.pop(config_entry.entry_id, None)
            if set(domain_data) == {DATA_BLE_SLOT_SEMAPHORE}:
                hass.data.pop(DOMAIN, None)
    return unloaded
