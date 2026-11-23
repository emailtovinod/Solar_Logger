"""Data sources. Each one reads raw holding registers from the inverter."""

from __future__ import annotations

from typing import Protocol

from ..registers import RegisterMap


class Source(Protocol):
    def read(self, start: int, count: int) -> list[int]:
        """Read `count` holding registers from `start`. Raises on failure."""

    def close(self) -> None:
        """Drop the connection; the next read reconnects."""


def make_source(cfg: dict, regmap: RegisterMap) -> Source:
    kind = cfg.get("type")
    if kind == "solarman_v5":
        from .solarman_v5 import SolarmanV5Source

        return SolarmanV5Source(
            host=cfg["host"],
            serial=int(cfg["serial"]),
            port=int(cfg.get("port", 8899)),
            slave_id=int(cfg.get("slave_id", 1)),
            timeout=float(cfg.get("timeout", 10)),
        )
    if kind == "modbus_rtu":
        from .modbus_rtu import ModbusRtuSource

        return ModbusRtuSource(
            device=cfg["device"],
            baudrate=int(cfg.get("baudrate", 9600)),
            slave_id=int(cfg.get("slave_id", 1)),
            timeout=float(cfg.get("timeout", 2)),
        )
    if kind == "simulator":
        from .simulator import SimulatorSource

        return SimulatorSource(regmap)
    raise ValueError(f"unknown source type {kind!r} (use solarman_v5, modbus_rtu or simulator)")
