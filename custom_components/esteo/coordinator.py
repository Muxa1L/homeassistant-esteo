"""DataUpdateCoordinator for the Esteo integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CheryTspClient,
    EsteoApiError,
    EsteoClient,
    HydraAuthError,
    TspError,
)
from .const import (
    AUTH_METHOD_DIRECT,
    AUTH_METHOD_OAUTH,
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_USER_TOKEN,
    CONF_VEHICLE_NAME,
    CONF_VIN,
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    MIN_SCAN_INTERVAL_SEC,
)
from .models import VehicleState

_LOGGER = logging.getLogger(__name__)


class EsteoCoordinator(DataUpdateCoordinator[VehicleState]):
    """Poll the Chery TSP realtime state for one vehicle."""

    def __init__(self, hass: HomeAssistant, entry_data: dict[str, Any], scan_interval: int) -> None:
        """Initialize the coordinator.

        entry_data is the config-entry data dict:
          - oauth method: access_token/refresh_token/expires_at + vin (+ user_token cache)
          - direct method: user_token + vin
        """
        self._hass = hass
        self._entry_data = entry_data
        self._auth_method = entry_data.get("auth_method", AUTH_METHOD_OAUTH)
        self.vin: str = entry_data[CONF_VIN]
        self.vehicle_name: str = entry_data.get(CONF_VEHICLE_NAME) or self.vin
        self.account_id: str | None = entry_data.get(CONF_ACCOUNT_ID)

        session = async_get_clientsession(hass)
        self._session = session

        self._esteo: EsteoClient | None = None
        if self._auth_method == AUTH_METHOD_OAUTH:
            self._esteo = EsteoClient(
                session,
                token_provider=lambda: {
                    CONF_ACCESS_TOKEN: self._entry_data.get(CONF_ACCESS_TOKEN),
                    CONF_REFRESH_TOKEN: self._entry_data.get(CONF_REFRESH_TOKEN),
                    CONF_EXPIRES_AT: self._entry_data.get(CONF_EXPIRES_AT),
                },
                token_updater=self._store_oauth_tokens,
            )

        self._tsp = CheryTspClient(
            session,
            vin=self.vin,
            token_provider=lambda: self._entry_data.get(CONF_USER_TOKEN, ""),
            relogin_handler=self._relogin_tsp,
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.vin}",
            update_interval=timedelta(
                seconds=max(scan_interval, MIN_SCAN_INTERVAL_SEC)
            ),
        )

    # ------------------------------------------------------------------
    # credential handling
    # ------------------------------------------------------------------
    def _store_oauth_tokens(self, tokens: dict[str, Any]) -> None:
        """Persist refreshed OAuth tokens into the config entry data."""
        self._entry_data.update(
            {
                CONF_ACCESS_TOKEN: tokens["access_token"],
                CONF_REFRESH_TOKEN: tokens["refresh_token"],
                CONF_EXPIRES_AT: tokens["expires_at"],
            }
        )
        # ask HA to persist (entry object set after coordinator construction)
        if (updater := getattr(self, "_entry_update", None)) is not None:
            updater()

    def set_entry_update_callback(self, cb) -> None:
        """Register a callback that persists entry.data changes."""
        self._entry_update = cb

    async def _relogin_tsp(self) -> None:
        """Refresh TSP credentials (called by the TSP client on auth errors)."""
        if self._esteo is None:
            raise TspError(
                None,
                "TSP token was rejected; using 'direct token' setup — "
                "update the token in integration settings.",
            )
        creds = await self._esteo.login_tsp()
        user_token = creds.get("userToken")
        if not user_token:
            raise UpdateFailed(f"TSP re-login returned no userToken: {creds!r:.200}")
        self._entry_data[CONF_USER_TOKEN] = user_token
        if creds.get("accountId") is not None:
            self._entry_data[CONF_ACCOUNT_ID] = self.account_id = creds["accountId"]
        if (updater := getattr(self, "_entry_update", None)) is not None:
            updater()
        _LOGGER.debug("TSP token refreshed for %s", self.vin)

    async def ensure_tsp_credentials(self) -> None:
        """Fetch TSP credentials on startup if not cached yet."""
        if self._entry_data.get(CONF_USER_TOKEN) or self._esteo is None:
            return
        await self._relogin_tsp()

    # ------------------------------------------------------------------
    # polling
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> VehicleState:
        try:
            await self.ensure_tsp_credentials()
            raw = await self._tsp.realtime()
            if not raw:
                raise UpdateFailed("TSP returned an empty state body")
            return VehicleState.from_data_pool(raw)
        except HydraAuthError as err:
            raise UpdateFailed(f"Esteo authentication failed: {err}") from err
        except EsteoApiError as err:
            raise UpdateFailed(f"Esteo API error: {err}") from err
        except TspError as err:
            raise UpdateFailed(f"Chery TSP error: {err}") from err

    async def async_unload(self) -> None:
        """Best-effort TSP logout (only for OAuth method)."""
        if self._esteo is not None:
            try:
                await self._esteo.logout_tsp()
            except Exception:  # noqa: BLE001
                pass