"""Constants for the Esteo (Chery RU telematics) integration.

All protocol constants are derived from static analysis of the
com.kodix.onives.esteo Android app — see ../API_REFERENCE.md (project root).
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "esteo"
MANUFACTURER: Final = "Chery (Esteo)"

# ---------------------------------------------------------------------------
# Esteo cloud (Hydra OAuth2 + backend REST)
# ---------------------------------------------------------------------------
HYDRA_BASE_URL: Final = "https://idp.prod.esteo.ru"
HYDRA_CLIENT_ID: Final = "43962376-cd37-48e3-8df9-c28077e5fc17"
HYDRA_AUDIENCE: Final = "com.kodix.onives.esteo"
HYDRA_REDIRECT_URI: Final = "https://app.omoda.dev/auth"
HYDRA_SCOPE: Final = "offline_access openid"
HYDRA_BRAND: Final = "esteo"

API_BASE_URL: Final = "https://backend.prod.esteo.ru"

# ---------------------------------------------------------------------------
# Chery TSP (RU production)
# ---------------------------------------------------------------------------
TSP_BASE_URL: Final = "https://tspconsole.chery.ru"
TSP_APP_ID: Final = "ru-1"
TSP_APP_SECRET: Final = "RQUIP7rCoYd83Hd7792TbX498M4U094Ma9fA4D445A03a7665302"
TSP_SUCCESS_CODE: Final = "000000"
TSP_COMMAND_ACCEPTED_CODE: Final = "A00079"
TSP_CHANNEL_ID: Final = 10
MQTT_PASSWORD_SALT: Final = "fa89db3abe8045919d70c6ed3cc65bc5"

# ---------------------------------------------------------------------------
# Config entry data keys
# ---------------------------------------------------------------------------
CONF_AUTH_METHOD: Final = "auth_method"
AUTH_METHOD_OAUTH: Final = "oauth"
AUTH_METHOD_DIRECT: Final = "direct"

CONF_ACCESS_TOKEN: Final = "access_token"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_EXPIRES_AT: Final = "expires_at"
CONF_USER_TOKEN: Final = "user_token"  # TSP token from /chery/account/login
CONF_ACCOUNT_ID: Final = "account_id"
CONF_VIN: Final = "vin"
CONF_TASK_ID: Final = "task_id"
CONF_CONTROL_PIN: Final = "control_pin"
CONF_VEHICLE_NAME: Final = "vehicle_name"

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL_SEC: Final = 300
MIN_SCAN_INTERVAL_SEC: Final = 60

# ---------------------------------------------------------------------------
# Raw flag → boolean mappings
#
# IMPORTANT: these polarities are best-effort assumptions based on the Chery
# SDK conventions ("1" = active/open/on) and have NOT yet been verified
# against live data. The raw values are always exposed as attributes on the
# diagnostic sensor (`*_raw_state`) so they can be calibrated against the
# mobile app and flipped here if needed.
# ---------------------------------------------------------------------------
VALUE_TRUE: Final = "1"
VALUE_FALSE: Final = "0"

# door/window fields where "1" means OPEN (assumption — verify)
OPEN_FLAGS: Final = (
    "frontLeftDoor",
    "frontRightDoor",
    "backLeftDoor",
    "backRightDoor",
    "hood",
    "trunkDoor",
    "frontLeftWindowState",
    "frontRightWindowState",
    "backLeftWindowState",
    "backRightWindowState",
    "sunroofState",
    "chargeGunState",
    "biopsyAlarm",
    "engineState",
)

# fields where "0" means LOCKED/ENABLED and "1" means unlocked/disabled
# (doorLock confirmed inverted against live data: "0" = locked)
LOCKED_FLAGS: Final = (
    "doorLock",
    "trunkLock",
    "antiThftState",
)

# TSP response codes that hint at an authorization problem (token expired).
# Exact codes are not statically known; these are common shapes seen in the
# Chery stack. Any non-success response triggers exactly one re-login+retry.
TSP_AUTH_HINT_CODES: Final = {
    "A00001",
    "A00002",
    "A00003",
    "A00004",
    "A00005",
    "A10001",
    "A10002",
    "A10003",
    "401",
    "401000",
}