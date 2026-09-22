"""Offline smoke tests for the Esteo integration core logic.

No network, no Home Assistant required:
- imports api.py/models.py via importlib (bypassing the HA-dependent __init__)
- validates the TSP request signing against an independent implementation
- validates PKCE, redirect parsing and the VehicleState parser

Run:  python tools/smoke_test.py
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
import types
from pathlib import Path

# Windows consoles often default to cp1251 — force UTF-8 for this test
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "esteo"
)

# ---------------------------------------------------------------- aiohttp stub
try:
    import aiohttp  # noqa: F401
except ImportError:  # stub it so api.py can be imported without aiohttp
    stub = types.ModuleType("aiohttp")
    stub.ClientSession = object
    stub.ClientError = Exception
    sys.modules["aiohttp"] = stub

# ---------------------------------------------------------------- package shim
pkg = types.ModuleType("esteo")
pkg.__path__ = [str(COMPONENT_DIR)]
sys.modules["esteo"] = pkg


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"esteo.{name}", COMPONENT_DIR / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"esteo.{name}"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


api = load("api")
models = load("models")

PASSED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    assert condition, label
    PASSED += 1


# ================================================================ signing
def reference_sign(body: dict, ts: int) -> str:
    """Independent re-implementation of the Java CVNetManager flow (tagEncrypt=1)."""
    flat = dict(body)
    flat["appId"] = api.TSP_APP_ID

    parts = []
    for key in sorted(flat.keys()):
        value = flat[key]
        if value is not None and value != "":
            parts.append(f"{key}={value}")
    secret = "".join(
        api.TSP_APP_SECRET[i]
        for i in range(len(api.TSP_APP_SECRET))
        if i % 2 == 0
    )
    canonical = "&".join(parts) + "&" if parts else ""
    canonical += f"secretKey={secret}&timestamp={ts}"
    return hashlib.sha256(canonical.encode()).hexdigest().upper()


ts = 1756500000000
body = {"vin": "LVSHCAFB9NE123456"}

signed = api.tsp_sign_body(body, ts)
check("sign present", "sign" in signed)
check("appId added", signed.get("appId") == "ru-1")
check(
    "signature matches reference",
    signed["sign"] == reference_sign(body, ts),
    f"sign={signed['sign'][:16]}…",
)
check("original body untouched", "sign" not in body)

body2 = {"vin": "X", "clientType": "1", "seq": "42", "controlType": "1"}
signed2 = api.tsp_sign_body(body2, ts)
check("multi-field signature matches", signed2["sign"] == reference_sign(body2, ts))

flat3 = api._flatten_for_sign({"items": [{"b": 2, "a": 1}]})
check(
    "array-of-objects flattened sorted",
    flat3["items"] == "a=1&b=2",
    f"got {flat3['items']!r}",
)

# ================================================================ PKCE
verifier, challenge = api.generate_pkce_pair()
check("verifier length 43-128", 43 <= len(verifier) <= 128, f"len={len(verifier)}")
expected = (
    base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    .decode()
    .rstrip("=")
)
check("PKCE S256 challenge correct", challenge == expected)

url = api.build_authorize_url("STATE123", challenge)
check(
    "authorize URL shape",
    url.startswith("https://idp.prod.esteo.ru/oauth2/auth?")
    and "code_challenge_method=S256" in url
    and "response_mode=fragment" in url
    and "brand=esteo" in url
    and "audience=com.kodix.onives.esteo" in url
    and "locale=ru" in url,
)

# ================================================================ redirect parsing
parsed = api.parse_code_from_redirect(
    "https://app.omoda.dev/auth#code=ABC123&state=STATE123&scope=openid+offline_access"
)
check("code parsed from fragment", parsed.get("code") == "ABC123")
check("state parsed from fragment", parsed.get("state") == "STATE123")

parsed2 = api.parse_code_from_redirect("code=XYZ&state=S2")
check("bare fragment parsed", parsed2.get("code") == "XYZ" and parsed2.get("state") == "S2")

try:
    api.parse_code_from_redirect("https://app.omoda.dev/auth#error=access_denied")
    check("error fragment raises", False)
except api.HydraAuthError:
    check("error fragment raises", True)

# ================================================================ state model
SAMPLE = {
    "vin": "LVSHCAFB9NE123456",
    "time": "2025-08-29 10:00:00",
    "engineState": "1",
    "onlineStatus": "1",
    "doorLock": "1",
    "frontLeftDoor": "0",
    "hood": "0",
    "trunkDoor": "1",
    "dumpEnergy": "78",
    "oilSurplus": "12.5",
    "dynamicCombinedRange": "870",
    "odometer": "12345",
    "inCarTemperature": "21.5",
    "averageSpeed": "45",
    "lFrontTyreKpa": "230",
    "rFrontTyreKpa": "232",
    "lRearTyreKpa": "228",
    "rRearTyreKpa": "229",
    "lat": "55.7522",
    "lon": "37.6156",
    "validFlag": "1",
    "gpsSpeed": "0",
    "direction": "90",
    "sateliteNum": "12",
    "chargeState": "0",
    "biopsyAlarm": "0",
    "sunroofState": "0",
    "frontLeftWindowState": "0",
    "unknownFutureField": "zzz",
}

state = models.VehicleState.from_data_pool(SAMPLE)
check("vin parsed", state.vin == "LVSHCAFB9NE123456")
check("soc parsed", state.soc == 78.0)
check("fuel parsed", state.fuel_level == 12.5)
check(
    "range picked",
    state.range == 870.0 and state.range_source == "dynamicCombinedRange",
)
check("odometer parsed", state.odometer == 12345.0)
check("interior temp", state.interior_temperature == 21.5)
check("engine on", state.engine_on is True)
check("online true", state.online is True)
check("doors locked", state.doors_locked is True)
check("door closed", state.door_fl_open is False)
check("trunk open", state.trunk_open is True)
check("tire fl", state.tire_pressure_fl == 230.0)
check("gps lat", state.gps.latitude == 55.7522)
check("gps valid", state.gps.valid is True)
check("unknown field kept in raw", state.raw.get("unknownFutureField") == "zzz")

empty = models.VehicleState.from_data_pool({})
check("empty pool soc None", empty.soc is None)
check("empty pool engine None", empty.engine_on is None)
check("empty pool range None", empty.range is None)

weird = models.VehicleState.from_data_pool({"dumpEnergy": "", "engineState": None})
check("empty string → None", weird.soc is None and weird.engine_on is None)

print(f"\nAll {PASSED} checks passed.")