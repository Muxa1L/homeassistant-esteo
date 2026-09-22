"""Lock entity for the Esteo integration (door lock/unlock)."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
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
    """Set up Esteo lock from a config entry."""
    coordinator: EsteoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EsteoDoorLock(coordinator)])


class EsteoDoorLock(EsteoVehicleEntity, LockEntity):
    """Door lock entity — lock/unlock via TSP command."""

    _attr_icon = "mdi:car-door-lock"

    def __init__(self, coordinator: EsteoCoordinator) -> None:
        """Initialize the lock."""
        super().__init__(coordinator, "door_lock")

    @property
    def is_locked(self) -> bool | None:
        """Return True if doors are locked."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.doors_locked

    @property
    def code_format(self) -> str | None:
        """Return the code format if a control PIN is needed."""
        return None  # PIN is stored in config, not entered per-action

    async def async_lock(self, **kwargs) -> None:
        """Lock the doors."""
        await self.coordinator.execute_command(self.coordinator._tsp.lock_doors())
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs) -> None:
        """Unlock the doors."""
        await self.coordinator.execute_command(self.coordinator._tsp.unlock_doors())
        await self.coordinator.async_request_refresh()