"""Climate entity for the Esteo integration (remote A/C control)."""

from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
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
    """Set up Esteo climate from a config entry."""
    coordinator: EsteoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EsteoClimate(coordinator)])


class EsteoClimate(EsteoVehicleEntity, ClimateEntity):
    """Remote climate control via TSP airControl command."""

    _attr_icon = "mdi:car-seat-heater"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 16.0
    _attr_max_temp = 30.0
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: EsteoCoordinator) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, "climate")
        self._target_temp = 22.0

    @property
    def current_temperature(self) -> float | None:
        """Return the interior temperature."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.interior_temperature

    @property
    def target_temperature(self) -> float:
        """Return the target temperature."""
        return self._target_temp

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        if self.coordinator.data and self.coordinator.data.raw.get("frontHVACState") == "1":
            return HVACMode.AUTO
        return HVACMode.OFF

    async def async_set_temperature(self, **kwargs) -> None:
        """Set the target temperature."""
        temp = kwargs.get("temperature")
        if temp is not None:
            self._target_temp = float(temp)
        if self.hvac_mode != HVACMode.OFF:
            await self.coordinator.execute_command(
                lambda: self.coordinator._tsp.control_climate(
                    True, self._target_temp, 10
                )
            )
            await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode."""
        if hvac_mode == HVACMode.AUTO:
            await self.coordinator.execute_command(
                lambda: self.coordinator._tsp.control_climate(
                    True, self._target_temp, 10
                )
            )
        else:
            await self.coordinator.execute_command(
                lambda: self.coordinator._tsp.control_climate(False)
            )
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn the climate on."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Turn the climate off."""
        await self.async_set_hvac_mode(HVACMode.OFF)