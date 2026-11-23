# Solar Logger — plan

A self-hosted web app that logs and displays all data from a Deye inverter,
running on a Linux machine on the same LAN.

Status: plan only. Nothing here has been tested against the actual inverter;
the protocol details come from public community sources and must be confirmed
in Phase 0.

## 1. How a Deye inverter exposes data

The Deye Cloud app does not talk to the inverter directly over Wi-Fi in normal
use. The Wi-Fi "stick" logger (made by Solarman/IGEN) pushes data to the cloud,
and the app reads it back from the cloud over HTTPS.

```
Inverter --RS232/RS485 (Modbus RTU)--> Wi-Fi logger stick --TCP--> Deye/Solarman cloud <--HTTPS-- phone app
                                              |
                                              +-- LAN: TCP 8899 (Solarman V5), UDP 48899 (discovery/AT), HTTP 80 (config page)
                                              +-- BLE (app "local mode", newer sticks)
```

Four possible data sources, in order of preference:

| # | Source | Poll rate | Needs internet | Notes |
|---|--------|-----------|----------------|-------|
| A | Logger LAN port, TCP 8899, Solarman V5 (Modbus RTU frame wrapped with the logger serial number) | 5–30 s | No | Best option when available. Libraries: `pysolarmanv5`. Community reports say newer logger firmware closes 8899/48899. |
| B | Direct RS485 Modbus RTU on the inverter's Modbus/meter port, via a USB-RS485 adapter or an RS485-to-Ethernet/ESP32 bridge | 1–10 s | No | Always works, independent of logger firmware. Needs ~$10 hardware and a cable. 9600 8N1, slave ID 1 by default. |
| C | Deye Cloud developer API (`developer.deyecloud.com`, regional base such as `eu1-developer.deyecloud.com/v1.0`): token, station list, `station/latest`, device data, history | ~5 min (cloud update interval) | Yes | Official, needs appId/appSecret from the developer portal plus the account login. Coarse resolution; rate limited. |
| D | Logger "Server B" push: configure the stick's second remote server to point at the Linux machine and decode the pushed V5 frames | ~5 min | No | Passive, only on firmware that still exposes this setting and does not force TLS. |

The app's private API (what the phone actually calls) is not a target: it is
undocumented, tied to app versions, and the developer API returns the same data
legitimately.

Register maps: the community definition files in `davidrapan/ha-solarman`
(`deye_hybrid.yaml`, `deye_sg04lp3.yaml`, string/micro variants) list address,
scale, unit and signedness per model. These will be the basis for our own map.

## 2. Phase 0 — discovery on the real system (must be done first)

Run from the Linux machine on the same LAN as the inverter. Results decide
which collector is primary.

1. Record the inverter model, logger serial number (sticker on the stick,
   10 digits) and logger firmware version (app: device > logger info).
2. Find the logger's IP (router DHCP list) and give it a fixed lease.
3. Port scan: `nmap -p 80,8899,30003 <ip>` and `nmap -sU -p 48899 <ip>`.
4. If 80 is open: open `http://<ip>/` (commonly admin/admin) and note the
   firmware and whether a second server ("Server B") can be set.
5. If 8899 is open: read a few registers with `pysolarmanv5` using the logger
   serial; compare against the app to confirm the register map.
6. If 8899 is closed: choose between B (RS485 adapter) and C (cloud API).
   Check the inverter manual for the Modbus port pinout.
7. Optional, to see what the app itself sends: run `mitmproxy` on the Linux
   machine and point the phone's Wi-Fi proxy at it, or capture logger traffic
   with `tcpdump host <logger-ip>` on the router or a mirrored port. This is
   only for confirming cloud hosts and update intervals; certificate pinning
   may block decryption of the app's traffic.
8. Register at the Deye developer portal and create an app (appId/appSecret)
   regardless, as the fallback source.

Deliverable: a short findings note (model, firmware, open ports, chosen source).

## 3. Architecture

```
            +-------------------- Linux machine (Docker Compose) --------------------+
 logger --> | collector (Python asyncio)  -->  TimescaleDB/Postgres  <--  API (FastAPI) | <-- browser
 RS485  --> |   sources: v5 | rtu | cloud       raw + rollups              REST + WS     |
 cloud  --> |   register map (YAML)                                        static SPA    |
            +-----------------------------------------------------------------------+
```

- **Collector**: one process, pluggable source drivers behind a common
  interface (`read() -> {signal: value}`); YAML register map per inverter
  model; batches contiguous registers; retry with backoff; marks gaps rather
  than inventing values. Only one Modbus client at a time may talk to the
  logger, so the collector is the single owner of that connection.
- **Storage**: PostgreSQL + TimescaleDB. `samples(time, device_id, signal, value)`
  hypertable; continuous aggregates at 1 min / 15 min / 1 day; raw kept 90 days,
  rollups kept indefinitely. (SQLite is a workable alternative for a very small
  machine.)
- **API**: FastAPI. `/api/live` (latest snapshot), `/api/series` (signal, range,
  resolution), `/api/energy/daily|monthly`, `/api/events`, `/api/export.csv`,
  WebSocket `/ws/live`.
- **Frontend**: React + Vite SPA served by the API container. Pages:
  - Live: power-flow diagram (PV, battery, grid, load), key numbers.
  - History: zoomable charts for any signal; day/week/month/year energy.
  - Battery: SOC, voltage, current, temperature, charge/discharge energy.
  - Events: faults, warnings, grid outages, collector gaps.
  - Settings: source config, poll interval, tariff for cost figures.
- **Deployment**: `docker compose up -d` with three services (db, app, optional
  reverse proxy). LAN-only by default with a login; remote access via
  Tailscale/WireGuard rather than an open port.

## 4. Signals to log

All of the following where the model supports it (hybrid models have the most):

- PV: per-string voltage, current, power; total PV power; daily and total yield.
- Battery: SOC, voltage, current, power, temperature; daily and total
  charge/discharge energy; BMS limits and status.
- Grid: per-phase voltage, current, frequency; import/export power (CT or
  meter); daily and total import/export energy.
- Load: per-phase and total load power; daily and total consumption; backup
  (UPS) output.
- Inverter: output power, run state, DC and heatsink temperatures, fault and
  warning codes, generator port power.
- Settings (read-only at first): work mode, time-of-use table, charge/discharge
  limits, export limit.

## 5. Build phases

| Phase | Scope | Done when |
|-------|-------|-----------|
| 0 | Discovery (section 2) | Source chosen; sample register reads match the app |
| 1 | Collector + register map + DB schema; CLI that prints a live snapshot | 24 h of continuous logging with no unexplained gaps |
| 2 | API + live dashboard with WebSocket updates | Live page matches the app within one poll interval |
| 3 | History charts, energy totals, CSV export, rollups and retention | Daily totals agree with the inverter's own counters |
| 4 | Events and alerts (fault codes, low SOC, grid loss, collector offline) via email/Telegram/ntfy | Test alert delivered for each rule |
| 5 | Cloud API driver as fallback/backfill; Docker Compose packaging, backups, docs | Fresh install on the Linux machine from the README |
| 6 (optional) | Write access: work mode, time-of-use, charge limits; MQTT/Home Assistant export | Each write confirmed by read-back; guarded by a config flag |

Write access is deliberately last and off by default: a wrong register write
can change how the inverter treats the battery and grid.

## 6. Risks

- Logger firmware may block local access (8899). Mitigation: RS485 path (B) or
  cloud API (C); do not update logger firmware before Phase 0 if it currently
  works.
- Register maps differ between models and firmware. Mitigation: per-model YAML,
  validated against the app in Phase 0.
- Cloud API limits and 5-minute granularity make it unsuitable as the primary
  source for a live view.
- Polling alongside the logger's own cloud uploads can cause occasional
  timeouts; the collector must tolerate and record them.

## 7. Information needed from the owner

1. Exact inverter model (e.g. SUN-8K-SG04LP3-EU) and whether a battery is fitted.
2. Logger serial number and firmware version.
3. Result of the port scan in Phase 0.
4. Linux machine details (distribution, CPU architecture, Docker available?).
