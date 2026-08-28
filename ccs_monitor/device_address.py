from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from collections.abc import Callable
from typing import Any


MDNS_SUFFIX = ".local"
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RESOLUTION_CACHE_TTL_SECONDS = 10.0
_resolution_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
_resolution_cache_lock = threading.Lock()


class DeviceAddressError(ValueError):
    pass


class DeviceAddressResolutionError(OSError):
    pass


def normalize_device_address(value: str) -> str:
    """Return a canonical IP address or mDNS ``.local`` hostname."""
    if not isinstance(value, str) or not value.strip():
        raise DeviceAddressError("设备地址不能为空")
    candidate = value.strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        hostname = candidate.rstrip(".").casefold()
    if not hostname.endswith(MDNS_SUFFIX) or hostname == MDNS_SUFFIX.removeprefix("."):
        raise DeviceAddressError("设备地址必须是有效 IP 或以 .local 结尾的 mDNS 主机名")
    if len(hostname) > 253 or any(not _HOST_LABEL.fullmatch(label) for label in hostname.split(".")):
        raise DeviceAddressError(f"mDNS 主机名无效：{candidate}")
    return hostname


def is_mdns_hostname(value: str) -> bool:
    try:
        normalized = normalize_device_address(value)
        ipaddress.ip_address(normalized)
    except DeviceAddressError:
        return False
    except ValueError:
        return True
    return False


def clear_device_address_cache() -> None:
    with _resolution_cache_lock:
        _resolution_cache.clear()


def resolve_device_addresses(
    value: str,
    *,
    resolver: Callable[..., Any] | None = None,
    cache_ttl_seconds: float = _RESOLUTION_CACHE_TTL_SECONDS,
) -> tuple[str, ...]:
    """Resolve an IP or mDNS hostname to canonical IP strings.

    System ``getaddrinfo`` is used intentionally so the application follows the
    operating system's mDNS implementation and interface selection. Successful
    and failed default lookups are cached briefly because source checks can run
    at telemetry frequency.
    """
    normalized = normalize_device_address(value)
    try:
        return (_canonical_ip(normalized),)
    except ValueError:
        pass

    use_cache = resolver is None
    now = time.monotonic()
    if use_cache:
        with _resolution_cache_lock:
            cached = _resolution_cache.get(normalized)
        if cached is not None and cached[0] > now:
            if cached[1]:
                return cached[1]
            raise DeviceAddressResolutionError(f"无法解析 mDNS 主机名：{normalized}")

    lookup = resolver or socket.getaddrinfo
    try:
        records = lookup(normalized, 0, socket.AF_UNSPEC, socket.SOCK_DGRAM)
    except OSError as exc:
        if not use_cache:
            raise DeviceAddressResolutionError(f"无法解析 mDNS 主机名 {normalized}：{exc}") from exc
        try:
            addresses = _resolve_multicast_dns(normalized)
        except (DeviceAddressResolutionError, OSError, RuntimeError) as fallback_exc:
            _store_resolution(normalized, (), min(cache_ttl_seconds, 2.0), now)
            raise DeviceAddressResolutionError(f"无法解析 mDNS 主机名：{normalized}") from fallback_exc
        ordered = _ordered_addresses(addresses)
        _store_resolution(
            normalized,
            ordered,
            cache_ttl_seconds if ordered else min(cache_ttl_seconds, 2.0),
            now,
        )
        if not ordered:
            raise DeviceAddressResolutionError(f"无法解析 mDNS 主机名：{normalized}") from exc
        return ordered

    addresses: set[str] = set()
    for record in records:
        try:
            addresses.add(_canonical_ip(str(record[4][0])))
        except (IndexError, TypeError, ValueError):
            continue
    if not addresses and use_cache:
        try:
            addresses.update(_resolve_multicast_dns(normalized))
        except (DeviceAddressResolutionError, OSError, RuntimeError) as exc:
            _store_resolution(normalized, (), min(cache_ttl_seconds, 2.0), now)
            raise DeviceAddressResolutionError(f"无法解析 mDNS 主机名：{normalized}") from exc
    ordered = _ordered_addresses(addresses)
    if use_cache:
        _store_resolution(
            normalized,
            ordered,
            cache_ttl_seconds if ordered else min(cache_ttl_seconds, 2.0),
            now,
        )
    if not ordered:
        raise DeviceAddressResolutionError(f"mDNS 主机名没有可用地址：{normalized}")
    return ordered


def resolve_device_address(
    value: str,
    *,
    resolver: Callable[..., Any] | None = None,
) -> str:
    """Resolve one preferred address, using IPv4 first for existing UDP sockets."""
    return resolve_device_addresses(value, resolver=resolver)[0]


def device_address_matches(
    configured: str,
    observed: str,
    *,
    resolver: Callable[..., Any] | None = None,
) -> bool:
    """Compare an observed peer with a configured IP or mDNS hostname."""
    try:
        expected = normalize_device_address(configured)
        actual = normalize_device_address(observed)
    except DeviceAddressError:
        return False
    if expected == actual:
        return True
    try:
        actual_ip = _canonical_ip(actual)
        expected_addresses = set(resolve_device_addresses(expected, resolver=resolver))
    except (DeviceAddressResolutionError, ValueError):
        return False
    return actual_ip in expected_addresses


def format_device_address_for_url(value: str) -> str:
    normalized = normalize_device_address(value)
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized
    return f"[{address}]" if address.version == 6 else str(address)


def _canonical_ip(value: str) -> str:
    # ``recvfrom`` and ``getaddrinfo`` may append an IPv6 scope identifier.
    candidate = value.split("%", 1)[0]
    return str(ipaddress.ip_address(candidate))


def _ordered_addresses(addresses: set[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(
        {_canonical_ip(item) for item in addresses},
        key=lambda item: (ipaddress.ip_address(item).version, item),
    ))


def _resolve_multicast_dns(hostname: str, timeout_seconds: float = 1.5) -> tuple[str, ...]:
    """Query A/AAAA records over multicast when the OS resolver lacks mDNS."""
    try:
        from zeroconf import (
            DNSAddress,
            DNSOutgoing,
            DNSQuestion,
            IPVersion,
            RecordUpdateListener,
            Zeroconf,
        )
    except ImportError as exc:
        raise DeviceAddressResolutionError("mDNS 解析组件 zeroconf 未安装") from exc

    fqdn = f"{hostname.rstrip('.')}."
    addresses: set[str] = set()
    addresses_lock = threading.Lock()
    received = threading.Event()

    class AddressListener(RecordUpdateListener):
        def async_update_records(self, _zc, _now, updates) -> None:
            for update in updates:
                record = update.new
                if (
                    not isinstance(record, DNSAddress)
                    or record.name.rstrip(".").casefold() != hostname.rstrip(".").casefold()
                    or record.ttl <= 0
                ):
                    continue
                family = socket.AF_INET if record.type == 1 else (
                    socket.AF_INET6 if record.type == 28 else None
                )
                if family is None:
                    continue
                try:
                    address = _canonical_ip(socket.inet_ntop(family, record.address))
                except (OSError, ValueError):
                    continue
                with addresses_lock:
                    addresses.add(address)
                received.set()

    listener = AddressListener()
    try:
        zeroconf = Zeroconf(ip_version=IPVersion.All)
    except (OSError, RuntimeError) as exc:
        raise DeviceAddressResolutionError(f"无法启动 mDNS 查询：{exc}") from exc
    questions = (
        DNSQuestion(fqdn, 1, 1),   # A / IN
        DNSQuestion(fqdn, 28, 1),  # AAAA / IN
    )
    try:
        zeroconf.add_listener(listener, list(questions))
        outgoing = DNSOutgoing(0)
        for question in questions:
            outgoing.add_question(question)
        zeroconf.send(outgoing)
        received.wait(max(0.0, timeout_seconds))
        if received.is_set():
            time.sleep(0.05)
        with addresses_lock:
            return _ordered_addresses(addresses)
    finally:
        try:
            zeroconf.remove_listener(listener)
        finally:
            zeroconf.close()


def _store_resolution(
    hostname: str,
    addresses: tuple[str, ...],
    ttl_seconds: float,
    now: float,
) -> None:
    with _resolution_cache_lock:
        _resolution_cache[hostname] = (now + max(0.0, ttl_seconds), addresses)
