# Home Assistant — Esteo (Chery RU) integration

**Unofficial, read-only** Home Assistant integration for Chery vehicles sold via the
Esteo (RU) dealer network. It is built entirely on reverse-engineered cloud APIs
(see `../API_REFERENCE.md`) — there is no public/official API.

> ⚠️ Work in progress — read-only status only (sensors, binary sensors, GPS tracker).
> Remote control (engine start, climate, locks) is planned but **not** implemented yet.

## What you get (per vehicle)

- **Device tracker** — GPS position (lat/lon/heading/altitude/satellites)
- **Sensors** — battery %, range, odometer, interior temperature, fuel level, average speed,
  charging power, remaining charge time, 4 tire pressures, charge state, raw-state diagnostic
- **Binary sensors** — engine on, online, alarm, anti-theft, charge gun, hood, trunk,
  4 doors, 4 windows, sunroof

Polling defaults to every 300 s (configurable in integration options, min 60 s).
The Chery TSP is slow — a state refresh can take 10–60 s.

## Installation

1. Copy `custom_components/esteo` into your Home Assistant
   `<config>/custom_components/esteo` directory (or install via HACS as a custom repository).
2. Restart Home Assistant.
3. Settings → Devices & Services → Add Integration → **Esteo**.

## Setup (recommended — Esteo account, no browser needed)

The integration handles the entire login flow internally — you just enter your
Esteo phone number and password (the same credentials you use in the Esteo mobile app):

1. In the integration setup choose **Esteo account (phone + password)**.
2. Enter your phone number in `+7XXXXXXXXXX` format (e.g. `+79991234567`) and your password.
3. Pick your vehicle from the garage list.

The integration does the full OAuth2 PKCE + Ory Kratos login programmatically —
no browser, no copy-paste. Tokens are stored and refreshed automatically.

## Setup (advanced — direct TSP token)

If you cannot complete the OAuth flow (or want to bypass Esteo entirely), capture the
`userToken` (the `Authorization` header value sent to `tspconsole.chery.ru`) once with a
traffic-capture tool, then choose **Direct TSP token** in setup and enter VIN + token.

## Calibration note

The Chery TSP returns raw numeric codes for doors/windows/engine flags. The mappings
("1" = open/on) follow common Chery conventions but are **not yet verified against live
data**. Every raw field is exposed as an attribute of the `Raw state` diagnostic sensor —
compare it with the mobile app and, if a flag is inverted, edit the mapping tables in
`custom_components/esteo/const.py` (documented there) and open an issue so it can be fixed.

## Troubleshooting

- Enable debug logging:
  ```yaml
  logger:
    logs:
      custom_components.esteo: debug
  ```
- If updates fail with a TSP error, open integration **Options** and save — this re-fetches
  the TSP token (for the OAuth setup method).
- The car is often offline; a failed poll usually just means it is asleep. The app
  occasionally uses an "SMS awaken" fallback which this integration does not implement yet.

## Legal

For personal interoperability use with your own vehicle and account only.
Not affiliated with Esteo, Kodix or Chery.