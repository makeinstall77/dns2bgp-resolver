from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network, ip_address
from typing import Literal, Self

DomainSource = Literal["manual", "auto"]
DomainListType = Literal["url", "file"]


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
    created_at: datetime | None = None
    next_resolve_at: datetime | None = None
    last_resolved_at: datetime | None = None
    last_error: str | None = None
    addresses: list[ResolvedAddress] = field(default_factory=list)

    @classmethod
    def create(cls, name: str, *, source: DomainSource = "manual") -> Self:
        return cls(name=DomainName(name), source=source)
