"""Live end-to-end validation of the Esteo/Chery protocol chain.

Runs the full read-only pipeline OUTSIDE Home Assistant so you can verify
the reverse-engineered protocol with your real account before installing
the integration:

    OAuth (Hydra PKCE, manual paste) → Esteo garage → TSP credentials
    → signed realtime state query → pretty-printed vehicle status

Usage:
    python tools/live_check.py                 # interactive OAuth flow
    python tools/live_check.py --loop          # keep polling every 60 s
    python tools/live_check.py --direct <USER_TOKEN> --vin <VIN>
                                              # bypass Esteo (captured token)

Requires: aiohttp  (pip install aiohttp)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import aiohttp
except ImportError:
    print("aiohttp is required for the live check:  pip install aiohttp")
    sys.exit(1)

COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "esteo"
)

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


def hr(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


async def oauth_flow(session: aiohttp.ClientSession) -> dict:
    """Interactive Hydra PKCE flow; returns token dict."""
    import secrets as _secrets

    verifier, challenge = api.generate_pkce_pair()
    state_value = _secrets.token_urlsafe(16)
    url = api.build_authorize_url(state_value, challenge)
    hr("STEP 1 — Authorization")
    print("Open this URL in your browser and log in with your Esteo account:\n")
    print(url)
    print(
        "\nAfter login the browser lands on https://app.omoda.dev/auth#code=…"
        "\n(the page may fail to load — that is OK)."
        "\nCopy the FULL address from the browser address bar and paste it here:"
    )
    pasted = input("\n> ").strip()
    parsed = api.parse_code_from_redirect(pasted)
    if parsed.get("state") and parsed["state"] != state_value:
        print("WARNING: state mismatch (continuing anyway in live-check mode)")

    hr("STEP 2 — Token exchange")
    payload = await api.hydra_exchange_code(session, parsed["code"], verifier)
    print(
        f"access_token:  {payload['access_token'][:40]}…"
        f"\nrefresh_token: {str(payload.get('refresh_token'))[:20]}…"
        f"\nexpires_in:    {payload.get('expires_in')} s"
    )
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": api.token_expiry(payload),
    }


def print_state(state: models.VehicleState) -> None:
    print(
        f"VIN:                  {state.vin}\n"
        f"Data time:            {state.timestamp}\n"
        f"Online:               {state.online}\n"
        f"Engine on:            {state.engine_on}\n"
        f"Battery SOC:          {state.soc}\n"
        f"Fuel level:           {state.fuel_level}\n"
        f"Range:                {state.range} ({state.range_source})\n"
        f"Odometer:             {state.odometer}\n"
        f"Interior temp:        {state.interior_temperature}\n"
        f"Doors locked:         {state.doors_locked}\n"
        f"Door FL/FR/RL/RR open:{state.door_fl_open}/{state.door_fr_open}"
        f"/{state.door_rl_open}/{state.door_rr_open}\n"
        f"Trunk open:           {state.trunk_open}\n"
        f"GPS:                  lat={state.gps.latitude} lon={state.gps.longitude}"
        f" valid={state.gps.valid} sat={state.gps.satellites}\n"
        f"Tires kPa FL/FR/RL/RR:{state.tire_pressure_fl}/{state.tire_pressure_fr}"
        f"/{state.tire_pressure_rl}/{state.tire_pressure_rr}"
    )
    print(f"(raw fields in latest response: {len(state.raw)})")

async def run(args: argparse.Namespace) -> int:
    async with aiohttp.ClientSession() as session:
        if args.direct:
            vin = (args.vin or "").strip().upper()
            if not vin:
                print("--direct requires --vin as well")
                return 2
            hr("DIRECT MODE — validating TSP token")
            tsp = api.CheryTspClient(session, vin, lambda: args.direct)
            raw = await tsp.realtime()
            if not raw:
                print("Empty state — token may be invalid or car asleep.")
                return 1
            print_state(models.VehicleState.from_data_pool(raw))
            return 0

        tokens = await oauth_flow(session)
        esteo = api.EsteoClient(session, lambda: tokens, lambda t: tokens.update(t))

        hr("STEP 3 — Garage (GET /telematics/v1/chery/vehicle)")
        vehicles = await esteo.get_vehicles()
        print(json.dumps(vehicles, indent=2, ensure_ascii=False)[:2000])

        hr("STEP 4 — TSP credential exchange (POST /chery/account/login)")
        creds = await esteo.login_tsp()
        print(
            f"userToken:  {str(creds.get('userToken'))[:40]}…\n"
            f"accountId: {creds.get('accountId')}\n"
            f"(full keys: {list(creds.keys())})"
        )
        user_token = creds.get("userToken")
        if not user_token:
            print("No userToken returned — cannot continue.")
            return 1

        vin = args.vin
        if not vin:
            vins = []
            for v in vehicles if isinstance(vehicles, list) else []:
                for key in ("vin", "VIN", "vehicleVin"):
                    if v.get(key):
                        vins.append(str(v[key]))
                        break
            if len(vins) == 1:
                vin = vins[0]
            elif vins:
                print("\nMultiple vehicles found — pick one:")
                for i, v in enumerate(vins):
                    print(f"  [{i}] {v}")
                vin = vins[int(input("index> "))]
            else:
                vin = input("No VIN found in garage — enter VIN manually: ").strip()
        vin = vin.strip().upper()

        hr("STEP 5 — Task info (GET /chery/vehicle/task?vin=…)")
        try:
            task = await esteo.get_task(vin)
            print(f"taskId: {task.get('taskId')} (createdAt: {task.get('taskIdCreatedAt')})")
        except Exception as err:  # noqa: BLE001
            print(f"(task query failed: {err})")

        tsp = api.CheryTspClient(session, vin, lambda: user_token)

        async def poll_once() -> None:
            hr("STEP 6 — Realtime state (POST /asr/manager/realtime)")
            raw = await tsp.realtime()
            if not raw:
                print("Empty state body — car may be asleep; try again.")
                return
            print_state(models.VehicleState.from_data_pool(raw))

        await poll_once()

        if args.loop:
            print("\nPolling every 60 s — Ctrl+C to stop.")
            while True:
                await asyncio.sleep(60)
                try:
                    await poll_once()
                except KeyboardInterrupt:
                    raise
                except Exception as err:  # noqa: BLE001
                    print(f"poll failed: {err}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--direct", metavar="USER_TOKEN",
        help="skip Esteo OAuth; validate a captured TSP userToken directly",
    )
    parser.add_argument("--vin", help="VIN (required with --direct, optional otherwise)")
    parser.add_argument(
        "--loop", action="store_true", help="keep polling realtime state every 60 s"
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as err:  # noqa: BLE001
        print(f"\nFAILED: {type(err).__name__}: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())