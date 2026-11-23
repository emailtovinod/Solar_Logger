"""Direct RS485 connection to the inverter's Modbus port via a serial adapter."""

from __future__ import annotations

import minimalmodbus


class ModbusRtuSource:
    def __init__(self, device: str, baudrate: int = 9600, slave_id: int = 1, timeout: float = 2):
        self._device = device
        self._baudrate = baudrate
        self._slave_id = slave_id
        self._timeout = timeout
        self._inst: minimalmodbus.Instrument | None = None

    def read(self, start: int, count: int) -> list[int]:
        if self._inst is None:
            inst = minimalmodbus.Instrument(self._device, self._slave_id)
            inst.serial.baudrate = self._baudrate
            inst.serial.timeout = self._timeout
            self._inst = inst
        return self._inst.read_registers(start, count, functioncode=3)

    def close(self) -> None:
        inst, self._inst = self._inst, None
        if inst is not None:
            try:
                inst.serial.close()
            except Exception:
                pass
