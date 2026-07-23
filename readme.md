# Teltonika TCP Monitor

Python console server for Teltonika trackers configured to send TCP AVL data to port `8090` using **Codec 8** (the configuration shown in the attached screenshots).

The server:

1. Listens on Linux on TCP port `8090`.
2. Accepts the Teltonika IMEI handshake and replies with `0x01`.
3. Decodes received Codec 8 / Codec 8 Extended AVL packets.
4. Prints decoded JSON to the console.
5. Acknowledges the tracker with the advertised number of records, even when a packet cannot be fully decoded, so simulation/error bursts do not make the sender back off waiting for an ACK.

## Run

```bash
python3 teltonika_server.py --host 0.0.0.0 --port 8090
```

If you are using a firewall, open TCP port `8090`:

```bash
sudo ufw allow 8090/tcp
```

Point the tracker server settings to the Linux machine public IP or DNS name and port `8090` with TCP protocol selected.

## Console output

Each incoming packet is printed as formatted JSON. The output includes the tracker IMEI, remote address, timestamp converted to the machine local timezone, GPS coordinates, speed, satellites, priority, and IO element values keyed by Teltonika IO ID, ordered numerically by AVL Property ID.

## Notes

- The script uses only the Python standard library.
- It is intended for a simple TCP listener/decoder workflow and does not store data.
- The acknowledgement is a 4-byte big-endian integer containing the AVL record count advertised by the packet. If decoding fails after the record count is readable, the server still sends that acknowledgement and logs the decode error plus raw packet bytes so simulations continue sending instead of backing off.
