"""Live end-to-end validation of the Esteo/Chery protocol chain.

Runs the full read-only pipeline OUTSIDE Home Assistant so you can verify
the reverse-engineered protocol with your real account before installing
the integration:

    Headless OAuth login (phone + password) → Esteo garage → TSP credentials
    → signed realtime state query → pretty-printed vehicle status

With --commands it also opens an interactive REMOTE CONTROL test menu
(lock/unlock, engine, climate, defrost, find car, trunk, location sharing)
so every Phase 2 endpoint can be verified against the live car and the raw
TSP responses inspected/calibrated.

Usage:
    python tools/live_check.py --phone +79991234567 --password SECRET
    python tools/live_check.py --phone +79991234567 --password SECRET --loop
    python tools/live_check.py --phone +79991234567 --password SECRET --commands --pin 1234
    python tools/live_check.py --direct <USER_TOKEN> --vin <VIN> --commands --pin 1234
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


async def headless_login(session: aiohttp.ClientSession, phone: str, password: str) -> dict:
    """Headless OAuth login via phone + password (no browser)."""
    hr("STEP 1 — Headless login (phone + password)")
    tokens = await api.login_with_credentials(session, phone, password)
    print(
        f"access_token:  {tokens['access_token'][:40]}…\n"
        f"refresh_token: {str(tokens.get('refresh_token'))[:20]}…\n"
        f"expires_at:    {tokens.get('expires_at')}"
    )
    return tokens


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


# ---------------------------------------------------------------------------
# Remote-control test menu (Phase 2 verification)
# ---------------------------------------------------------------------------

MENU = """\
---------------- REMOTE CONTROL TEST MENU ----------------
  [ 1] Lock doors
  [ 2] Unlock doors
  [ 3] Find car (honk & flash)
  [ 4] Engine START (10 min)
  [ 5] Engine STOP
  [ 6] Climate ON (temp + duration)
  [ 7] Climate OFF
  [ 8] Windshield defrost ON
  [ 9] Windshield defrost OFF
  [10] Rear defrost ON
  [11] Rear defrost OFF
  [12] Open trunk
  [13] Location sharing ON
  [14] Location sharing OFF
  [ r] Raw request (custom path + extra JSON — for calibration)
  [ p] Refresh taskId (re-check control PIN)
  [ s] Poll realtime state
  [ q] Quit
----------------------------------------------------------"""


def _print_tsp_response(payload) -> None:
    """Dump a raw TSP response and hint at the success codes."""
    print("RAW TSP RESPONSE:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if isinstance(payload, dict):
        code = payload.get("code")
        if str(code) == "000000":
            print(">> code 000000 = SUCCESS")
        elif str(code) == "A00079":
            print(">> code A00079 = command ACCEPTED/queued")
        elif code is not None:
            print(f">> code {code} — NOT a known success code, needs investigation")


async def _ensure_task_id(esteo, tsp, vin: str, pin: str) -> bool:
    """Check the control PIN → taskId. Tries Esteo backend, falls back to TSP.

    Prints raw responses of both endpoints so their shapes can be verified.
    """
    hr("CONTROL PIN CHECK → taskId")
    if esteo is not None:
        try:
            hr("Esteo backend: POST /telematics/v1/chery/vehicle/check-password")
            result = await esteo.check_control_password(vin, pin)
            print(json.dumps(result, indent=2, ensure_ascii=False)[:1000])
            task_id = result.get("taskId")
            if task_id:
                tsp.set_task_id(str(task_id))
                print(f">> taskId obtained: {task_id}")
                return True
            print(">> no taskId in backend response")
        except Exception as err:  # noqa: BLE001
            print(f">> backend check-password failed: {type(err).__name__}: {err}")
    try:
        hr("TSP side: POST /asc/vehicleControl/check-password")
        result = await tsp.check_password(pin)
        print(json.dumps(result, indent=2, ensure_ascii=False)[:1000])
        task_id = result.get("taskId")
        if task_id:
            tsp.set_task_id(str(task_id))
            print(f">> taskId obtained: {task_id}")
            return True
        print(">> no taskId in TSP response")
    except Exception as err:  # noqa: BLE001
        print(f">> TSP check-password failed: {type(err).__name__}: {err}")
    return False


async def _run_command(desc: str, coro) -> None:
    """Send one command and dump the raw TSP response."""
    hr(f"COMMAND — {desc}")
    try:
        payload = await coro
        _print_tsp_response(payload)
    except Exception as err:  # noqa: BLE001
        print(f"COMMAND FAILED: {type(err).__name__}: {err}")


async def _raw_request(tsp) -> None:
    """Send a fully custom signed command — endpoint calibration tool."""
    hr("RAW REQUEST")
    path = input("path (e.g. /asc/vehicleControl/lockControl)> ").strip()
    if not path:
        print("aborted")
        return
    body_s = input(
        'extra JSON merged over base {vin, clientType, seq, taskId} '
        '(e.g. {"swi": "1"} — Enter for none)> '
    ).strip()
    extra = None
    if body_s:
        try:
            extra = json.loads(body_s)
        except json.JSONDecodeError as err:
            print(f"invalid JSON ({err}) — aborted")
            return
    await _run_command(f"POST {path} extra={extra!r}", tsp.send_command(path, extra))


async def command_menu(esteo, tsp, vin: str, pin_arg: str | None) -> None:
    """Interactive remote-control test menu (Phase 2 live verification)."""
    print("\n" + "!" * 60)
    print("WARNING: these commands ACT ON THE REAL VEHICLE.")
    print("!" * 60)
    pin = pin_arg or input("\nControl PIN> ").strip()
    if not pin:
        print("No PIN — commands need a taskId; aborting menu.")
        return

    while not await _ensure_task_id(esteo, tsp, vin, pin):
        retry = input(
            "\nNo taskId obtained — commands cannot be authorized.\n"
            "Re-enter PIN (or press Enter to quit menu)> "
        ).strip()
        if not retry:
            return
        pin = retry

    while True:
        print(MENU)
        choice = input("choice> ").strip().lower()
        try:
            if choice == "q":
                return
            if choice == "s":
                hr("Realtime state (POST /asr/manager/realtime)")
                raw = await tsp.realtime()
                if raw:
                    print_state(models.VehicleState.from_data_pool(raw))
                else:
                    print("Empty state body — car may be asleep; try again.")
                continue
            if choice == "p":
                while not await _ensure_task_id(esteo, tsp, vin, pin):
                    retry = input("Re-enter PIN (Enter to quit menu)> ").strip()
                    if not retry:
                        return
                    pin = retry
                continue
            if choice == "r":
                await _raw_request(tsp)
                continue
            if choice == "1":
                await _run_command("Lock doors", tsp.lock_doors())
            elif choice == "2":
                await _run_command("Unlock doors", tsp.unlock_doors())
            elif choice == "3":
                await _run_command("Find car", tsp.find_car())
            elif choice == "4":
                minutes = input("duration minutes [10]> ").strip()
                await _run_command(
                    f"Engine START ({minutes or 10} min)",
                    tsp.start_engine(int(minutes) if minutes else 10),
                )
            elif choice == "5":
                await _run_command("Engine STOP", tsp.stop_engine())
            elif choice == "6":
                temp = input("temperature °C [22]> ").strip()
                dur = input("duration minutes [10]> ").strip()
                await _run_command(
                    f"Climate ON ({temp or 22}°C, {dur or 10} min)",
                    tsp.control_climate(
                        True,
                        float(temp) if temp else 22.0,
                        int(dur) if dur else 10,
                    ),
                )
            elif choice == "7":
                await _run_command("Climate OFF", tsp.control_climate(False))
            elif choice == "8":
                await _run_command(
                    "Windshield defrost ON", tsp.control_windshield_defrost(True)
                )
            elif choice == "9":
                await _run_command(
                    "Windshield defrost OFF", tsp.control_windshield_defrost(False)
                )
            elif choice == "10":
                await _run_command("Rear defrost ON", tsp.control_rear_defrost(True))
            elif choice == "11":
                await _run_command("Rear defrost OFF", tsp.control_rear_defrost(False))
            elif choice == "12":
                await _run_command("Open trunk", tsp.open_trunk())
            elif choice == "13":
                await _run_command("Location sharing ON", tsp.set_location_sharing(True))
            elif choice == "14":
                await _run_command("Location sharing OFF", tsp.set_location_sharing(False))
            else:
                print("unknown choice")
        except Exception as err:  # noqa: BLE001
            print(f"MENU ACTION FAILED: {type(err).__name__}: {err}")
        print("\n(tip: press [s] to poll state and confirm the change took effect)")

async def run(args: argparse.Namespace) -> int:
    # Login session needs unsafe cookie jar (redirect chain goes via HTTP)
    import aiohttp as _aiohttp

    jar = _aiohttp.CookieJar(unsafe=True)
    async with _aiohttp.ClientSession(cookie_jar=jar) as session:
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
            if args.commands:
                # No Esteo backend in direct mode → PIN checked via TSP side
                await command_menu(None, tsp, vin, args.pin)
            return 0

        if not args.phone or not args.password:
            print("Either --phone + --password (headless login) or --direct + --vin")
            return 2

        tokens = await headless_login(session, args.phone, args.password)
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

        if args.commands:
            await command_menu(esteo, tsp, vin, args.pin)

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
        "--phone",
        help="Esteo account phone number (e.g. +79991234567) for headless login",
    )
    parser.add_argument(
        "--password",
        help="Esteo account password for headless login",
    )
    parser.add_argument(
        "--direct", metavar="USER_TOKEN",
        help="skip Esteo OAuth; validate a captured TSP userToken directly",
    )
    parser.add_argument("--vin", help="VIN (required with --direct, optional otherwise)")
    parser.add_argument(
        "--loop", action="store_true", help="keep polling realtime state every 60 s"
    )
    parser.add_argument(
        "--commands", action="store_true",
        help="open the interactive remote-control test menu (Phase 2 verification)",
    )
    parser.add_argument(
        "--pin", help="control PIN for remote commands (else prompted interactively)"
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