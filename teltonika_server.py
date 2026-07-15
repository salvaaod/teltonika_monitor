#!/usr/bin/env python3
"""Small Teltonika Codec 8 TCP server for Linux.

Listens on port 8090, accepts the tracker IMEI handshake, decodes AVL data
packets, prints them to the console, and acknowledges received records.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import signal
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Any

SHUTDOWN_TIMEOUT_SECONDS = 2.0

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8090


class ProtocolError(Exception):
    """Raised when incoming data does not match the expected Teltonika format."""


@dataclass
class Reader:
    data: bytes
    pos: int = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, size: int) -> bytes:
        if self.remaining() < size:
            raise ProtocolError(f"need {size} bytes, only {self.remaining()} available")
        value = self.data[self.pos : self.pos + size]
        self.pos += size
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self.take(8))[0]

    def i16(self) -> int:
        return struct.unpack(">h", self.take(2))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]


def close_tcp_connection(conn: socket.socket) -> None:
    """Gracefully close a TCP channel by sending FIN before closing the socket."""
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    conn.close()


def recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = conn.recv(remaining)
        if not chunk:
            raise ConnectionError("client closed the connection")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def parse_imei(conn: socket.socket) -> str:
    imei_len = struct.unpack(">H", recv_exact(conn, 2))[0]
    if not 1 <= imei_len <= 32:
        raise ProtocolError(f"invalid IMEI length: {imei_len}")
    imei = recv_exact(conn, imei_len).decode("ascii", errors="replace")
    if not imei.isdigit():
        print(f"warning: IMEI contains non-digit characters: {imei!r}", flush=True)
    conn.sendall(b"\x01")
    return imei


def parse_io_elements(reader: Reader, codec_id: int) -> dict[str, Any]:
    event_io_id = reader.u16() if codec_id == 0x8E else reader.u8()
    total_io = reader.u16() if codec_id == 0x8E else reader.u8()
    values: dict[str, int] = {}

    id_reader = reader.u16 if codec_id == 0x8E else reader.u8
    count_reader = reader.u16 if codec_id == 0x8E else reader.u8

    for value_size in (1, 2, 4, 8):
        count = count_reader()
        for _ in range(count):
            io_id = id_reader()
            raw = reader.take(value_size)
            values[str(io_id)] = int.from_bytes(raw, "big", signed=False)

    if codec_id == 0x8E:
        count = reader.u16()
        for _ in range(count):
            io_id = reader.u16()
            value_len = reader.u16()
            values[str(io_id)] = reader.take(value_len).hex()

    if len(values) != total_io:
        print(f"warning: IO count mismatch, declared={total_io}, decoded={len(values)}", flush=True)

    return {"event_io_id": event_io_id, "total_io": total_io, "values": values}


def parse_avl_record(reader: Reader, codec_id: int) -> dict[str, Any]:
    timestamp_ms = reader.u64()
    priority = reader.u8()
    longitude = reader.i32() / 10_000_000
    latitude = reader.i32() / 10_000_000
    altitude = reader.i16()
    angle = reader.u16()
    satellites = reader.u8()
    speed = reader.u16()

    return {
        "timestamp": dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=dt.timezone.utc).isoformat(),
        "priority": priority,
        "gps": {
            "latitude": latitude,
            "longitude": longitude,
            "altitude_m": altitude,
            "angle_deg": angle,
            "satellites": satellites,
            "speed_kmh": speed,
        },
        "io": parse_io_elements(reader, codec_id),
    }


def parse_avl_packet(packet: bytes) -> tuple[int, dict[str, Any]]:
    reader = Reader(packet)
    preamble = reader.u32()
    if preamble != 0:
        raise ProtocolError(f"invalid preamble: 0x{preamble:08x}")

    data_len = reader.u32()
    avl_data = reader.take(data_len)
    crc = reader.u32()
    if reader.remaining() != 0:
        raise ProtocolError(f"unexpected trailing bytes: {reader.remaining()}")

    avl = Reader(avl_data)
    codec_id = avl.u8()
    if codec_id not in (0x08, 0x8E):
        raise ProtocolError(f"unsupported codec: 0x{codec_id:02x}")

    record_count = avl.u8()
    records = [parse_avl_record(avl, codec_id) for _ in range(record_count)]
    record_count_end = avl.u8()
    if record_count_end != record_count:
        raise ProtocolError(f"record count mismatch: {record_count} != {record_count_end}")
    if avl.remaining() != 0:
        raise ProtocolError(f"unparsed AVL bytes: {avl.remaining()}")

    decoded = {
        "codec": f"0x{codec_id:02x}",
        "record_count": record_count,
        "records": records,
        "crc16_from_device": f"0x{crc:08x}",
    }
    return record_count, decoded


def handle_client(conn: socket.socket, address: tuple[str, int]) -> None:
    with conn:
        print(f"client connected: {address[0]}:{address[1]}", flush=True)
        imei = parse_imei(conn)
        print(f"imei accepted: {imei}", flush=True)

        while True:
            header = recv_exact(conn, 8)
            data_len = struct.unpack(">I", header[4:8])[0]
            if data_len <= 0 or data_len > 1_000_000:
                raise ProtocolError(f"invalid AVL data length: {data_len}")
            body_and_crc = recv_exact(conn, data_len + 4)
            packet = header + body_and_crc
            record_count, decoded = parse_avl_packet(packet)
            decoded["imei"] = imei
            decoded["remote_address"] = f"{address[0]}:{address[1]}"
            print(json.dumps(decoded, indent=2, sort_keys=True), flush=True)
            conn.sendall(struct.pack(">I", record_count))
            print(f"acknowledged {record_count} record(s) for {imei}", flush=True)


def serve(host: str, port: int) -> None:
    stop_event = threading.Event()
    clients: set[socket.socket] = set()
    client_threads: list[threading.Thread] = []
    clients_lock = threading.Lock()

    def request_shutdown(signum: int, _frame: object) -> None:
        signal_name = signal.Signals(signum).name
        print(f"received {signal_name}; gracefully closing Teltonika TCP channels", flush=True)
        stop_event.set()

    previous_sigint = signal.signal(signal.SIGINT, request_shutdown)
    previous_sigterm = signal.signal(signal.SIGTERM, request_shutdown)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen()
            server.settimeout(0.5)
            print(f"listening for Teltonika TCP connections on {host}:{port}", flush=True)
            while not stop_event.is_set():
                try:
                    conn, address = server.accept()
                except socket.timeout:
                    continue
                with clients_lock:
                    clients.add(conn)
                thread = threading.Thread(
                    target=safe_handle_client,
                    args=(conn, address, clients, clients_lock),
                )
                thread.start()
                client_threads.append(thread)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        stop_event.set()
        with clients_lock:
            for client in list(clients):
                close_tcp_connection(client)
        for thread in client_threads:
            thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        print("server stopped", flush=True)


def safe_handle_client(
    conn: socket.socket,
    address: tuple[str, int],
    clients: set[socket.socket],
    clients_lock: threading.Lock,
) -> None:
    try:
        handle_client(conn, address)
    except ConnectionError as exc:
        print(f"client disconnected {address[0]}:{address[1]}: {exc}", flush=True)
    except OSError as exc:
        print(f"socket closed {address[0]}:{address[1]}: {exc}", flush=True)
    except Exception as exc:  # keep server alive for the next tracker connection
        print(f"connection error {address[0]}:{address[1]}: {exc}", flush=True)
    finally:
        with clients_lock:
            clients.discard(conn)
        close_tcp_connection(conn)


def main() -> None:
    parser = argparse.ArgumentParser(description="Teltonika Codec 8 TCP console decoder")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default: {DEFAULT_PORT})")
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
