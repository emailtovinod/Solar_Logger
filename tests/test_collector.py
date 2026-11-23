import os

import pytest

from solar_logger.collector import Collector, poll_once
from solar_logger.registers import load_map
from solar_logger.sources.simulator import SimulatorSource

MAP = load_map("deye_p3")


class FlakySource(SimulatorSource):
    """Fails every read whose start address is in `bad`."""

    def __init__(self, regmap, bad):
        super().__init__(regmap)
        self.bad = bad
        self.closed = 0

    def read(self, start, count):
        if start in self.bad:
            raise TimeoutError("no response")
        return super().read(start, count)

    def close(self):
        self.closed += 1


class FakeStorage:
    def __init__(self):
        self.rows = []
        self.fail = False

    def write(self, polls):
        if self.fail:
            raise ConnectionError("db down")
        self.rows.extend(polls)


def test_poll_reads_every_signal():
    poll = poll_once(SimulatorSource(MAP), MAP, "inv")
    assert poll.ok and poll.error is None
    assert set(poll.values) == {s.name for s in MAP.signals}
    assert 200 < poll.values["grid_l1_voltage"] < 260


def test_failed_block_leaves_gap_not_guess():
    blocks = MAP.blocks()
    src = FlakySource(MAP, bad={blocks[-1][0]})
    poll = poll_once(src, MAP, "inv")
    assert not poll.ok and "TimeoutError" in poll.error
    assert 0 < len(poll.values) < len(MAP.signals)
    assert src.closed == 1


def test_unreachable_device_stops_after_first_block():
    src = FlakySource(MAP, bad={b[0] for b in MAP.blocks()})
    poll = poll_once(src, MAP, "inv")
    assert not poll.ok and poll.values == {}
    assert src.closed == 1


def test_database_outage_is_buffered_then_flushed():
    storage = FakeStorage()
    c = Collector(SimulatorSource(MAP), MAP, storage, "inv", interval=1)
    storage.fail = True
    c.step()
    c.step()
    assert len(c.pending) == 2 and storage.rows == []
    storage.fail = False
    c.step()
    assert len(c.pending) == 0 and len(storage.rows) == 3


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_postgres_storage_roundtrip():
    import psycopg

    from solar_logger.storage import Storage

    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS samples, polls")
    storage = Storage(url)
    storage.init_schema()
    storage.init_schema()  # idempotent
    good = poll_once(SimulatorSource(MAP), MAP, "inv")
    bad = poll_once(FlakySource(MAP, bad={b[0] for b in MAP.blocks()}), MAP, "inv")
    storage.write([good, bad])
    storage.write([good])  # replay after a failed flush must not duplicate
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM samples").fetchone()[0] == len(MAP.signals)
        assert conn.execute("SELECT ok, signals, error IS NULL FROM polls ORDER BY time").fetchall() == [
            (True, len(MAP.signals), True),
            (False, 0, False),
        ]
        soc = conn.execute("SELECT value FROM samples WHERE signal = 'battery_soc'").fetchone()[0]
        assert soc == good.values["battery_soc"]
    storage.close()
