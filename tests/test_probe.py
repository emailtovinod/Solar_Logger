"""The probe's own V5 framing is checked against pysolarmanv5 and a fake logger."""
import importlib.util
import socket
import struct
import threading
from pathlib import Path

import pytest
from pysolarmanv5 import PySolarmanV5

spec = importlib.util.spec_from_file_location("probe", Path(__file__).parent.parent / "tools" / "probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

SERIAL = 1234567890
REGS = {a: a * 3 % 65536 for a in range(0, 800)}


def fake_logger(server):
    """Answers V5 read requests the way a logger stick does."""
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            while True:
                req = conn.recv(1024)
                if not req:
                    break
                modbus = req[26:-2]
                _, _, start, count = struct.unpack(">BBHH", modbus[:6])
                if start + count - 1 not in REGS:
                    break  # unsupported range: drop the connection
                body = bytes([1, 3, 2 * count]) + b"".join(struct.pack(">H", REGS[a]) for a in range(start, start + count))
                body += probe.crc16(body)
                payload = b"\x02\x01" + bytes(12) + body
                frame = b"\xa5" + struct.pack("<H", len(payload)) + b"\x10\x15" + req[5:7] + req[7:11] + payload
                conn.sendall(frame + bytes([sum(frame[1:]) & 0xFF]) + b"\x15")


@pytest.fixture
def logger_port():
    server = socket.create_server(("127.0.0.1", 0))
    threading.Thread(target=fake_logger, args=(server,), daemon=True).start()
    yield server.getsockname()[1]
    server.close()


def test_request_frame_matches_pysolarmanv5(logger_port):
    modbus = probe.modbus_request(588, 2)
    lib = PySolarmanV5("127.0.0.1", SERIAL, port=logger_port, socket_timeout=2)
    expected = bytes(lib._v5_frame_encoder(modbus))
    lib.disconnect()
    mine = bytearray(probe.v5_request(SERIAL, modbus))
    # sequence number is chosen by each implementation; align it and recompute checksum
    mine[5:7] = expected[5:7]
    mine[-2] = sum(mine[1:-2]) & 0xFF
    assert bytes(mine) == expected


def test_probe_reads_fake_logger(logger_port):
    assert probe.read_v5("127.0.0.1", logger_port, SERIAL, 588, 3, 2) == [REGS[588], REGS[589], REGS[590]]


def test_collector_driver_reads_fake_logger(logger_port):
    from solar_logger.collector import poll_once
    from solar_logger.registers import load_map
    from solar_logger.sources import make_source

    m = load_map("deye_p3")
    src = make_source({"type": "solarman_v5", "host": "127.0.0.1", "serial": SERIAL, "port": logger_port, "timeout": 2}, m)
    poll = poll_once(src, m, "inv")
    src.close()
    assert poll.ok, poll.error
    assert poll.values["battery_soc"] == REGS[588]


def test_full_scan_and_stream(logger_port, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe.time, "sleep", lambda s: None)
    monkeypatch.setattr(probe, "SCAN_RANGES", ((0, 120), (5000, 5050)))
    read = probe.check_modbus("127.0.0.1", logger_port, SERIAL, 2)
    assert read is not None
    probe.full_scan(read)
    probe.stream_test(read, rounds=3)
    rows = (tmp_path / "probe_registers.csv").read_text().splitlines()
    assert len(rows) == 121 and rows[6] == "5,0x000f,15,15"
    text = "\n".join(probe.lines)
    assert "range 5000-5049: 0 registers read, 50 failed" in text
    assert "ok 3/3, failed 0" in text


def test_port_scan_finds_fake_logger(logger_port):
    assert logger_port in probe.scan_all_ports("127.0.0.1", timeout=0.5)
