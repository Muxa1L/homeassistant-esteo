"""Button entities for the Esteo integration (one-shot commands)."""

from __future__ import annotations

from typing import Any, Callable, Coroutine

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EsteoCoordinator
from .entity import EsteoVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Esteo buttons from a config entry."""
    coordinator: EsteoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EsteoButton(
            coordinator, "find_car", "Find car (honk & flash)",
            lambda c: c._tsp.find_car(),
            icon="mdi:car-search",
        ),
        EsteoButton(
            coordinator, "open_trunk", "Open trunk",
            lambda c: c._tsp.open_trunk(),
            icon="mdi:car-back",
        ),
    ])


class EsteoButton(EsteoVehicleEntity, ButtonEntity):
    """A one-shot TSP command button."""

    def __init__(
        self,
        coordinator: EsteoCoordinator,
        key: str,
        name: str,
        press_fn: Callable[[EsteoCoordinator], Coroutine],
        icon: str = "mdi:gesture-tap",
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_icon = icon
        self._press_fn = press_fn

    async def async_press(self) -> None:
        """Execute the command."""
        await self.coordinator.execute_command(self._press_fn(self.coordinator))
        await self.coordinator.async_request_refresh()