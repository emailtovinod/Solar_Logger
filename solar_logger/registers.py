"""Register maps: which Modbus holding registers to read and how to decode them."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

_TYPES = {"u16": (1, False), "s16": (1, True), "u32": (2, False), "s32": (2, True)}


@dataclass(frozen=True)
class Signal:
    name: str
    unit: str
    type: str
    registers: tuple[int, ...]  # low word first
    scale: float = 1.0
    offset: float = 0.0

    def decode(self, regs: dict[int, int]) -> float | None:
        """Return the engineering value, or None if any register is missing."""
        raw = 0
        for i, addr in enumerate(self.registers):
            word = regs.get(addr)
            if word is None:
                return None
            raw |= (word & 0xFFFF) << (16 * i)
        bits = 16 * len(self.registers)
        if _TYPES[self.type][1] and raw >= 1 << (bits - 1):
            raw -= 1 << bits
        return round((raw - self.offset) * self.scale, 6)

    def encode(self, value: float) -> dict[int, int]:
        """Inverse of decode; used by the simulator and tests."""
        bits = 16 * len(self.registers)
        raw = round(value / self.scale + self.offset) & ((1 << bits) - 1)
        return {addr: (raw >> (16 * i)) & 0xFFFF for i, addr in enumerate(self.registers)}


@dataclass(frozen=True)
class RegisterMap:
    model: str
    signals: tuple[Signal, ...]

    def blocks(self, max_gap: int = 8, max_size: int = 64) -> list[tuple[int, int]]:
        """Group needed addresses into (start, count) read requests.

        Addresses closer than max_gap are read in one request, since one larger
        read is cheaper than two round trips through the logger.
        """
        addrs = sorted({a for s in self.signals for a in s.registers})
        blocks: list[tuple[int, int]] = []
        start = prev = addrs[0]
        for a in addrs[1:]:
            if a - prev > max_gap or a - start + 1 > max_size:
                blocks.append((start, prev - start + 1))
                start = a
            prev = a
        blocks.append((start, prev - start + 1))
        return blocks

    def decode(self, regs: dict[int, int]) -> dict[str, float]:
        """Decode every signal whose registers were all read."""
        out = {}
        for s in self.signals:
            v = s.decode(regs)
            if v is not None:
                out[s.name] = v
        return out


def load_map(model_or_path: str) -> RegisterMap:
    """Load a bundled map by model name, or a custom map from a YAML file path."""
    path = Path(model_or_path)
    if path.suffix in (".yaml", ".yml"):
        text = path.read_text(encoding="utf-8")
    else:
        res = resources.files("solar_logger.maps") / f"{model_or_path}.yaml"
        if not res.is_file():
            raise ValueError(f"unknown inverter model {model_or_path!r}")
        text = res.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    signals = []
    seen = set()
    for item in data["signals"]:
        typ = item["type"]
        regs = tuple(item["registers"])
        if typ not in _TYPES:
            raise ValueError(f"{item['name']}: unknown type {typ!r}")
        if len(regs) != _TYPES[typ][0]:
            raise ValueError(f"{item['name']}: type {typ} needs {_TYPES[typ][0]} register(s)")
        if item["name"] in seen:
            raise ValueError(f"duplicate signal name {item['name']!r}")
        seen.add(item["name"])
        signals.append(
            Signal(
                name=item["name"],
                unit=item.get("unit", ""),
                type=typ,
                registers=regs,
                scale=float(item.get("scale", 1)),
                offset=float(item.get("offset", 0)),
            )
        )
    if not signals:
        raise ValueError("register map has no signals")
    return RegisterMap(model=data.get("model", path.stem), signals=tuple(signals))
