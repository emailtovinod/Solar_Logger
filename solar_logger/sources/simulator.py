"""Fake inverter for running the pipeline without hardware. Values are made up."""

from __future__ import annotations

import math
import time

from ..registers import RegisterMap

_BASE = {"W": 800.0, "V": 230.0, "A": 4.0, "Hz": 50.0, "%": 60.0, "°C": 30.0, "kWh": 5.0}


class SimulatorSource:
    def __init__(self, regmap: RegisterMap):
        self._map = regmap

    def read(self, start: int, count: int) -> list[int]:
        wave = math.sin(time.time() / 60)
        regs: dict[int, int] = {}
        for s in self._map.signals:
            base = _BASE.get(s.unit, 0.0)
            regs.update(s.encode(base * (1 + 0.1 * wave)))
        return [regs.get(a, 0) for a in range(start, start + count)]

    def close(self) -> None:
        pass
