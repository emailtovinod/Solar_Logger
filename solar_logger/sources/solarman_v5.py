"""Wi-Fi logger stick on the LAN: Solarman V5 protocol over TCP 8899."""

from __future__ import annotations

from pysolarmanv5 import PySolarmanV5


class SolarmanV5Source:
    def __init__(self, host: str, serial: int, port: int = 8899, slave_id: int = 1, timeout: float = 10):
        self._args = dict(
            address=host,
            serial=serial,
            port=port,
            mb_slave_id=slave_id,
            socket_timeout=timeout,
            auto_reconnect=False,
        )
        self._client: PySolarmanV5 | None = None

    def read(self, start: int, count: int) -> list[int]:
        if self._client is None:
            self._client = PySolarmanV5(**self._args)
        return list(self._client.read_holding_registers(start, count))

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
