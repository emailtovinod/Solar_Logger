import pytest

from solar_logger.registers import Signal, load_map


def test_decode_types():
    assert Signal("a", "W", "s16", (1,)).decode({1: 0xFFFE}) == -2
    assert Signal("a", "W", "u16", (1,)).decode({1: 0xFFFE}) == 65534
    # low word first
    assert Signal("a", "kWh", "u32", (1, 2), scale=0.1).decode({1: 0x0001, 2: 0x0001}) == 6553.7
    assert Signal("a", "W", "s32", (1, 9)).decode({1: 0xFFFF, 9: 0xFFFF}) == -1
    assert Signal("t", "°C", "u16", (1,), scale=0.1, offset=1000).decode({1: 1253}) == 25.3
    assert Signal("a", "W", "u32", (1, 2)).decode({1: 5}) is None


@pytest.mark.parametrize("model", ["deye_hybrid", "deye_p3"])
def test_bundled_maps(model):
    m = load_map(model)
    blocks = m.blocks()
    covered = {a for start, n in blocks for a in range(start, start + n)}
    assert all(a in covered for s in m.signals for a in s.registers)
    assert all(1 <= n <= 64 for _, n in blocks)
    assert blocks == sorted(blocks)
    # encode/decode round trip on every signal
    for s in m.signals:
        assert s.decode(s.encode(12.0)) == pytest.approx(12.0, abs=s.scale)
        if s.type.startswith("s"):
            assert s.decode(s.encode(-12.0)) == pytest.approx(-12.0, abs=s.scale)


def test_known_addresses():
    p3 = {s.name: s for s in load_map("deye_p3").signals}
    assert p3["battery_soc"].registers == (588,)
    assert p3["grid_power"].registers == (625, 690)
    hy = {s.name: s for s in load_map("deye_hybrid").signals}
    assert hy["battery_soc"].registers == (184,)
    assert hy["pv1_power"].registers == (186,)


def test_unknown_model():
    with pytest.raises(ValueError):
        load_map("nope")
