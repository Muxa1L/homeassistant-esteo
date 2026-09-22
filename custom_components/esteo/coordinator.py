"""DataUpdateCoordinator for the Esteo integration."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntryAuthFailed

from .api import (
    CheryTspClient,
    EsteoApiError,
    EsteoClient,
    EsteoReauthRequired,
    HydraAuthError,
    TspError,
    login_with_credentials,
)
from .const import (
    AUTH_METHOD_DIRECT,
    AUTH_METHOD_OAUTH,
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_CONTROL_PIN,
    CONF_EXPIRES_AT,
    CONF_PASSWORD,
    CONF_PHONE,
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

    def __init__(
        self,
        hass: HomeAssistant,
        entry_data: dict[str, Any],
        scan_interval: int,
        config_entry: Any = None,
    ) -> None:
        """Initialize the coordinator.

        entry_data is the config-entry data dict:
          - oauth method: access_token/refresh_token/expires_at + vin (+ user_token cache)
          - direct method: user_token + vin
        config_entry is the ConfigEntry (needed for ConfigEntryAuthFailed).
        """
        self._hass = hass
        self._entry_data = entry_data
        self.config_entry = config_entry
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
                relogin_handler=self._relogin_oauth,
            )

        self._tsp = CheryTspClient(
            session,
            vin=self.vin,
            token_provider=lambda: self._entry_data.get(CONF_USER_TOKEN, ""),
            relogin_handler=self._relogin_tsp,
        )
        self._task_id_ts: float = 0.0

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
    @property
    def has_control_pin(self) -> bool:
        """Whether a control PIN is configured."""
        return bool(self._entry_data.get(CONF_CONTROL_PIN))

    async def ensure_task_id(self) -> None:
        """Ensure a valid taskId (from PIN check) for command authorization."""
        if not self.has_control_pin:
            raise TspError(None, "No control PIN configured — add it in integration options")
        if self._tsp._task_id and (time.time() - self._task_id_ts) < 3600:
            return  # taskId still fresh (< 1 hour)
        pin = self._entry_data[CONF_CONTROL_PIN]
        # OAuth mode: use the Esteo backend endpoint; direct mode: use the TSP endpoint
        if self._esteo is not None:
            result = await self._esteo.check_control_password(self.vin, pin)
        else:
            t_user_id = None
            if self.account_id:
                try:
                    t_user_id = int(self.account_id)
                except (TypeError, ValueError):
                    t_user_id = None
            result = await self._tsp.check_password(pin, t_user_id=t_user_id)
        task_id = result.get("taskId")
        if not task_id:
            raise TspError(None, f"Control PIN check returned no taskId: {result!r:.200}")
        self._tsp.set_task_id(task_id)
        self._task_id_ts = time.time()
        _LOGGER.debug("TaskId obtained for %s", self.vin)

    async def execute_command(self, coro_factory) -> Any:
        """Execute a TSP command. coro_factory: zero-arg callable → coroutine.

        Ensures TSP credentials and a fresh taskId, then runs the command;
        on failure refreshes the taskId and retries once (a coroutine cannot
        be re-awaited, hence the factory).
        """
        await self.ensure_tsp_credentials()
        await self.ensure_task_id()
        try:
            return await coro_factory()
        except TspError as err:
            # taskId might have expired — refresh it and retry once
            _LOGGER.debug("Command failed (%s) — refreshing taskId and retrying", err.code)
            self._tsp._task_id = None
            await self.ensure_task_id()
            return await coro_factory()

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

    async def _relogin_oauth(self) -> dict[str, Any]:
        """Full phone+password re-login when the refresh token is expired.

        Creates a temporary session with unsafe cookie jar (the OAuth
        redirect chain goes through HTTP). Returns a fresh token dict and
        also refreshes the TSP credentials.
        """
        import aiohttp as _aiohttp

        phone = self._entry_data.get(CONF_PHONE)
        password = self._entry_data.get(CONF_PASSWORD)
        if not phone or not password:
            raise EsteoReauthRequired(
                "OAuth refresh token expired and no phone/password stored — "
                "re-authenticate the integration in settings."
            )
        _LOGGER.info("Performing full re-login for %s", self.vin)
        jar = _aiohttp.CookieJar(unsafe=True)
        timeout = _aiohttp.ClientTimeout(total=30)
        try:
            async with _aiohttp.ClientSession(
                cookie_jar=jar, timeout=timeout
            ) as login_session:
                tokens = await login_with_credentials(login_session, phone, password)
        except HydraAuthError as err:
            # Stored phone/password no longer valid (e.g. password changed)
            raise EsteoReauthRequired(
                f"Stored credentials were rejected during re-login: {err}"
            ) from err
        # Persist new OAuth tokens
        self._entry_data[CONF_ACCESS_TOKEN] = tokens["access_token"]
        self._entry_data[CONF_REFRESH_TOKEN] = tokens.get("refresh_token", "")
        self._entry_data[CONF_EXPIRES_AT] = tokens["expires_at"]
        # The EsteoClient now has a valid access token; refresh TSP too
        await self._relogin_tsp()
        return tokens

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
            state = VehicleState.from_data_pool(raw)
            # Best-effort: also query the location-sharing switch state
            try:
                loc = await self._tsp.query_location_sharing()
                switch = loc.get("locationSwitch")
                if switch is not None:
                    state.location_sharing_disabled = str(switch) == "1"
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Location switch query failed (ignored)")
            return state
        except EsteoReauthRequired as err:
            # Stored credentials missing/rejected → HA shows the re-auth flow
            if self.config_entry is not None:
                raise ConfigEntryAuthFailed(self.config_entry, str(err)) from err
            raise UpdateFailed(f"Esteo re-authentication required: {err}") from err
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