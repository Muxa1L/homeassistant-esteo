"""Support for Esteo (Chery RU) vehicles."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC, DOMAIN
from .coordinator import EsteoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Esteo component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an Esteo vehicle from a config entry."""
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC),
    )

    coordinator = EsteoCoordinator(
        hass,
        dict(entry.data),
        int(scan_interval),
    )
    coordinator.set_entry_update_callback(
        lambda: _persist_entry(hass, entry, coordinator._entry_data)
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_platforms(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


def _persist_entry(hass: HomeAssistant, entry: ConfigEntry, data: dict) -> None:
    """Persist coordinator-side credential updates into entry.data."""
    if entry.data == data:
        return
    hass.config_entries.async_update_entry(entry, data=dict(data))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Esteo config entry."""
    coordinator: EsteoCoordinator | None = hass.data[DOMAIN].get(entry.entry_id)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        if coordinator is not None:
            await coordinator.async_unload()
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)