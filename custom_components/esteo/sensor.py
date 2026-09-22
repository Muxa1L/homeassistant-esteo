"""Sensors for the Esteo integration (read-only status)."""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
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
    """Set up Esteo sensors from a config entry."""
    coordinator: EsteoCoordinator = hass.data[DOMAIN][entry.entry_id]

    sensors: list[EsteoSensor] = [
        EsteoSensor(
            coordinator, "battery", "Battery",
            lambda s: s.soc,
            native_unit=PERCENTAGE,
            device_class=SensorDeviceClass.BATTERY,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:car-battery",
        ),
        EsteoSensor(
            coordinator, "fuel", "Fuel level",
            lambda s: s.fuel_level,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:gas-station",
        ),
        EsteoSensor(
            coordinator, "range", "Range",
            lambda s: s.range,
            native_unit=UnitOfLength.KILOMETERS,
            device_class=SensorDeviceClass.DISTANCE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:map-marker-distance",
            extra=lambda s: {
                "range_source": s.range_source,
                "range_unit": s.raw.get("rangeUnit"),
            },
        ),
        EsteoSensor(
            coordinator, "odometer", "Odometer",
            lambda s: s.odometer,
            native_unit=UnitOfLength.KILOMETERS,
            device_class=SensorDeviceClass.DISTANCE,
            state_class=SensorStateClass.TOTAL_INCREASING,
            icon="mdi:counter",
        ),
        EsteoSensor(
            coordinator, "interior_temperature", "Interior temperature",
            lambda s: s.interior_temperature,
            native_unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EsteoSensor(
            coordinator, "average_speed", "Average speed",
            lambda s: s.average_speed,
            native_unit=UnitOfSpeed.KILOMETERS_PER_HOUR,
            device_class=SensorDeviceClass.SPEED,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:speedometer",
        ),
        EsteoSensor(
            coordinator, "average_fuel", "Average fuel consumption",
            lambda s: s.average_fuel,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:fuel",
        ),
        EsteoSensor(
            coordinator, "charging_power", "Charging power",
            lambda s: s.charging_power,
            native_unit=UnitOfPower.KILO_WATT,
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:ev-station",
        ),
        EsteoSensor(
            coordinator, "remain_charge_time", "Remaining charge time",
            lambda s: s.remain_charge_time,
            native_unit=UnitOfTime.MINUTES,
            device_class=SensorDeviceClass.DURATION,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:timer",
        ),
        EsteoSensor(
            coordinator, "tire_pressure_front_left", "Tire pressure front left",
            lambda s: s.tire_pressure_fl,
            native_unit=UnitOfPressure.KPA,
            device_class=SensorDeviceClass.PRESSURE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:car-tire-alert",
        ),
        EsteoSensor(
            coordinator, "tire_pressure_front_right", "Tire pressure front right",
            lambda s: s.tire_pressure_fr,
            native_unit=UnitOfPressure.KPA,
            device_class=SensorDeviceClass.PRESSURE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:car-tire-alert",
        ),
        EsteoSensor(
            coordinator, "tire_pressure_rear_left", "Tire pressure rear left",
            lambda s: s.tire_pressure_rl,
            native_unit=UnitOfPressure.KPA,
            device_class=SensorDeviceClass.PRESSURE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:car-tire-alert",
        ),
        EsteoSensor(
            coordinator, "tire_pressure_rear_right", "Tire pressure rear right",
            lambda s: s.tire_pressure_rr,
            native_unit=UnitOfPressure.KPA,
            device_class=SensorDeviceClass.PRESSURE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:car-tire-alert",
        ),
        EsteoSensor(
            coordinator, "tire_temp_front_left", "Tire temperature front left",
            lambda s: s.tire_temp_fl,
            native_unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:thermometer",
        ),
        EsteoSensor(
            coordinator, "tire_temp_front_right", "Tire temperature front right",
            lambda s: s.tire_temp_fr,
            native_unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:thermometer",
        ),
        EsteoSensor(
            coordinator, "tire_temp_rear_left", "Tire temperature rear left",
            lambda s: s.tire_temp_rl,
            native_unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:thermometer",
        ),
        EsteoSensor(
            coordinator, "tire_temp_rear_right", "Tire temperature rear right",
            lambda s: s.tire_temp_rr,
            native_unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:thermometer",
        ),
        EsteoSensor(
            coordinator, "charge_state", "Charge state",
            lambda s: s.charge_state,
            icon="mdi:ev-station",
        ),
        # Diagnostic: raw data pool for calibration / debugging
        EsteoRawStateSensor(coordinator, "raw_state", "Raw state"),
    ]
    async_add_entities(sensors)

class EsteoSensor(EsteoVehicleEntity, SensorEntity):
    """A single read-only sensor derived from the vehicle state."""

    def __init__(
        self,
        coordinator: EsteoCoordinator,
        key: str,
        name: str,
        value_getter: Callable[[Any], Any],
        *,
        native_unit: str | None = None,
        device_class: SensorDeviceClass | None = None,
        state_class: SensorStateClass | None = None,
        icon: str | None = None,
        extra: Callable[[Any], dict] | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_native_unit_of_measurement = native_unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_icon = icon
        self._value_getter = value_getter
        self._extra = extra

    @property
    def native_value(self) -> Any:
        """Return the sensor value (None if unknown)."""
        if self.coordinator.data is None:
            return None
        return self._value_getter(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict:
        """Add per-sensor attributes on top of the common ones."""
        attrs = dict(super().extra_state_attributes)
        if self._extra and self.coordinator.data is not None:
            attrs.update(self._extra(self.coordinator.data))
        return attrs


class EsteoRawStateSensor(EsteoVehicleEntity, SensorEntity):
    """Diagnostic sensor exposing the full raw data-pool as attributes."""

    _attr_entity_category = "diagnostic"
    _attr_icon = "mdi:clipboard-text"

    def __init__(
        self, coordinator: EsteoCoordinator, key: str, name: str
    ) -> None:
        """Initialize the raw-state sensor."""
        super().__init__(coordinator, key)
        self._attr_name = name

    @property
    def native_value(self) -> Any:
        """Number of fields in the latest data pool."""
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.raw)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose every raw field for calibration."""
        attrs = dict(super().extra_state_attributes)
        if self.coordinator.data is not None:
            attrs.update(self.coordinator.data.raw)
        return attrs