"""Config flow for the Esteo integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    CheryTspClient,
    EsteoClient,
    HydraAuthError,
    login_with_credentials,
)
from .const import (
    AUTH_METHOD_DIRECT,
    AUTH_METHOD_OAUTH,
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_AUTH_METHOD,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_TASK_ID,
    CONF_USER_TOKEN,
    CONF_VEHICLE_NAME,
    CONF_VIN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _vehicle_title(vehicle: dict[str, Any], vin: str) -> str:
    """Best-effort human name for a garage entry."""
    for key in ("name", "title", "modelName", "model", "brandModel"):
        value = vehicle.get(key)
        if value:
            return str(value)
    brand = vehicle.get("brand") or ""
    model = vehicle.get("model") or ""
    if brand or model:
        return f"{brand} {model}".strip()
    return vin


def _vehicle_vin(vehicle: dict[str, Any]) -> str | None:
    for key in ("vin", "VIN", "vehicleVin"):
        value = vehicle.get(key)
        if value:
            return str(value)
    return None


class EsteoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Esteo config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._tokens: dict[str, Any] | None = None
        self._vehicles: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: choose the authentication method."""
        if user_input is not None:
            method = user_input[CONF_AUTH_METHOD]
            if method == AUTH_METHOD_OAUTH:
                return await self.async_step_credentials()
            return await self.async_step_direct()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AUTH_METHOD): vol.In(
                        {
                            AUTH_METHOD_OAUTH: "Esteo account (phone + password)",
                            AUTH_METHOD_DIRECT: "Direct TSP token (advanced)",
                        }
                    )
                }
            ),
        )

    # ------------------------------------------------------------------
    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2a: headless login with phone + password (no browser)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = user_input["phone"].strip()
            password = user_input["password"]
            # Need a session with unsafe cookie jar (redirect chain goes via HTTP)
            import aiohttp as _aiohttp

            jar = _aiohttp.CookieJar(unsafe=True)
            timeout = _aiohttp.ClientTimeout(total=30)
            try:
                async with _aiohttp.ClientSession(
                    cookie_jar=jar, timeout=timeout
                ) as login_session:
                    self._tokens = await login_with_credentials(
                        login_session, phone, password
                    )
                return await self._async_after_tokens()
            except HydraAuthError as err:
                _LOGGER.warning("Login failed: %s", err)
                errors["base"] = "login_failed"
            except (_aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.warning("Network error during login: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="credentials",
            data_schema=vol.Schema(
                {
                    vol.Required("phone"): str,
                    vol.Required("password"): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "phone_hint": "+7 9XX XXX-XX-XX",
            },
        )

    async def _async_after_tokens(self) -> FlowResult:
        """Exchange OAuth tokens for the garage list."""
        session = async_get_clientsession(self.hass)
        esteo = EsteoClient(session, lambda: self._tokens or {}, None)
        try:
            vehicles = await esteo.get_vehicles()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to fetch vehicles: %s", err)
            return self.async_abort(reason="no_vehicles")
        if not vehicles:
            return self.async_abort(reason="no_vehicles")
        self._vehicles = vehicles

        if len(vehicles) == 1:
            vin = _vehicle_vin(vehicles[0])
            if vin:
                return await self._async_finish(vin, vehicles[0])
        return await self.async_step_select_vehicle()

    async def async_step_select_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2a-2: pick a car from the garage."""
        if user_input is not None:
            vin = user_input[CONF_VIN]
            vehicle = next(
                (v for v in self._vehicles if _vehicle_vin(v) == vin), {}
            )
            return await self._async_finish(vin, vehicle)

        vin_map = {}
        for vehicle in self._vehicles:
            vin = _vehicle_vin(vehicle)
            if vin:
                vin_map[vin] = _vehicle_title(vehicle, vin)
        return self.async_show_form(
            step_id="select_vehicle",
            data_schema=vol.Schema({vol.Required(CONF_VIN): vol.In(vin_map)}),
        )

    async def _async_finish(self, vin: str, vehicle: dict[str, Any]) -> FlowResult:
        """Fetch TSP credentials for the chosen car and create the entry."""
        session = async_get_clientsession(self.hass)
        esteo = EsteoClient(session, lambda: self._tokens or {}, None)
        user_token = ""
        account_id = None
        task_id = None
        try:
            creds = await esteo.login_tsp()
            user_token = creds.get("userToken") or ""
            account_id = creds.get("accountId")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "TSP login during setup failed (will retry at runtime): %s", err
            )
        try:
            task = await esteo.get_task(vin)
            task_id = task.get("taskId")
        except Exception:  # noqa: BLE001
            pass

        if not user_token:
            return self.async_abort(reason="tsp_login_failed")

        data = {
            CONF_AUTH_METHOD: AUTH_METHOD_OAUTH,
            **(self._tokens or {}),
            CONF_VIN: vin,
            CONF_VEHICLE_NAME: _vehicle_title(vehicle, vin),
            CONF_USER_TOKEN: user_token,
            CONF_ACCOUNT_ID: account_id,
            CONF_TASK_ID: task_id,
        }
        await self._async_set_unique_id(vin)
        return self.async_create_entry(title=data[CONF_VEHICLE_NAME], data=data)

    # ------------------------------------------------------------------
    async def async_step_direct(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2b: direct TSP token (advanced)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            vin = user_input[CONF_VIN].strip().upper()
            user_token = user_input[CONF_USER_TOKEN].strip()
            session = async_get_clientsession(self.hass)
            tsp = CheryTspClient(session, vin, lambda: user_token)
            try:
                raw = await tsp.realtime()
                if not raw:
                    errors["base"] = "tsp_empty_state"
                else:
                    await self._async_set_unique_id(vin)
                    return self.async_create_entry(
                        title=f"Chery {vin[-6:]}",
                        data={
                            CONF_AUTH_METHOD: AUTH_METHOD_DIRECT,
                            CONF_VIN: vin,
                            CONF_USER_TOKEN: user_token,
                            CONF_ACCOUNT_ID: (user_input.get(CONF_ACCOUNT_ID) or "").strip() or None,
                            CONF_VEHICLE_NAME: f"Chery {vin[-6:]}",
                        },
                    )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Direct token validation failed: %s", err)
                errors["base"] = "tsp_validation_failed"

        return self.async_show_form(
            step_id="direct",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VIN): str,
                    vol.Required(CONF_USER_TOKEN): str,
                    vol.Optional(CONF_ACCOUNT_ID): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EsteoOptionsFlow:
        """Return the options flow handler."""
        return EsteoOptionsFlow(config_entry)

class EsteoOptionsFlow(config_entries.OptionsFlow):
    """Options: polling interval, and TSP token refresh (direct mode)."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            scan_interval = max(60, int(user_input[CONF_SCAN_INTERVAL]))

            # direct mode: allow replacing the TSP token
            if self._entry.data.get(CONF_AUTH_METHOD) == AUTH_METHOD_DIRECT:
                token = (user_input.get(CONF_USER_TOKEN) or "").strip()
                if token and token != self._entry.data.get(CONF_USER_TOKEN):
                    new_data = dict(self._entry.data)
                    new_data[CONF_USER_TOKEN] = token
                    self.hass.config_entries.async_update_entry(
                        self._entry, data=new_data
                    )

            # oauth mode: refresh the TSP userToken with the stored OAuth token
            if self._entry.data.get(CONF_AUTH_METHOD) == AUTH_METHOD_OAUTH:
                session = async_get_clientsession(self.hass)
                esteo = EsteoClient(
                    session,
                    lambda: {
                        CONF_ACCESS_TOKEN: self._entry.data.get(CONF_ACCESS_TOKEN),
                        CONF_REFRESH_TOKEN: self._entry.data.get(CONF_REFRESH_TOKEN),
                        CONF_EXPIRES_AT: self._entry.data.get(CONF_EXPIRES_AT),
                    },
                )
                try:
                    creds = await esteo.login_tsp()
                    user_token = creds.get("userToken")
                    if user_token:
                        new_data = dict(self._entry.data)
                        new_data[CONF_USER_TOKEN] = user_token
                        if creds.get("accountId") is not None:
                            new_data[CONF_ACCOUNT_ID] = creds["accountId"]
                        self.hass.config_entries.async_update_entry(
                            self._entry, data=new_data
                        )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Could not refresh TSP token in options: %s", err)

            return self.async_create_entry(
                title="", data={CONF_SCAN_INTERVAL: scan_interval}
            )

        schema: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=self._entry.options.get(
                    CONF_SCAN_INTERVAL,
                    self._entry.data.get(CONF_SCAN_INTERVAL, 300),
                ),
            ): int,
        }
        if self._entry.data.get(CONF_AUTH_METHOD) == AUTH_METHOD_DIRECT:
            schema[
                vol.Optional(
                    CONF_USER_TOKEN,
                    description={
                        "suggested_value": self._entry.data.get(CONF_USER_TOKEN)
                    },
                )
            ] = str

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema), errors=errors
        )