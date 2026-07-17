#!/usr/bin/env python3
"""Teltonika FMC650 Codec 8 Extended simulator.

Emulates one or more Teltonika-like devices by opening TCP connections,
sending the IMEI handshake, and then sending one Codec 8E AVL packet at a
time with simulated GPS and CAN/FMS-style IO values.
"""
from __future__ import annotations

import argparse
import math
import socket
import struct
import time
from dataclasses import dataclass
from typing import Iterable

CODEC_8E = 0x8E
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8090


@dataclass(frozen=True)
class IoValue:
    """One Teltonika AVL IO value."""

    avl_id: int
    value: int | bytes
    size: int | None = None

    @property
    def encoded_size(self) -> int:
        if isinstance(self.value, bytes):
            return len(self.value)
        if self.size is None:
            raise ValueError(f"numeric AVL ID {self.avl_id} must declare size")
        return self.size


@dataclass(frozen=True)
class DeviceProfile:
    """Static identity and movement seed for a virtual FMC650 device."""

    name: str
    imei: str
    base_latitude: float
    base_longitude: float
    heading: int
    speed_kmh: int
    fuel_percent: int
    odometer_km: int
    vin: str


@dataclass(frozen=True)
class AvlRecord:
    timestamp_ms: int
    priority: int
    longitude: float
    latitude: float
    altitude_m: int
    angle_deg: int
    satellites: int
    speed_kmh: int
    event_io_id: int
    io_values: tuple[IoValue, ...]


PROFILES = (
    DeviceProfile(
        name="fmc650-truck-01",
        imei="356307042441013",
        base_latitude=54.6872,
        base_longitude=25.2797,
        heading=85,
        speed_kmh=72,
        fuel_percent=68,
        odometer_km=482_350,
        vin="TRUCKFMC65000001",
    ),
    DeviceProfile(
        name="fmc650-bus-02",
        imei="356307042441021",
        base_latitude=52.2297,
        base_longitude=21.0122,
        heading=210,
        speed_kmh=44,
        fuel_percent=52,
        odometer_km=318_120,
        vin="BUSFMC650000002",
    ),
    DeviceProfile(
        name="fmc650-reefer-03",
        imei="356307042441039",
        base_latitude=59.4370,
        base_longitude=24.7536,
        heading=15,
        speed_kmh=64,
        fuel_percent=74,
        odometer_km=623_775,
        vin="REEFERFMC6500003",
    ),
)


def crc16_ibm(data: bytes) -> int:
    """Return Teltonika CRC-16/IBM for AVL data."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
            crc &= 0xFFFF
    return crc


def pack_i32_coordinate(value: float) -> bytes:
    return struct.pack(">i", int(round(value * 10_000_000)))


def group_io_values(values: Iterable[IoValue]) -> tuple[list[IoValue], list[IoValue], list[IoValue], list[IoValue], list[IoValue]]:
    groups = {1: [], 2: [], 4: [], 8: []}
    variable: list[IoValue] = []
    for value in sorted(values, key=lambda item: item.avl_id):
        size = value.encoded_size
        if isinstance(value.value, bytes) or size not in groups:
            variable.append(value)
        else:
            groups[size].append(value)
    return groups[1], groups[2], groups[4], groups[8], variable


def encode_io_values(event_io_id: int, values: tuple[IoValue, ...]) -> bytes:
    one, two, four, eight, variable = group_io_values(values)
    encoded = bytearray(struct.pack(">HH", event_io_id, len(values)))
    for size, group in ((1, one), (2, two), (4, four), (8, eight)):
        encoded += struct.pack(">H", len(group))
        for item in group:
            if not isinstance(item.value, int):
                raise ValueError(f"fixed AVL ID {item.avl_id} must be numeric")
            encoded += struct.pack(">H", item.avl_id)
            encoded += int(item.value).to_bytes(size, "big", signed=False)
    encoded += struct.pack(">H", len(variable))
    for item in variable:
        raw = item.value if isinstance(item.value, bytes) else int(item.value).to_bytes(item.encoded_size, "big")
        encoded += struct.pack(">HH", item.avl_id, len(raw)) + raw
    return bytes(encoded)


def encode_record(record: AvlRecord) -> bytes:
    return b"".join(
        (
            struct.pack(">QB", record.timestamp_ms, record.priority),
            pack_i32_coordinate(record.longitude),
            pack_i32_coordinate(record.latitude),
            struct.pack(">hH", record.altitude_m, record.angle_deg),
            struct.pack(">BH", record.satellites, record.speed_kmh),
            encode_io_values(record.event_io_id, record.io_values),
        )
    )


def encode_packet(record: AvlRecord) -> bytes:
    """Build one Codec 8E AVL packet containing exactly one record."""
    avl_data = bytes((CODEC_8E, 1)) + encode_record(record) + bytes((1,))
    crc = crc16_ibm(avl_data)
    return struct.pack(">II", 0, len(avl_data)) + avl_data + struct.pack(">I", crc)


def simulated_position(profile: DeviceProfile, sequence: int) -> tuple[float, float, int, int]:
    distance_degrees = sequence * max(profile.speed_kmh, 1) * 0.000002
    heading_rad = math.radians(profile.heading)
    latitude = profile.base_latitude + math.cos(heading_rad) * distance_degrees
    longitude = profile.base_longitude + math.sin(heading_rad) * distance_degrees
    angle = (profile.heading + sequence * 2) % 360
    speed = max(0, profile.speed_kmh + int(4 * math.sin(sequence / 3)))
    return latitude, longitude, angle, speed


def build_record(profile: DeviceProfile, sequence: int, static: bool) -> AvlRecord:
    latitude, longitude, angle, speed = simulated_position(profile, 0 if static else sequence)
    ignition = 1
    movement = 0 if static else int(speed > 0)
    rpm = 700 if static else 1250 + int(250 * math.sin(sequence / 4))
    fuel = max(0, profile.fuel_percent - sequence // 20)
    odometer = profile.odometer_km if static else profile.odometer_km + sequence
    engine_temp = 86 + int(3 * math.sin(sequence / 5))
    total_fuel_used = 92_000 + sequence * 2
    total_engine_hours = 12_300 + sequence

    io_values = (
        IoValue(21, min(5, 4 + sequence % 2), 1),  # GSM signal
        IoValue(69, 1, 1),  # GNSS status
        IoValue(80, 4, 1),  # data mode
        IoValue(85, rpm, 2),  # engine RPM / CAN-style data
        IoValue(87, odometer, 4),  # total mileage
        IoValue(89, fuel, 1),  # fuel level percentage
        IoValue(102, total_engine_hours, 4),  # engine hours
        IoValue(115, engine_temp, 2),  # engine temperature
        IoValue(200, 0, 1),  # sleep mode
        IoValue(239, ignition, 1),  # ignition
        IoValue(240, movement, 1),  # movement
        IoValue(12002, profile.vin.encode("ascii")),  # FMS vehicle VIN, variable-size Codec 8E IO
        IoValue(12028, total_fuel_used, 4),  # FMS total fuel used
        IoValue(12036, odometer, 4),  # FMS total mileage
    )
    return AvlRecord(
        timestamp_ms=int(time.time() * 1000),
        priority=0,
        longitude=longitude,
        latitude=latitude,
        altitude_m=120 + sequence % 9,
        angle_deg=angle,
        satellites=12,
        speed_kmh=speed,
        event_io_id=240,
        io_values=io_values,
    )


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("remote closed the connection")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_imei(sock: socket.socket, imei: str) -> None:
    raw_imei = imei.encode("ascii")
    sock.sendall(struct.pack(">H", len(raw_imei)) + raw_imei)
    accepted = recv_exact(sock, 1)
    if accepted != b"\x01":
        raise ConnectionError(f"IMEI {imei} rejected with response {accepted.hex()}")


def run_device(profile: DeviceProfile, host: str, port: int, packets: int, interval: float, static: bool) -> None:
    with socket.create_connection((host, port), timeout=10) as sock:
        send_imei(sock, profile.imei)
        print(f"{profile.name} ({profile.imei}) accepted by {host}:{port}", flush=True)
        for sequence in range(packets):
            record = build_record(profile, sequence, static)
            packet = encode_packet(record)
            sock.sendall(packet)
            ack = struct.unpack(">I", recv_exact(sock, 4))[0]
            if ack != 1:
                raise ConnectionError(f"expected ACK 1 from server, received {ack}")
            print(
                f"{profile.name} sent packet {sequence + 1}/{packets}: "
                f"lat={record.latitude:.6f} lon={record.longitude:.6f} "
                f"speed={record.speed_kmh} crc=0x{packet[-4:].hex()}",
                flush=True,
            )
            if sequence + 1 < packets:
                time.sleep(interval)


def select_profiles(names: list[str]) -> list[DeviceProfile]:
    by_name = {profile.name: profile for profile in PROFILES}
    if not names or names == ["all"]:
        return list(PROFILES)
    selected = []
    for name in names:
        try:
            selected.append(by_name[name])
        except KeyError as exc:
            choices = ", ".join(sorted(by_name))
            raise SystemExit(f"unknown profile {name!r}; choose one of: all, {choices}") from exc
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="FMC650 Codec 8E multi-device simulator")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"server host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"server TCP port (default: {DEFAULT_PORT})")
    parser.add_argument("--packets", type=int, default=3, help="AVL packets to send per device (default: 3)")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between packets per device (default: 1.0)")
    parser.add_argument(
        "--device",
        action="append",
        default=[],
        help="device profile name; repeat for multiple devices or omit/use 'all' for every profile",
    )
    parser.add_argument("--static", action="store_true", help="send static position/CAN values instead of simulated movement")
    args = parser.parse_args()

    if args.packets < 1:
        raise SystemExit("--packets must be at least 1")
    for profile in select_profiles(args.device):
        run_device(profile, args.host, args.port, args.packets, args.interval, args.static)


if __name__ == "__main__":
    main()
