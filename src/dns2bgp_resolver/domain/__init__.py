from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network, collapse_addresses, ip_address, ip_network
from typing import Literal, Self

DomainSource = Literal["manual", "auto"]
DomainListType = Literal["url", "file"]
MatchMode = Literal["exact", "suffix"]
PrefixSource = Literal["static", "passive", "manual"]
Ipv6SuppressMode = Literal["default", "on", "off"]


def resolve_ipv6_suppress(mode: Ipv6SuppressMode, *, global_default: bool) -> bool:
    """Effective suppress (block AAAA): default inherits global; on/off override."""
    if mode == "on":
        return True
    if mode == "off":
        return False
    return global_default


def next_ipv6_suppress_mode(mode: Ipv6SuppressMode) -> Ipv6SuppressMode:
    """Cycle: default → AAAA вкл (off) → AAAA выкл (on) → default."""
    order: tuple[Ipv6SuppressMode, ...] = ("default", "off", "on")
    return order[(order.index(mode) + 1) % len(order)]


def suffix_match_covers(listed: str, qname: str) -> bool:
    """dnsdist SuffixMatchNode: listed matches qname or any subdomain of listed."""
    return qname == listed or qname.endswith("." + listed)


def filter_suppress_names_manual_priority(
    *,
    suppress_names: Iterable[tuple[str, str]],
    manual_allow_names: Iterable[str],
) -> list[str]:
    """
    Build final dnsdist suppress list.

    Manual AAAA-allow names win: drop any auto suppress entry that would
    SuffixMatch-cover them (or sit under their suffix tree).
    Manual suppress entries are always kept.
    """
    allows = {n.strip(".").lower() for n in manual_allow_names if n}
    result: list[str] = []
    for raw_name, source in suppress_names:
        name = raw_name.strip(".").lower()
        if not name:
            continue
        if source == "manual":
            result.append(name)
            continue
        drop = False
        for allow in allows:
            if suffix_match_covers(name, allow) or suffix_match_covers(allow, name):
                drop = True
                break
        if not drop:
            result.append(name)
    return result



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


def ip_to_prefix32(ip: str) -> str:
    return str(IPv4Network(f"{IPv4Address(ip)}/32"))


def _drop_covered(networks: list[IPv4Network]) -> list[IPv4Network]:
    """Keep broader prefixes; drop those fully contained in another."""
    kept: list[IPv4Network] = []
    for net in sorted(set(networks), key=lambda n: (n.prefixlen, int(n.network_address))):
        if any(net != other and net.subnet_of(other) for other in kept):
            continue
        kept.append(net)
    return kept


def summarize_prefixes(cidrs: Iterable[str]) -> list[str]:
    """Aggregate IPv4 prefixes for BGP export.

    - lone host → /32
    - 2+ hosts in the same /24 → that /24
    - adjacent equal-length prefixes collapse (/24+/24→/23, …)
    - prefixes covered by a broader one are dropped
    """
    networks = [IPv4Network(c, strict=False) for c in cidrs]
    networks = _drop_covered(networks)

    by_24: dict[IPv4Network, list[IPv4Network]] = defaultdict(list)
    rest: list[IPv4Network] = []
    for net in networks:
        if net.prefixlen == 32:
            parent = IPv4Network(f"{net.network_address}/24", strict=False)
            by_24[parent].append(net)
        else:
            rest.append(net)

    promoted: list[IPv4Network] = list(rest)
    for parent, hosts in by_24.items():
        if any(parent == other or parent.subnet_of(other) for other in rest):
            continue
        if len(hosts) >= 2:
            promoted.append(parent)
        else:
            promoted.extend(hosts)

    return [str(n) for n in collapse_addresses(_drop_covered(promoted))]


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
    suppress_ipv6: Ipv6SuppressMode = "default"
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
        suppress_ipv6: Ipv6SuppressMode = "default",
    ) -> Self:
        parsed, mode = parse_domain_input(name)
        return cls(
            name=parsed,
            source=source,
            match_mode=match_mode or mode,
            suppress_ipv6=suppress_ipv6,
        )


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
