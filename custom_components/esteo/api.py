"""Async client for the Esteo backend and the Chery TSP.

Implements the reverse-engineered protocol documented in API_REFERENCE.md:

- Ory Hydra OAuth2 Authorization Code + PKCE (manual redirect-URL paste)
- Esteo backend REST (garage, TSP credential exchange)
- Chery TSP signed HTTP API (realtime vehicle state)

This module is intentionally standalone (aiohttp only) so it can be reused
outside Home Assistant (e.g. in scripts and tests).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import time
from typing import Any, Callable

import aiohttp

from .const import (
    API_BASE_URL,
    HYDRA_AUDIENCE,
    HYDRA_BASE_URL,
    HYDRA_BRAND,
    HYDRA_CLIENT_ID,
    HYDRA_REDIRECT_URI,
    HYDRA_SCOPE,
    TSP_APP_ID,
    TSP_APP_SECRET,
    TSP_BASE_URL,
    TSP_SUCCESS_CODE,
)

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "okhttp/4.12.0"  # the app uses OkHttp; keep a plausible UA


class EsteoError(Exception):
    """Base error."""


class HydraAuthError(EsteoError):
    """OAuth token exchange/refresh failed."""


class EsteoApiError(EsteoError):
    """Esteo backend request failed."""


class TspError(EsteoError):
    """Chery TSP request failed."""

    def __init__(self, code: str | None, message: str) -> None:
        super().__init__(f"TSP error {code}: {message}")
        self.code = code


class TspAuthError(TspError):
    """TSP rejected the token (expired/invalid)."""


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(48))  # 64 chars, RFC-safe
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def build_authorize_url(state: str, code_challenge: str, locale: str = "ru") -> str:
    """Build the Hydra authorize URL exactly the way the app does."""
    from urllib.parse import urlencode

    params = {
        "access_type": "offline",
        "response_type": "code",
        "response_mode": "fragment",
        "code_challenge_method": "S256",
        "refresh": "true",
        "state": state,
        "code_challenge": code_challenge,
        "scope": HYDRA_SCOPE,
        "redirect_uri": HYDRA_REDIRECT_URI,
        "audience": HYDRA_AUDIENCE,
        "client_id": HYDRA_CLIENT_ID,
        "brand": HYDRA_BRAND,
        "locale": locale,
    }
    return f"{HYDRA_BASE_URL}/oauth2/auth?{urlencode(params)}"


def parse_code_from_redirect(url_or_fragment: str) -> dict[str, str]:
    """Extract code/state from the pasted redirect URL (or raw fragment).

    The app uses response_mode=fragment, so the code lands in the URL
    fragment: https://app.omoda.dev/auth#code=...&state=...
    """
    from urllib.parse import parse_qs

    text = url_or_fragment.strip()
    if "code=" in text:
        idx = text.index("code=")
        return {k: v[0] for k, v in parse_qs(text[idx:]).items()}
    if "error=" in text:
        parsed = {k: v[0] for k, v in parse_qs(text).items()}
        raise HydraAuthError(
            f"Authorization failed: {parsed.get('error')} ({parsed.get('error_description', '')})"
        )
    raise HydraAuthError(
        "Could not find 'code=' in the pasted URL. Make sure you copied the "
        "full address from the browser after logging in."
    )


# ---------------------------------------------------------------------------
# Hydra token endpoints
# ---------------------------------------------------------------------------
async def _hydra_token_request(
    session: aiohttp.ClientSession, form: dict[str, str]
) -> dict[str, Any]:
    async with session.post(
        f"{HYDRA_BASE_URL}/oauth2/token",
        data=form,
        headers={"User-Agent": USER_AGENT},
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise HydraAuthError(
                f"Hydra token endpoint returned {resp.status}: {body[:300]}"
            )
        payload = json.loads(body)
        if "access_token" not in payload:
            raise HydraAuthError(
                f"Hydra token response missing access_token: {body[:300]}"
            )
        return payload


async def hydra_exchange_code(
    session: aiohttp.ClientSession, code: str, code_verifier: str
) -> dict[str, Any]:
    """Exchange the authorization code for tokens."""
    return await _hydra_token_request(
        session,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": HYDRA_CLIENT_ID,
            "redirect_uri": HYDRA_REDIRECT_URI,
        },
    )


async def hydra_refresh_token(
    session: aiohttp.ClientSession, refresh_token: str
) -> dict[str, Any]:
    """Refresh the access token."""
    return await _hydra_token_request(
        session,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": HYDRA_CLIENT_ID,
            "redirect_uri": HYDRA_REDIRECT_URI,
        },
    )


def token_expiry(payload: dict[str, Any]) -> int:
    """Compute the access-token expiry epoch from a token payload."""
    return int(time.time()) + int(payload.get("expires_in", 3000))

# ---------------------------------------------------------------------------
# Esteo backend
# ---------------------------------------------------------------------------
class EsteoClient:
    """Esteo backend REST client with automatic token refresh."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: Callable[[], dict[str, Any]],
        token_updater: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """token_provider returns {'access_token','refresh_token','expires_at'}."""
        self._session = session
        self._token_provider = token_provider
        self._token_updater = token_updater

    async def _access_token(self, force_refresh: bool = False) -> str:
        tokens = self._token_provider()
        access = tokens.get("access_token")
        expires_at = int(tokens.get("expires_at") or 0)
        if not force_refresh and access and expires_at - 60 > time.time():
            return access
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise HydraAuthError(
                "Esteo access token expired and no refresh token available"
            )
        _LOGGER.debug("Refreshing Esteo OAuth token")
        payload = await hydra_refresh_token(self._session, refresh)
        new_tokens = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token", refresh),
            "expires_at": token_expiry(payload),
        }
        if self._token_updater:
            self._token_updater(new_tokens)
        return new_tokens["access_token"]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        _retried: bool = False,
    ) -> Any:
        token = await self._access_token()
        url = f"{API_BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        async with self._session.request(
            method, url, headers=headers, json=json_body, params=params
        ) as resp:
            body = await resp.text()
            if resp.status == 401 and not _retried:
                # force refresh once and retry
                await self._access_token(force_refresh=True)
                return await self._request(
                    method, path, json_body=json_body, params=params, _retried=True
                )
            if resp.status not in (200, 201):
                raise EsteoApiError(
                    f"Esteo API {path} returned {resp.status}: {body[:300]}"
                )
            if not body:
                return None
            return json.loads(body)

    async def get_vehicles(self) -> list[dict[str, Any]]:
        """Return the garage (list of vehicles)."""
        data = await self._request("GET", "/telematics/v1/chery/vehicle")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "items", "vehicles", "body"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        raise EsteoApiError(f"Unexpected garage response: {data!r:.300}")

    async def login_tsp(self) -> dict[str, Any]:
        """Exchange the OAuth token for Chery TSP credentials (userToken/accountId)."""
        token = await self._access_token()
        data = await self._request(
            "POST", "/telematics/v1/chery/account/login", json_body={"token": token}
        )
        if isinstance(data, dict) and not (
            "userToken" in data or "accountId" in data
        ):
            for key in ("data", "body"):
                if isinstance(data.get(key), dict):
                    return data[key]
        return data

    async def get_task(self, vin: str) -> dict[str, Any]:
        """Return {'taskId', 'taskIdCreatedAt'} for the vehicle."""
        data = await self._request(
            "GET", "/telematics/v1/chery/vehicle/task", params={"vin": vin}
        )
        if isinstance(data, dict) and "taskId" not in data:
            for key in ("data", "body"):
                if isinstance(data.get(key), dict):
                    return data[key]
        return data or {}

    async def get_remote_control_items(self, vin: str) -> Any:
        """Capabilities of the car (which remote-control items exist)."""
        return await self._request(
            "GET",
            "/telematics/v1/chery/vehicle/remote-control/items",
            params={"vin": vin},
        )

    async def logout_tsp(self) -> None:
        try:
            await self._request(
                "POST", "/telematics/v1/chery/account/logout", json_body={}
            )
        except EsteoApiError:
            _LOGGER.debug("TSP logout failed (ignored)")

    async def check_control_password(self, vin: str, pin: str) -> dict[str, Any]:
        """Verify the control PIN → returns {'taskId': ...} for command auth."""
        data = await self._request(
            "POST", "/telematics/v1/chery/vehicle/check-password",
            json_body={"vin": vin, "password": pin},
        )
        if isinstance(data, dict) and "taskId" not in data:
            for key in ("data", "body"):
                if isinstance(data.get(key), dict):
                    return data[key]
        return data or {}

# ---------------------------------------------------------------------------
# Chery TSP — request signing (API_REFERENCE.md §8)
# ---------------------------------------------------------------------------
def _flatten_for_sign(body: dict[str, Any]) -> dict[str, Any]:
    """Mirror of CVNetManager.a(JSONObject): flatten values for signing.

    - arrays of objects become "k=v&k2=v2" strings (sub-keys sorted)
    - arrays of scalars become their concatenated string values
    - everything else is kept as-is
    """
    flat: dict[str, Any] = {}
    for key, value in body.items():
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    sub = _flatten_for_sign(item)
                    chunk = "".join(
                        f"{k}={sub[k]}&" for k in sorted(sub) if sub[k] is not None
                    )
                    parts.append(chunk.rstrip("&"))
                else:
                    parts.append(str(item))
            flat[key] = "".join(parts)
        else:
            flat[key] = value
    return flat


def tsp_sign_body(body: dict[str, Any], timestamp_ms: int) -> dict[str, Any]:
    """Return a copy of `body` with appId + sign added (tagEncrypt == "1")."""
    flat = _flatten_for_sign(body)
    flat["appId"] = TSP_APP_ID

    canonical = ""
    for key in sorted(flat):
        value = flat[key]
        if value is None or value == "":
            continue
        canonical += f"{key}={value}&"

    secret_key = TSP_APP_SECRET[0::2]  # every even-index character
    canonical += f"secretKey={secret_key}&timestamp={timestamp_ms}"

    signed = dict(body)
    signed["appId"] = TSP_APP_ID
    signed["sign"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper()
    return signed


def tsp_headers(token: str, timestamp_ms: int) -> dict[str, str]:
    """Headers required on every TSP request (API_REFERENCE.md §7.1)."""
    return {
        "Authorization": token,
        "timestamp": str(timestamp_ms),
        "x-TenantId": "",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Chery TSP client
# ---------------------------------------------------------------------------
class CheryTspClient:
    """Minimal Chery TSP client (read-only operations for now)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        vin: str,
        token_provider: Callable[[], str],
        relogin_handler: Callable[[], Any] | None = None,
    ) -> None:
        """token_provider returns the current TSP userToken.
        relogin_handler (optional) is awaited once when the TSP reports an
        auth error — it should refresh the token (e.g. re-run the Esteo
        login) before the request is retried."""
        self._session = session
        self._vin = vin
        self._token_provider = token_provider
        self._relogin_handler = relogin_handler
        self._task_id: str | None = None

    async def _post(
        self, path: str, body: dict[str, Any], _retried: bool = False
    ) -> Any:
        timestamp_ms = int(time.time() * 1000)
        signed = tsp_sign_body(body, timestamp_ms)
        token = self._token_provider()
        url = f"{TSP_BASE_URL}{path}"
        async with self._session.post(
            url, json=signed, headers=tsp_headers(token, timestamp_ms)
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise TspError(
                    None, f"HTTP {resp.status} on {path}: {text[:300]}"
                )
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as err:
                raise TspError(None, f"Non-JSON response from {path}: {text[:300]}") from err

            code = str(payload.get("code", ""))
            if code != TSP_SUCCESS_CODE:
                # one relogin+retry for auth-looking errors
                if not _retried and self._relogin_handler is not None:
                    _LOGGER.debug("TSP non-success code %s on %s — retrying after re-login", code, path)
                    await self._relogin_handler()
                    return await self._post(path, body, _retried=True)
                raise TspError(code, f"{path}: {text[:300]}")
            return payload

    async def realtime(self) -> dict[str, Any]:
        """Fetch the full vehicle state data-pool (API_REFERENCE.md §11)."""
        payload = await self._post("/asr/manager/realtime", {"vin": self._vin})
        body = payload.get("body")
        if isinstance(body, dict):
            return body
        if isinstance(body, str) and body:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {}
        return {}

    async def query_vehicle_location(self) -> dict[str, Any]:
        """Explicit location query (realtime() already includes lat/lon)."""
        payload = await self._post(
            "/asc/vehicleControl/queryVehicleLocation",
            {"vin": self._vin, "clientType": "1", "seq": str(int(time.time() * 1000))},
        )
        body = payload.get("body")
        return body if isinstance(body, dict) else {}

    async def check_token(self) -> bool:
        """Validate the TSP token."""
        payload = await self._post(
            "/act/user/checkToken",
            {"vin": self._vin, "clientType": "1", "seq": str(int(time.time() * 1000))},
        )
        return str(payload.get("code")) == TSP_SUCCESS_CODE

    # ------------------------------------------------------------------
    # Remote control commands (Phase 2)
    # ------------------------------------------------------------------
    def _cmd_base(self) -> dict[str, Any]:
        """Base fields for every command request."""
        return {
            "vin": self._vin,
            "clientType": "1",
            "seq": str(int(time.time() * 1000)),
            "taskId": self._task_id or "",
        }

    def set_task_id(self, task_id: str) -> None:
        """Set the taskId obtained from the control-PIN check."""
        self._task_id = task_id

    async def check_password(self, pin: str) -> dict[str, Any]:
        """Verify the control PIN via TSP → returns {'taskId': ...}.

        This is the TSP-side endpoint (works in both OAuth and direct-token
        modes). The Esteo backend has a parallel endpoint used by the OAuth
        client.
        """
        payload = await self._post(
            "/asc/vehicleControl/check-password",
            {
                "vin": self._vin,
                "password": pin,
                "clientType": "1",
                "seq": str(int(time.time() * 1000)),
            },
        )
        body = payload.get("body") if isinstance(payload, dict) else None
        if isinstance(body, dict):
            return body
        if isinstance(body, str) and body:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {}
        return payload if isinstance(payload, dict) else {}

    async def send_command(self, path: str, extra: dict[str, Any] | None = None) -> Any:
        """Send a signed command POST to the TSP."""
        body = self._cmd_base()
        if extra:
            body.update(extra)
        payload = await self._post(path, body)
        return payload

    async def lock_doors(self) -> Any:
        return await self.send_command("/asc/vehicleControl/lockControl", {"swi": "1"})

    async def unlock_doors(self) -> Any:
        return await self.send_command("/asc/vehicleControl/lockControl", {"swi": "0"})

    async def start_engine(self, duration_minutes: int = 10) -> Any:
        return await self.send_command("/asc/vehicleControl/engineControl", {
            "swi": "1", "engineTimes": str(duration_minutes),
        })

    async def stop_engine(self) -> Any:
        return await self.send_command("/asc/vehicleControl/engineControl", {"swi": "0"})

    async def find_car(self) -> Any:
        return await self.send_command("/asc/vehicleControl/findCar")

    async def open_trunk(self) -> Any:
        return await self.send_command("/asc/vehicleControl/powerLiftgateControl", {"swi": "1"})

    async def control_climate(self, on: bool, temperature: float = 22.0,
                              duration: int = 10) -> Any:
        return await self.send_command("/asc/vehicleControl/airControl", {
            "swi": "1" if on else "0",
            "temperature": str(temperature),
            "airConditionTimes": str(duration),
        })

    async def control_windshield_defrost(self, on: bool) -> Any:
        return await self.send_command("/asc/vehicleControl/frontWindshieldControl", {
            "swi": "1", "frontWindshieldHeat": "1" if on else "0",
        })

    async def control_rear_defrost(self, on: bool) -> Any:
        return await self.send_command("/asc/vehicleControl/backDefrostingControl", {
            "swi": "1" if on else "0",
        })

    async def set_location_sharing(self, enabled: bool) -> Any:
        """Enable/disable vehicle location sharing."""
        return await self.send_command("/act/vehicleLocation/setSwitch", {
            "swi": "1" if enabled else "0",
        })


# ---------------------------------------------------------------------------
# Headless OAuth login (phone + password, no browser)
# ---------------------------------------------------------------------------
from urllib.parse import urlparse, parse_qs  # noqa: E402

_HYDRA_HOST = "idp.prod.esteo.ru"


def _fix_url(url: str) -> str:
    """Resolve relative URLs and force HTTPS for the IDP domain."""
    if url.startswith("/"):
        return f"https://{_HYDRA_HOST}{url}"
    return url.replace(f"http://{_HYDRA_HOST}", f"https://{_HYDRA_HOST}")


def format_phone(phone: str) -> str:
    """Format a phone number into the +7 (XXX) XXX-XX-XX mask Kratos expects."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+7 ({digits[:3]}) {digits[3:6]}-{digits[6:8]}-{digits[8:10]}"
    return phone  # fallback: return as-is


async def login_with_credentials(
    session: aiohttp.ClientSession,
    phone: str,
    password: str,
) -> dict[str, Any]:
    """Full programmatic OAuth login: phone + password → tokens.

    No browser needed. The session MUST have a CookieJar with unsafe=True
    (the redirect chain goes through HTTP at one point).
    """
    from urllib.parse import urlencode as _ue

    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    authorize_url = build_authorize_url(state, challenge)
    formatted_phone = format_phone(phone)

    # Step 1: follow authorize URL → capture flow_id + login_challenge + cookies
    flow_id: str | None = None
    login_challenge: str | None = None
    url: str = authorize_url
    for _ in range(10):
        async with session.get(url, allow_redirects=False) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                url = _fix_url(resp.headers.get("Location", ""))
                if not url:
                    break
                qs = parse_qs(urlparse(url).query)
                if "login_challenge" in qs:
                    login_challenge = qs["login_challenge"][0]
                if "flow" in qs and "/login" in url:
                    flow_id = qs["flow"][0]
                continue
            break

    if not flow_id:
        raise HydraAuthError("Could not obtain a Kratos login flow from the authorize URL")

    # Step 2: extract CSRF cookie → get CSRF token from flow API
    csrf_cookie = next(
        (c.value for c in session.cookie_jar if c.key.startswith("csrf_token_")), None
    )
    csrf: str | None = None
    async with session.get(
        f"https://{_HYDRA_HOST}/self-service/login/flows?id={flow_id}",
        headers={"Accept": "application/json", "X-CSRF-Token": csrf_cookie or ""},
    ) as resp:
        if resp.status == 200:
            for node in (await resp.json()).get("ui", {}).get("nodes", []):
                if node.get("attributes", {}).get("name") == "csrf_token":
                    csrf = node["attributes"]["value"]
                    break
    if not csrf:
        raise HydraAuthError("Could not extract CSRF token from the login flow")

    # Step 3: POST credentials as form-encoded (browser flow → 303 redirect)
    post_body = _ue({
        "identifier": formatted_phone, "password": password,
        "csrf_token": csrf, "method": "password",
    }).encode()
    async with session.post(
        f"https://{_HYDRA_HOST}/self-service/login?flow={flow_id}",
        data=post_body,
        headers={"Accept": "text/html", "Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=False,
    ) as resp:
        if resp.status == 400:
            data = await resp.json()
            msgs = data.get("ui", {}).get("messages", [])
            text = msgs[0].get("text", "Login failed") if msgs else "Login failed"
            raise HydraAuthError(f"Login rejected: {text}")
        if resp.status not in (200, 303):
            raise HydraAuthError(f"Kratos login returned {resp.status}")
        # consume the body to release the connection
        await resp.read()

    # Step 4: revisit /login?login_challenge=XXX with the Kratos session cookie
    # → Remix does the Hydra handoff → consent → OAuth code
    if not login_challenge:
        raise HydraAuthError("Login succeeded but login_challenge was lost")
    code: str | None = None
    url: str = f"https://{_HYDRA_HOST}/login?login_challenge={login_challenge}"
    for _ in range(15):
        if not url:
            break
        if "code=" in url:
            parsed = parse_code_from_redirect(url)
            if parsed.get("state") != state:
                raise HydraAuthError("State mismatch in OAuth callback")
            code = parsed["code"]
            break
        if "error=" in url:
            parsed = parse_code_from_redirect(url)
            raise HydraAuthError(f"OAuth error: {parsed.get('error')}")
        url = _fix_url(url)
        async with session.get(url, allow_redirects=False,
                               headers={"Accept": "text/html"}) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                url = resp.headers.get("Location", "")
            else:
                await resp.read()
                break

    if not code:
        raise HydraAuthError("Login succeeded but no OAuth code was returned")
    tokens = await hydra_exchange_code(session, code, verifier)
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": token_expiry(tokens),
    }