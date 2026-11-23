#!/usr/bin/env python3
"""Check what a Deye/Solarman data logger exposes on the local network.

Standard library only; run it on a computer on the same network as the logger:

    python3 probe.py                 # logger's own hotspot (10.10.100.254)
    python3 probe.py 192.168.1.50    # logger on the home network
    python3 probe.py 192.168.1.50 --serial 1234567890

It only reads. The report is printed and saved to probe_report.txt.
"""

from __future__ import annotations

import argparse
import base64
import re
import socket
import struct
import sys
import urllib.error
import urllib.request

TCP_PORTS = {80: "web config page", 8899: "Solarman V5 / Modbus", 502: "Modbus TCP", 30003: "alt. data port", 443: "HTTPS"}
DISCOVERY = (b"WIFIKIT-214028-READ", b"HF-A11ASSISTHREAD")
lines: list[str] = []


def out(text: str = "") -> None:
    print(text)
    lines.append(text)


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def modbus_request(start: int, count: int, slave: int = 1) -> bytes:
    body = struct.pack(">BBHH", slave, 3, start, count)
    return body + crc16(body)


def parse_modbus(frame: bytes, count: int, slave: int = 1) -> list[int]:
    """Parse a Modbus RTU read-holding-registers response."""
    if len(frame) >= 5 and frame[0] == slave and frame[1] == 0x83:
        raise ValueError(f"Modbus exception code {frame[2]}")
    need = 3 + 2 * count + 2
    if len(frame) < need or frame[0] != slave or frame[1] != 3 or frame[2] != 2 * count:
        raise ValueError(f"unexpected Modbus reply {frame.hex()}")
    if crc16(frame[: need - 2]) != frame[need - 2 : need]:
        raise ValueError("Modbus CRC mismatch")
    return list(struct.unpack(f">{count}H", frame[3 : 3 + 2 * count]))


def v5_request(serial: int, modbus: bytes, seq: int = 1) -> bytes:
    payload = b"\x02" + bytes(14) + modbus  # frame type, sensor type, three time fields
    head = b"\xa5" + struct.pack("<H", len(payload)) + b"\x10\x45" + struct.pack("<H", seq) + struct.pack("<I", serial)
    body = head + payload
    return body + bytes([sum(body[1:]) & 0xFF]) + b"\x15"


def v5_unwrap(frame: bytes) -> bytes:
    """Return the Modbus frame inside a V5 response."""
    if len(frame) < 29 or frame[0] != 0xA5 or frame[-1] != 0x15:
        raise ValueError(f"not a V5 frame: {frame.hex()}")
    if sum(frame[1:-2]) & 0xFF != frame[-2]:
        raise ValueError("V5 checksum mismatch")
    if frame[3:5] != b"\x10\x15":
        raise ValueError(f"unexpected V5 control code {frame[3:5].hex()}")
    return frame[25:-2]


def exchange(host: str, port: int, request: bytes, timeout: float) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(request)
        return s.recv(1024)


def read_v5(host, port, serial, start, count, timeout):
    return parse_modbus(v5_unwrap(exchange(host, port, v5_request(serial, modbus_request(start, count)), timeout)), count)


def read_rtu_tcp(host, port, start, count, timeout):
    return parse_modbus(exchange(host, port, modbus_request(start, count), timeout), count)


def read_modbus_tcp(host, port, start, count, timeout):
    req = struct.pack(">HHHBBHH", 1, 0, 6, 1, 3, start, count)
    resp = exchange(host, port, req, timeout)
    if len(resp) < 9 + 2 * count or resp[7] != 3:
        raise ValueError(f"unexpected Modbus TCP reply {resp.hex()}")
    return list(struct.unpack(f">{count}H", resp[9 : 9 + 2 * count]))


def check_tcp(host: str, timeout: float) -> dict[int, bool]:
    out("== TCP ports ==")
    result = {}
    for port, what in TCP_PORTS.items():
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            result[port] = True
            out(f"  {port:<6} OPEN    {what}")
        except OSError as exc:
            result[port] = False
            out(f"  {port:<6} closed  {what} ({type(exc).__name__})")
    return result


def check_udp(host: str, timeout: float) -> int | None:
    """Ask the logger to identify itself. Reply format: IP,MAC,SERIAL."""
    out("\n== UDP 48899 discovery ==")
    serial = None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    got = False
    for msg in DISCOVERY:
        for target in (host, "255.255.255.255"):
            try:
                s.sendto(msg, (target, 48899))
                data, addr = s.recvfrom(1024)
            except OSError:
                continue
            got = True
            text = data.decode("latin-1").strip()
            out(f"  reply to {msg.decode()} from {addr[0]}: {text}")
            parts = text.split(",")
            if len(parts) >= 3 and parts[2].strip().isdigit():
                serial = int(parts[2])
            break
    s.close()
    if not got:
        out("  no reply")
    return serial


def check_http(host: str, timeout: float) -> None:
    out("\n== Web page (port 80) ==")
    for path, auth in (("/", None), ("/status.html", "admin:admin")):
        req = urllib.request.Request(f"http://{host}{path}")
        if auth:
            req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(200_000).decode("latin-1", "replace")
                out(f"  GET {path}: {r.status}, server={r.headers.get('Server')}, {len(body)} bytes")
                for name, value in re.findall(r'var\s+(cover_\w+|webData\w*|status_\w)\s*=\s*"([^"]*)"', body):
                    if "pwd" not in name.lower() and "key" not in name.lower():
                        out(f"    {name} = {value}")
        except urllib.error.HTTPError as exc:
            out(f"  GET {path}: HTTP {exc.code} (server={exc.headers.get('Server')})")
        except OSError as exc:
            out(f"  GET {path}: failed ({exc})")


def check_modbus(host: str, port: int, serial: int | None, timeout: float) -> bool:
    out(f"\n== Register read on port {port} ==")
    attempts = []
    if serial is not None:
        attempts.append(("Solarman V5", lambda a, n: read_v5(host, port, serial, a, n, timeout)))
    else:
        out("  no numeric logger serial known, skipping Solarman V5 (pass --serial)")
    attempts.append(("Modbus RTU over TCP", lambda a, n: read_rtu_tcp(host, port, a, n, timeout)))
    attempts.append(("Modbus TCP", lambda a, n: read_modbus_tcp(host, port, a, n, timeout)))
    for name, read in attempts:
        try:
            head = read(0, 8)
        except Exception as exc:
            out(f"  {name}: failed ({type(exc).__name__}: {exc})")
            continue
        out(f"  {name}: WORKS")
        out(f"    registers 0-7: {head}")
        sn = b"".join(struct.pack(">H", w) for w in head[3:8]).decode("latin-1", "replace")
        out(f"    device type: 0x{head[0]:04x}, inverter serial: {sn!r}")
        for label, addr in (("single-phase map: battery SOC", 184), ("three-phase map: battery SOC", 588),
                            ("single-phase map: grid voltage", 150), ("three-phase map: grid L1 voltage", 598)):
            try:
                out(f"    register {addr} ({label}): raw {read(addr, 1)[0]}")
            except Exception as exc:
                out(f"    register {addr} ({label}): failed ({exc})")
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", nargs="?", default="10.10.100.254", help="logger IP (default: its hotspot address)")
    ap.add_argument("--serial", type=int, help="numeric logger serial for Solarman V5")
    ap.add_argument("--timeout", type=float, default=4)
    args = ap.parse_args()

    out(f"Probing logger at {args.host}")
    ports = check_tcp(args.host, args.timeout)
    found = check_udp(args.host, args.timeout)
    serial = args.serial or found
    if ports.get(80):
        check_http(args.host, args.timeout)
    working = False
    for port in (8899, 502, 30003):
        if ports.get(port):
            working = check_modbus(args.host, port, serial, args.timeout) or working

    out("\n== Result ==")
    if working:
        out("  Local register access works. The collector can read this logger directly.")
    elif not any(ports.values()):
        out("  Nothing reachable. Check the IP address and that this computer is on the logger's network.")
    else:
        out("  Logger is reachable but did not answer register reads. Local Modbus is probably")
        out("  disabled in this firmware; the RS485 adapter or the cloud API is the way in.")
    with open("probe_report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\nSaved to probe_report.txt")
    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())
