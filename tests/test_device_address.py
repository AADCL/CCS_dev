import socket
import unittest
from unittest.mock import patch

from ccs_monitor.device_address import (
    DeviceAddressError,
    DeviceAddressResolutionError,
    clear_device_address_cache,
    device_address_matches,
    format_device_address_for_url,
    normalize_device_address,
    resolve_device_addresses,
)


def mdns_resolver(host, port, family, socket_type):
    if host != "nrc17.local":
        raise socket.gaierror(f"unknown host: {host}")
    return [
        (socket.AF_INET6, socket.SOCK_DGRAM, 17, "", ("fe80::17", port, 0, 0)),
        (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.168.50.17", port)),
    ]


class DeviceAddressTests(unittest.TestCase):
    def test_normalizes_ip_and_mdns_hostname(self):
        self.assertEqual(normalize_device_address(" 192.168.50.17 "), "192.168.50.17")
        self.assertEqual(normalize_device_address(" NRC17.LOCAL. "), "nrc17.local")

    def test_rejects_regular_dns_and_invalid_mdns_names(self):
        for value in ("example.com", "nrc17", "-bad.local", "bad_.local"):
            with self.subTest(value=value), self.assertRaises(DeviceAddressError):
                normalize_device_address(value)

    def test_resolves_mdns_with_ipv4_preferred(self):
        self.assertEqual(
            resolve_device_addresses("nrc17.local", resolver=mdns_resolver),
            ("192.168.50.17", "fe80::17"),
        )

    def test_matches_resolved_mdns_to_observed_peer_ip(self):
        self.assertTrue(
            device_address_matches(
                "nrc17.local", "192.168.50.17", resolver=mdns_resolver
            )
        )
        self.assertFalse(
            device_address_matches(
                "nrc17.local", "192.168.50.99", resolver=mdns_resolver
            )
        )

    def test_resolution_failure_is_explicit(self):
        def failing_resolver(*_args):
            raise socket.gaierror("not found")

        with self.assertRaises(DeviceAddressResolutionError):
            resolve_device_addresses("nrc17.local", resolver=failing_resolver)

    def test_default_resolver_falls_back_to_multicast_dns(self):
        with (
            patch("ccs_monitor.device_address.socket.getaddrinfo", side_effect=socket.gaierror("not found")),
            patch("ccs_monitor.device_address._resolve_multicast_dns", return_value=("192.168.50.17",)),
        ):
            clear_device_address_cache()
            self.assertEqual(
                resolve_device_addresses("nrc17.local"), ("192.168.50.17",)
            )
            clear_device_address_cache()

    def test_formats_mdns_and_ipv6_for_urls(self):
        self.assertEqual(format_device_address_for_url("nrc17.local"), "nrc17.local")
        self.assertEqual(format_device_address_for_url("2001:db8::1"), "[2001:db8::1]")


if __name__ == "__main__":
    unittest.main()
