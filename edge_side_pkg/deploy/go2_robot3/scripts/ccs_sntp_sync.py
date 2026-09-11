#!/usr/bin/env python3
"""Read-only SNTP availability or offset query; timesyncd owns clock adjustment."""
import argparse
import socket
import struct
import sys
import time

NTP_DELTA = 2208988800


def timestamp(raw):
    seconds, fraction = struct.unpack("!II", raw)
    return seconds - NTP_DELTA + fraction / float(1 << 32)


def query(server, timeout, availability_only=False):
    packet = bytearray(48)
    packet[0] = 0x23
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.connect((server, 123))
        sent = time.time()
        ntp_sent = sent + NTP_DELTA
        packet[40:48] = struct.pack("!II", int(ntp_sent), int((ntp_sent % 1) * (1 << 32)))
        sock.send(packet)
        data = sock.recv(512)
        received = None if availability_only else time.time()
        peer = sock.getpeername()[0]
    if len(data) < 48 or data[0] & 7 != 4:
        raise RuntimeError("invalid NTP server response")
    if data[24:32] != packet[40:48]:
        raise RuntimeError("NTP response does not match this request")
    if availability_only:
        return {"server": peer}
    if data[0] >> 6 == 3 or not 1 <= data[1] <= 15:
        raise RuntimeError("NTP server reports unsynchronized time")
    server_received = timestamp(data[32:40])
    server_sent = timestamp(data[40:48])
    if server_received < 1577836800 or server_sent < server_received:
        raise RuntimeError("invalid NTP server timestamps")
    offset = ((server_received - sent) + (server_sent - received)) / 2.0
    return {"server": peer, "offset_seconds": offset,
            "round_trip_seconds": received - sent - (server_sent - server_received)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="192.168.50.101")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-offset", type=float, default=2.0)
    parser.add_argument("--availability-only", action="store_true",
                        help="check only that the server responds; ignore clock offset")
    args = parser.parse_args()
    if args.timeout <= 0 or args.retries < 1 or args.max_offset <= 0:
        parser.error("timeout, retries and max-offset must be positive")
    last_error = None
    for attempt in range(args.retries):
        try:
            if args.availability_only:
                result = query(args.server, args.timeout, availability_only=True)
                print("server={server} available=true".format(**result))
                return 0
            result = query(args.server, args.timeout)
            print("server={server} offset_seconds={offset_seconds:.3f} "
                  "round_trip_seconds={round_trip_seconds:.3f}".format(**result))
            return 0 if abs(result["offset_seconds"]) <= args.max_offset else 2
        except (OSError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 < args.retries:
                time.sleep(0.5)
    print("SNTP query failed: {}".format(last_error), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
