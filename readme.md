# Teltonika TCP Monitor

Python console server for Teltonika trackers configured to send TCP AVL data to port `8090` using **Codec 8** (the configuration shown in the attached screenshots).

The server:

1. Listens on Linux on TCP port `8090`.
2. Accepts the Teltonika IMEI handshake and replies with `0x01`.
3. Decodes received Codec 8 / Codec 8 Extended AVL packets.
4. Prints decoded JSON to the console.
5. Acknowledges the tracker with the number of decoded records.

## Run

```bash
python3 teltonika_server.py --host 0.0.0.0 --port 8090
```

If you are using a firewall, open TCP port `8090`:

```bash
sudo ufw allow 8090/tcp
```

Point the tracker server settings to the Linux machine public IP or DNS name and port `8090` with TCP protocol selected.


## FMC650 Codec 8E simulator

Use `teltonika_simulator.py` to emulate FMC650-like devices that connect with
different IMEIs and send one Codec 8 Extended AVL packet at a time. Each packet
contains simulated GNSS data plus static/simulated CAN/FMS-style AVL IO values,
including ignition, movement, engine RPM, fuel level, odometer, engine hours,
engine temperature, VIN, total fuel used, and total mileage. The simulator
generates the Teltonika CRC-16/IBM field and verifies the server ACK after every
packet.

Start the monitor in one terminal:

```bash
python3 teltonika_server.py --host 127.0.0.1 --port 8090
```

Run all built-in virtual devices from another terminal:

```bash
python3 teltonika_simulator.py --host 127.0.0.1 --port 8090 --packets 3 --interval 1
```

Send only one static device profile:

```bash
python3 teltonika_simulator.py --device fmc650-truck-01 --static --packets 1
```

Available profiles are `fmc650-truck-01`, `fmc650-bus-02`, and
`fmc650-reefer-03`.

## Console output

Each incoming packet is printed as formatted JSON. The output includes the tracker IMEI, remote address, timestamp converted to the machine local timezone, GPS coordinates, speed, satellites, priority, and IO element values keyed by Teltonika IO ID, ordered numerically by AVL Property ID.

## Notes

- The script uses only the Python standard library.
- It is intended for a simple TCP listener/decoder workflow and does not store data.
- The acknowledgement is protocol-correct for Teltonika TCP AVL packets: a 4-byte big-endian integer containing the number of records successfully decoded.
