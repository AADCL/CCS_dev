#!/usr/bin/env python3
"""Small SNTP client used because the on-site Windows NTP server times out timesyncd."""
import argparse
import socket
import struct
import sys
import time

NTP_DELTA = 2208988800


def query(server, timeout):
    packet = bytearray(48)
    packet[0] = 0x1B
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, 123))
        data, peer = sock.recvfrom(512)
    finally:
        sock.close()
    if len(data) < 48:
        raise RuntimeError("short NTP response")
    seconds, fraction = struct.unpack("!II", data[40:48])
    epoch = seconds - NTP_DELTA + fraction / float(1 << 32)
    if epoch < 1577836800:
        raise RuntimeError("NTP response predates 2020")
    return epoch, peer[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="192.168.50.101")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--retries", type=int, default=10)
    parser.add_argument("--max-offset", type=float, default=5.0)
    parser.add_argument("--set", action="store_true", dest="set_clock")
    args = parser.parse_args()
    last_error = None
    for attempt in range(max(1, args.retries)):
        try:
            epoch, peer = query(args.server, args.timeout)
            offset = epoch - time.time()
            if args.set_clock:
                time.clock_settime(time.CLOCK_REALTIME, epoch)
                offset = epoch - time.time()
            print("server={} utc={} offset_seconds={:.3f}".format(
                peer, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch)), offset))
            return 0 if abs(offset) <= args.max_offset else 2
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max(1, args.retries):
                time.sleep(2.0)
    print("SNTP query failed: {}".format(last_error), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
