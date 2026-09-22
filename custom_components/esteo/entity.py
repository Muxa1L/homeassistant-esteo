"""Shared entity base for Esteo vehicles."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import EsteoCoordinator


class EsteoVehicleEntity(CoordinatorEntity[EsteoCoordinator]):
    """Base entity for one Esteo vehicle."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EsteoCoordinator,
        key: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        vin = coordinator.vin
        self._attr_unique_id = f"{vin}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vin)},
            name=coordinator.vehicle_name,
            manufacturer=MANUFACTURER,
            serial_number=vin,
            model="Chery telematics vehicle",
        )

    @property
    def available(self) -> bool:
        """Entity availability — missing state means unavailable."""
        return self.coordinator.last_update_success and self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict:
        """Common attributes."""
        state = self.coordinator.data
        attrs = {"vin": self.coordinator.vin}
        if state is not None and state.timestamp:
            attrs["data_time"] = state.timestamp
        return attrs