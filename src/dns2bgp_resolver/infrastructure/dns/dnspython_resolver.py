from __future__ import annotations

import asyncio
import logging

import dns.asyncresolver
import dns.exception
import dns.rdatatype

from dns2bgp_resolver.application.ports.dns_resolver import DnsResolver
from dns2bgp_resolver.config import DnsSettings
from dns2bgp_resolver.domain import DomainName, IpAddress, ResolvedAddress

logger = logging.getLogger(__name__)


class DnspythonResolver(DnsResolver):
    def __init__(self, settings: DnsSettings) -> None:
        self._settings = settings
        self._resolver = dns.asyncresolver.Resolver()
        if settings.nameservers:
            self._resolver.nameservers = list(settings.nameservers)
        self._resolver.lifetime = settings.timeout

    async def resolve_a(self, name: DomainName) -> list[ResolvedAddress]:
        try:
            answer = await self._resolver.resolve(str(name), dns.rdatatype.A)
        except dns.exception.DNSException as exc:
            logger.info("DNS lookup %s: %s", name, exc)
            return []

        ttl = int(answer.rrset.ttl) if answer.rrset is not None else 300
        results: list[ResolvedAddress] = []
        for rdata in answer:
            ip_str = rdata.to_text()
            try:
                results.append(ResolvedAddress(ip=IpAddress(ip_str), ttl_seconds=ttl))
            except ValueError:
                logger.warning("skipping non-IPv4 answer for %s: %s", name, ip_str)
        return results


class SyncToAsyncDnsResolver(DnsResolver):
    """Optional wrapper if sync resolver is needed in tests."""

    def __init__(self, resolve_fn) -> None:  # type: ignore[no-untyped-def]
        self._resolve_fn = resolve_fn

    async def resolve_a(self, name: DomainName) -> list[ResolvedAddress]:
        return await asyncio.to_thread(self._resolve_fn, name)
