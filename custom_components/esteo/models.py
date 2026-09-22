"""Typed vehicle-state model for the Esteo/Chery telematics integration.

The TSP realtime endpoint returns a flat JSON object with ~135 string fields
(the "data pool", API_REFERENCE.md §13). All values arrive as strings; this
module parses them into a convenient dataclass while keeping the raw dict
for diagnostics/calibration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _num(raw: dict[str, Any], key: str) -> float | None:
    """Parse a numeric data-pool value ('' / null → None)."""
    value = raw.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(raw: dict[str, Any], key: str, true_value: str = "1") -> bool | None:
    """Parse a boolean-ish data-pool value."""
    value = raw.get(key)
    if value is None or value == "":
        return None
    return str(value) == true_value


def _str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    return str(value)


@dataclass(slots=True)
class GpsInfo:
    latitude: float | None
    longitude: float | None
    valid: bool | None
    speed: float | None  # km/h (gpsSpeed)
    heading: float | None
    altitude: float | None
    satellites: float | None


@dataclass(slots=True)
class VehicleState:
    """Parsed vehicle state (read-only)."""

    raw: dict[str, Any] = field(default_factory=dict)

    # --- identity ---
    vin: str | None = None
    timestamp: str | None = None  # 'time' field (format unknown)

    # --- position ---
    gps: GpsInfo | None = None

    # --- simple numbers ---
    soc: float | None = None  # dumpEnergy (battery %, EV/PHEV)
    fuel_level: float | None = None  # oilSurplus
    range: float | None = None  # best available range estimate
    range_source: str | None = None
    odometer: float | None = None
    interior_temperature: float | None = None
    average_speed: float | None = None
    average_fuel: float | None = None
    charging_power: float | None = None
    remain_charge_time: float | None = None

    # --- tire pressures (kPa) ---
    tire_pressure_fl: float | None = None
    tire_pressure_fr: float | None = None
    tire_pressure_rl: float | None = None
    tire_pressure_rr: float | None = None
    tire_pressure_unit: str | None = None

    # --- tire temperatures (°C) ---
    tire_temp_fl: float | None = None
    tire_temp_fr: float | None = None
    tire_temp_rl: float | None = None
    tire_temp_rr: float | None = None

    # --- flags (polarity assumptions documented in const.py) ---
    engine_on: bool | None = None
    online: bool | None = None
    alarm: bool | None = None
    charge_gun_connected: bool | None = None
    hood_open: bool | None = None
    trunk_open: bool | None = None
    doors_locked: bool | None = None
    door_fl_open: bool | None = None
    door_fr_open: bool | None = None
    door_rl_open: bool | None = None
    door_rr_open: bool | None = None
    window_fl_open: bool | None = None
    window_fr_open: bool | None = None
    window_rl_open: bool | None = None
    window_rr_open: bool | None = None
    sunroof_open: bool | None = None

    # --- raw enum-ish values surfaced as text sensors ---
    charge_state: str | None = None
    online_status: str | None = None
    operating_condition: str | None = None
    sentinel_mode: str | None = None

    # --- location sharing (from /act/vehicleLocation/querySwitch, NOT the data pool) ---
    location_sharing_disabled: bool | None = None  # True = privacy mode ON

    @property
    def anti_theft(self) -> bool | None:
        """antiThftState ('1' = armed/locked — polarity assumption)."""
        return _flag(self.raw, "antiThftState")

    @classmethod
    def from_data_pool(cls, raw: dict[str, Any]) -> VehicleState:
        """Parse the raw realtime data-pool dict."""
        state = cls(raw=raw)

        state.vin = _str(raw, "vin")
        state.timestamp = _str(raw, "time")

        state.gps = GpsInfo(
            latitude=_num(raw, "lat"),
            longitude=_num(raw, "lon"),
            valid=_flag(raw, "validFlag"),
            speed=_num(raw, "gpsSpeed"),
            heading=_num(raw, "direction"),
            altitude=_num(raw, "altitude"),
            satellites=_num(raw, "sateliteNum"),
        )

        state.soc = _num(raw, "dumpEnergy")
        state.fuel_level = _num(raw, "oilSurplus")
        state.range, state.range_source = _pick_range(raw)
        state.odometer = _num(raw, "odometer")
        state.interior_temperature = _num(raw, "inCarTemperature")
        state.average_speed = _num(raw, "averageSpeed")
        state.average_fuel = _num(raw, "averageFuel")
        state.charging_power = _num(raw, "chargingPower")
        state.remain_charge_time = _num(raw, "remainChargeTime")

        state.tire_pressure_fl = _num(raw, "lFrontTyreKpa")
        state.tire_pressure_fr = _num(raw, "rFrontTyreKpa")
        state.tire_pressure_rl = _num(raw, "lRearTyreKpa")
        state.tire_pressure_rr = _num(raw, "rRearTyreKpa")
        state.tire_pressure_unit = _str(raw, "tirePressureUnit")

        state.tire_temp_fl = _num(raw, "lFrontTyreTemp")
        state.tire_temp_fr = _num(raw, "rFrontTyreTemp")
        state.tire_temp_rl = _num(raw, "lRearTyreTemp")
        state.tire_temp_rr = _num(raw, "rRearTyreTemp")

        state.engine_on = _flag(raw, "engineState")
        state.online = _flag(raw, "onlineStatus")
        state.alarm = _flag(raw, "biopsyAlarm")
        state.charge_gun_connected = _flag(raw, "chargeGunState")
        state.hood_open = _flag(raw, "hood")
        state.trunk_open = _flag(raw, "trunkDoor")
        state.doors_locked = raw.get("doorLock") == "0"  # "0" = locked, "1" = unlocked (inverted)

        state.door_fl_open = _flag(raw, "frontLeftDoor")
        state.door_fr_open = _flag(raw, "frontRightDoor")
        state.door_rl_open = _flag(raw, "backLeftDoor")
        state.door_rr_open = _flag(raw, "backRightDoor")

        state.window_fl_open = _flag(raw, "frontLeftWindowState")
        state.window_fr_open = _flag(raw, "frontRightWindowState")
        state.window_rl_open = _flag(raw, "backLeftWindowState")
        state.window_rr_open = _flag(raw, "backRightWindowState")

        state.sunroof_open = _flag(raw, "sunroofState")

        state.charge_state = _str(raw, "chargeState")
        state.online_status = _str(raw, "onlineStatus")
        state.operating_condition = _str(raw, "operatingConditionType")
        state.sentinel_mode = _str(raw, "sentinelMode")

        return state


def _pick_range(raw: dict[str, Any]) -> tuple[float | None, str | None]:
    """Pick the most appropriate range value (EV/ICE/hybrid aware)."""
    for key in (
        "dynamicCombinedRange",  # hybrid/PHEV combined
        "cruiseRange",
        "dynamicPureElectricRange",
        "wltcCombinedRange",
        "pureElectricRange",
        "electricRange",
    ):
        value = _num(raw, key)
        if value is not None:
            return value, key
    return None, None