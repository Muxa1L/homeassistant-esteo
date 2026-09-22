"""Switch entities for the Esteo integration (remote control)."""

from __future__ import annotations

from typing import Any, Callable, Coroutine

from homeassistant.components.switch import SwitchEntity
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
    """Set up Esteo switches from a config entry."""
    coordinator: EsteoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EsteoSwitch(
            coordinator, "engine", "Engine (remote start)",
            lambda c: c._tsp.start_engine(10),
            lambda c: c._tsp.stop_engine(),
            value_getter=lambda s: s.engine_on,
            icon_on="mdi:engine", icon_off="mdi:engine-off",
        ),
        EsteoSwitch(
            coordinator, "windshield_defrost", "Windshield defrost",
            lambda c: c._tsp.control_windshield_defrost(True),
            lambda c: c._tsp.control_windshield_defrost(False),
            value_getter=lambda s: s.raw.get("fWinHeatingState") == "1",
            icon_on="mdi:car-defrost-front", icon_off="mdi:car-defrost-front-rear",
        ),
        EsteoSwitch(
            coordinator, "rear_defrost", "Rear defrost",
            lambda c: c._tsp.control_rear_defrost(True),
            lambda c: c._tsp.control_rear_defrost(False),
            value_getter=lambda s: s.raw.get("rWinHeatingState") == "1",
            icon_on="mdi:car-defrost-rear", icon_off="mdi:car-defrost-rear",
        ),
        EsteoSwitch(
            coordinator, "location_sharing", "Location sharing",
            lambda c: c._tsp.set_location_sharing(True),
            lambda c: c._tsp.set_location_sharing(False),
            value_getter=lambda s: s.gps.valid if s.gps else None,
            icon_on="mdi:map-marker", icon_off="mdi:map-marker-off",
        ),
    ])


class EsteoSwitch(EsteoVehicleEntity, SwitchEntity):
    """A remote-control switch backed by TSP commands."""

    def __init__(
        self,
        coordinator: EsteoCoordinator,
        key: str,
        name: str,
        turn_on_fn: Callable[[EsteoCoordinator], Coroutine],
        turn_off_fn: Callable[[EsteoCoordinator], Coroutine],
        value_getter: Callable,
        icon_on: str = "mdi:toggle-switch",
        icon_off: str = "mdi:toggle-switch-off",
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, key)
        self._attr_name = name
        self._turn_on_fn = turn_on_fn
        self._turn_off_fn = turn_off_fn
        self._value_getter = value_getter
        self._attr_icon = icon_off

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        if self.coordinator.data is None:
            return None
        return self._value_getter(self.coordinator.data)

    @property
    def icon(self) -> str | None:
        """Dynamic icon based on state."""
        if self.is_on:
            return self._attr_icon if self._attr_icon else "mdi:toggle-switch"
        # Use the off icon stored in a private attr
        return getattr(self, "_icon_off", "mdi:toggle-switch-off")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.coordinator.execute_command(self._turn_on_fn(self.coordinator))
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.coordinator.execute_command(self._turn_off_fn(self.coordinator))
        await self.coordinator.async_request_refresh()