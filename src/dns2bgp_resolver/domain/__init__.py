from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from typing import Literal, Self

DomainSource = Literal["manual", "auto"]
DomainListType = Literal["url", "file"]
MatchMode = Literal["exact", "suffix"]
PrefixSource = Literal["static", "passive", "manual"]


_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


@dataclass(frozen=True, slots=True)
class DomainName:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip(".").lower()
        if not normalized or not _DOMAIN_RE.match(normalized):
            raise ValueError(f"invalid domain name: {self.value!r}")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


def parse_domain_input(raw: str) -> tuple[DomainName, MatchMode]:
    """Parse FQDN (exact) or *.example.com / .example.com (suffix mask)."""
    text = raw.strip().lower()
    mode: MatchMode = "exact"
    if text.startswith("*."):
        text = text[2:]
        mode = "suffix"
    elif text.startswith("."):
        text = text[1:]
        mode = "suffix"
    name = DomainName(text.strip("."))
    return name, mode


def format_domain_label(name: str, match_mode: MatchMode = "exact") -> str:
    return f"*.{name}" if match_mode == "suffix" else name


@dataclass(frozen=True, slots=True)
class IpAddress:
    """Address value object. Currently IPv4-only; IPv6 can be enabled later."""

    value: str
    family: int = 4

    def __post_init__(self) -> None:
        addr = ip_address(self.value)
        if addr.version != 4:
            raise ValueError(f"IPv6 is not enabled: {self.value}")
        object.__setattr__(self, "value", str(IPv4Address(addr)))
        object.__setattr__(self, "family", 4)

    def as_prefix(self, prefix_len: int | None = None) -> str:
        length = prefix_len if prefix_len is not None else 32
        if length == 24:
            return str(IPv4Network(f"{self.value}/24", strict=False))
        return f"{self.value}/{length}"

    def __str__(self) -> str:
        return self.value


def ip_to_prefix24(ip: str) -> str:
    return str(IPv4Network(f"{ip}/24", strict=False))


def is_announcable_ipv4(ip: str) -> bool:
    addr = IPv4Address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_multicast
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
    )


def is_announcable_prefix(cidr: str) -> bool:
    net = IPv4Network(cidr, strict=False)
    return not (
        net.is_private
        or net.is_loopback
        or net.is_multicast
        or net.is_link_local
        or net.is_reserved
        or net.is_unspecified
    )


@dataclass(frozen=True, slots=True)
class Prefix:
    """IPv4 prefix for bird export. Static CIDRs keep original length."""

    cidr: str
    source: PrefixSource = "static"
    name: str | None = None

    def __post_init__(self) -> None:
        net = ip_network(self.cidr, strict=False)
        if net.version != 4:
            raise ValueError(f"IPv6 is not enabled: {self.cidr}")
        object.__setattr__(self, "cidr", str(net))

    @classmethod
    def from_ip24(cls, ip: str, *, source: PrefixSource = "manual") -> Self:
        return cls(cidr=ip_to_prefix24(ip), source=source)

    @classmethod
    def parse(cls, raw: str, *, source: PrefixSource = "static", name: str | None = None) -> Self:
        text = raw.strip()
        if "/" not in text:
            text = f"{text}/32"
        return cls(cidr=text, source=source, name=name)

    def __str__(self) -> str:
        return self.cidr


@dataclass(frozen=True, slots=True)
class ResolvedAddress:
    ip: IpAddress
    ttl_seconds: int


@dataclass(frozen=True, slots=True)
class DomainList:
    id: int
    name: str
    type: DomainListType
    url: str | None = None
    file_content: str | None = None
    enabled: bool = True
    sync_interval: int | None = None
    last_sync_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class Domain:
    name: DomainName
    id: int | None = None
    source: DomainSource = "manual"
    list_id: int | None = None
    enabled: bool = True
    match_mode: MatchMode = "exact"
    created_at: datetime | None = None
    next_resolve_at: datetime | None = None
    last_resolved_at: datetime | None = None
    last_error: str | None = None
    addresses: list[ResolvedAddress] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        name: str,
        *,
        source: DomainSource = "manual",
        match_mode: MatchMode | None = None,
    ) -> Self:
        parsed, mode = parse_domain_input(name)
        return cls(name=parsed, source=source, match_mode=match_mode or mode)


@dataclass(frozen=True, slots=True)
class StaticPrefix:
    cidr: str
    id: int | None = None
    name: str | None = None
    enabled: bool = True
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        net = ip_network(self.cidr, strict=False)
        if net.version != 4:
            raise ValueError(f"IPv6 is not enabled: {self.cidr}")
        object.__setattr__(self, "cidr", str(net))


@dataclass(frozen=True, slots=True)
class PassiveHit:
    ip: str
    matched_name: str
    last_seen: datetime | None = None
    first_seen: datetime | None = None
