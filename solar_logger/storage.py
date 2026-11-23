"""PostgreSQL storage. Uses TimescaleDB hypertables when the extension is available."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    time   timestamptz      NOT NULL,
    device text             NOT NULL,
    signal text             NOT NULL,
    value  double precision NOT NULL,
    PRIMARY KEY (device, signal, time)
);
CREATE INDEX IF NOT EXISTS samples_time_idx ON samples (time DESC);

-- One row per poll attempt, so gaps and their causes are visible.
CREATE TABLE IF NOT EXISTS polls (
    time        timestamptz NOT NULL,
    device      text        NOT NULL,
    ok          boolean     NOT NULL,
    duration_ms integer     NOT NULL,
    signals     integer     NOT NULL,
    error       text,
    PRIMARY KEY (device, time)
);
"""


@dataclass
class Poll:
    time: datetime
    device: str
    values: dict[str, float] = field(default_factory=dict)
    ok: bool = True
    duration_ms: int = 0
    error: str | None = None


class Storage:
    def __init__(self, url: str):
        self._url = url
        self._conn: psycopg.Connection | None = None

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._url, autocommit=True, connect_timeout=10)
        return self._conn

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def init_schema(self) -> bool:
        """Create tables. Returns True if TimescaleDB hypertables are in use."""
        conn = self._connection()
        conn.execute(SCHEMA)
        try:
            with conn.transaction():
                conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        except psycopg.Error as exc:
            log.info("TimescaleDB not available, using plain tables (%s)", str(exc).splitlines()[0])
            return False
        conn.execute("SELECT create_hypertable('samples', 'time', if_not_exists => TRUE, migrate_data => TRUE)")
        return True

    def write(self, polls: list[Poll]) -> None:
        """Store polls atomically. Re-writing the same poll is a no-op."""
        if not polls:
            return
        conn = self._connection()
        try:
            with conn.transaction(), conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO polls (time, device, ok, duration_ms, signals, error) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    [(p.time, p.device, p.ok, p.duration_ms, len(p.values), p.error) for p in polls],
                )
                cur.executemany(
                    "INSERT INTO samples (time, device, signal, value) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    [(p.time, p.device, name, value) for p in polls for name, value in p.values.items()],
                )
        except psycopg.OperationalError:
            self.close()  # reconnect on the next write
            raise
