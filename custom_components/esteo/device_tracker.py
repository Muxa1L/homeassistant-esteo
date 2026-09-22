"""Device tracker for the Esteo integration (vehicle location)."""

from __future__ import annotations

from homeassistant.components.device_tracker import (
    SourceType,
    TrackerEntity,
)
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
    """Set up the Esteo device tracker from a config entry."""
    coordinator: EsteoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EsteoVehicleTracker(coordinator)])


class EsteoVehicleTracker(EsteoVehicleEntity, TrackerEntity):
    """Track the vehicle via the TSP realtime GPS data."""

    _attr_icon = "mdi:car"

    def __init__(self, coordinator: EsteoCoordinator) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator, "tracker")

    @property
    def latitude(self) -> float | None:
        """Return the latitude."""
        gps = self.coordinator.data.gps if self.coordinator.data else None
        return gps.latitude if gps else None

    @property
    def longitude(self) -> float | None:
        """Return the longitude."""
        gps = self.coordinator.data.gps if self.coordinator.data else None
        return gps.longitude if gps else None

    @property
    def location_accuracy(self) -> int:
        """GPS-based position → accuracy 0 (exact)."""
        return 0

    @property
    def source_type(self) -> SourceType:
        """The source of the position: GPS."""
        return SourceType.GPS

    @property
    def extra_state_attributes(self) -> dict:
        """GPS details from the data pool."""
        attrs = dict(super().extra_state_attributes)
        if (gps := self.coordinator.data.gps if self.coordinator.data else None):
            attrs.update(
                {
                    "gps_valid": gps.valid,
                    "gps_speed": gps.speed,
                    "heading": gps.heading,
                    "altitude": gps.altitude,
                    "satellites": gps.satellites,
                }
            )
        return attrs