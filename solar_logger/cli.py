"""Command line entry point: solar-logger [--config FILE] {snapshot,init-db,run}."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

import yaml

from .collector import Collector, poll_once
from .registers import load_map
from .sources import make_source


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("device", {})
    cfg["device"].setdefault("name", "inverter")
    if "model" not in cfg["device"]:
        raise SystemExit("config: device.model is required (deye_hybrid, deye_p3 or a path to a map file)")
    if "source" not in cfg:
        raise SystemExit("config: source section is required")
    cfg["database_url"] = os.environ.get("SOLAR_DATABASE_URL") or cfg.get("database_url")
    cfg["poll_interval"] = float(cfg.get("poll_interval", 15))
    return cfg


def _storage(cfg: dict):
    from .storage import Storage

    if not cfg["database_url"]:
        raise SystemExit("config: database_url (or SOLAR_DATABASE_URL) is required")
    return Storage(cfg["database_url"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="solar-logger")
    parser.add_argument("--config", "-c", default="config.yaml")
    parser.add_argument("--verbose", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot", help="read once and print all signals (no database needed)")
    sub.add_parser("init-db", help="create database tables")
    sub.add_parser("run", help="poll continuously and store samples")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)
    regmap = load_map(cfg["device"]["model"])

    if args.command == "init-db":
        timescale = _storage(cfg).init_schema()
        print("schema ready" + (" (TimescaleDB hypertable)" if timescale else " (plain PostgreSQL tables)"))
        return 0

    source = make_source(cfg["source"], regmap)

    if args.command == "snapshot":
        poll = poll_once(source, regmap, cfg["device"]["name"])
        source.close()
        for s in regmap.signals:
            value = poll.values.get(s.name)
            print(f"{s.name:32} {'(not read)' if value is None else f'{value:g} {s.unit}'}")
        if not poll.ok:
            print(f"\nerrors: {poll.error}", file=sys.stderr)
        return 0 if poll.ok else 1

    storage = _storage(cfg)
    storage.init_schema()
    collector = Collector(source, regmap, storage, cfg["device"]["name"], cfg["poll_interval"])
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: collector.stop.set())
    collector.run()
    storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
