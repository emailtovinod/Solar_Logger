# Solar Logger

Local data logger for Deye inverters. See [PLAN.md](PLAN.md) for the full roadmap.
This is Phase 1: the collector and the database.

**Status:** tested only with the built-in simulator and a local PostgreSQL.
The Solarman V5 and RS485 drivers and the register maps have not been run
against a real inverter yet. Check `snapshot` output against the Deye app first.

## Quick start

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp config.example.yaml config.yaml      # set model, logger IP and serial number
.venv/bin/solar-logger snapshot         # read once and print every signal; no database needed
```

If `snapshot` shows values matching the app, start logging:

```sh
docker compose up -d --build            # TimescaleDB + collector
docker compose logs -f collector
```

Or without Docker, against any PostgreSQL:

```sh
.venv/bin/solar-logger init-db
.venv/bin/solar-logger run
```

To try the pipeline with no inverter, set `source: {type: simulator}`.

## Sources

| `source.type` | Connection | Needs |
|---|---|---|
| `solarman_v5` | Wi-Fi logger stick, TCP 8899 | stick IP and serial number; port 8899 open |
| `modbus_rtu` | USB-RS485 adapter on the inverter's Modbus port | `/dev/ttyUSB0`, 9600 baud, slave 1 |
| `simulator` | none | made-up data |

## Register maps

`solar_logger/maps/deye_hybrid.yaml` (single-phase SG0\*LP1) and `deye_p3.yaml`
(three-phase SG0\*LP3/HP3). Addresses come from the community definitions in
[davidrapan/ha-solarman](https://github.com/davidrapan/ha-solarman) (MIT).
`device.model` can also be a path to your own map file.

## Database

- `samples(time, device, signal, value)`: one row per signal per poll. A
  TimescaleDB hypertable when the extension is present, otherwise a plain table.
- `polls(time, device, ok, duration_ms, signals, error)`: one row per poll
  attempt, so gaps and their causes are visible.

A failed read stores nothing for the affected signals. If the database is
down, up to 5000 polls are buffered in memory and written when it returns.

## Tests

```sh
.venv/bin/pytest
TEST_DATABASE_URL=postgresql://user@host/db .venv/bin/pytest   # also runs the storage test (drops samples/polls in that db)
```
