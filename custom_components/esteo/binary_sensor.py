"""Binary sensors for the Esteo integration (read-only status)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EsteoCoordinator
from .entity import EsteoVehicleEntity


def _get_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> EsteoCoordinator:
    return hass.data[DOMAIN][entry.entry_id]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Esteo binary sensors from a config entry."""
    coordinator = _get_coordinator(hass, entry)

    def _getter(attr: str):
        def get(state) -> Any:
            return getattr(state, attr, None)

        return get

    sensors: list[EsteoBinarySensor] = [
        EsteoBinarySensor(coordinator, "engine", "Engine", BinarySensorDeviceClass.POWER, _getter("engine_on"), on_icon="mdi:engine", off_icon="mdi:engine-off"),
        EsteoBinarySensor(coordinator, "online", "Online", None, _getter("online"), on_icon="mdi:check-circle", off_icon="mdi:close-circle"),
        EsteoBinarySensor(coordinator, "alarm", "Alarm", BinarySensorDeviceClass.SAFETY, _getter("alarm"), on_icon="mdi:alarm-light", off_icon="mdi:alarm-light-outline"),
        EsteoBinarySensor(coordinator, "anti_theft", "Anti-theft armed", BinarySensorDeviceClass.LOCK, _getter("anti_theft")),
        EsteoBinarySensor(coordinator, "charge_gun", "Charge gun connected", BinarySensorDeviceClass.PLUG, _getter("charge_gun_connected")),
        EsteoBinarySensor(coordinator, "hood", "Hood open", BinarySensorDeviceClass.OPENING, _getter("hood_open")),
        EsteoBinarySensor(coordinator, "trunk", "Trunk open", BinarySensorDeviceClass.OPENING, _getter("trunk_open")),
        EsteoBinarySensor(coordinator, "door_front_left", "Front left door open", BinarySensorDeviceClass.OPENING, _getter("door_fl_open")),
        EsteoBinarySensor(coordinator, "door_front_right", "Front right door open", BinarySensorDeviceClass.OPENING, _getter("door_fr_open")),
        EsteoBinarySensor(coordinator, "door_rear_left", "Rear left door open", BinarySensorDeviceClass.OPENING, _getter("door_rl_open")),
        EsteoBinarySensor(coordinator, "door_rear_right", "Rear right door open", BinarySensorDeviceClass.OPENING, _getter("door_rr_open")),
        EsteoBinarySensor(coordinator, "window_front_left", "Front left window open", BinarySensorDeviceClass.OPENING, _getter("window_fl_open")),
        EsteoBinarySensor(coordinator, "window_front_right", "Front right window open", BinarySensorDeviceClass.OPENING, _getter("window_fr_open")),
        EsteoBinarySensor(coordinator, "window_rear_left", "Rear left window open", BinarySensorDeviceClass.OPENING, _getter("window_rl_open")),
        EsteoBinarySensor(coordinator, "window_rear_right", "Rear right window open", BinarySensorDeviceClass.OPENING, _getter("window_rr_open")),
        EsteoBinarySensor(coordinator, "sunroof", "Sunroof open", BinarySensorDeviceClass.OPENING, _getter("sunroof_open")),
    ]
    async_add_entities(sensors)


class EsteoBinarySensor(EsteoVehicleEntity, BinarySensorEntity):
    """A single read-only binary sensor derived from the vehicle state."""

    def __init__(
        self,
        coordinator: EsteoCoordinator,
        key: str,
        name: str,
        device_class: BinarySensorDeviceClass | None,
        value_getter,
        on_icon: str | None = None,
        off_icon: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, key)
        self._attr_translation_key = None
        self._attr_name = name
        self._attr_device_class = device_class
        self._value_getter = value_getter
        self._on_icon = on_icon
        self._off_icon = off_icon

    @property
    def is_on(self) -> bool | None:
        """Return the state (None if unknown)."""
        if self.coordinator.data is None:
            return None
        return self._value_getter(self.coordinator.data)

    @property
    def icon(self) -> str | None:
        """Icon override for sensors without a device class."""
        if self._on_icon and self._off_icon:
            return self._on_icon if self.is_on else self._off_icon
        return None