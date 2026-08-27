from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class CommandResult(Generic[T]):
    ok: bool
    data: T | None = None
    message: str = ""
    error: str | None = None

    @classmethod
    def success(cls, data: T | None = None, message: str = "") -> CommandResult[T]:
        return cls(ok=True, data=data, message=message)

    @classmethod
    def failure(cls, error: str, data: T | None = None) -> CommandResult[T]:
        return cls(ok=False, data=data, error=error, message=error)


@dataclass(frozen=True, slots=True)
class DomainView:
    name: str
    enabled: bool
    id: int | None = None
    source: str = "manual"
    match_mode: str = "exact"
    suppress_ipv6: bool = True
    addresses: list[str] = field(default_factory=list)
    next_resolve_at: str | None = None
    last_resolved_at: str | None = None
    last_error: str | None = None

    @property
    def label(self) -> str:
        return f"*.{self.name}" if self.match_mode == "suffix" else self.name


@dataclass(frozen=True, slots=True)
class DomainPageView:
    items: list[DomainView]
    total: int
    page: int
    pages: int
    page_size: int


AutoDomainSearchView = DomainPageView


@dataclass(frozen=True, slots=True)
class AutoSyncView:
    added: int
    removed: int
    skipped_manual: int
    list_id: int | None = None
    list_name: str | None = None
    needs_confirmation: bool = False
    pending_token: str | None = None
    would_add: int = 0
    would_remove: int = 0
    current_count: int = 0


@dataclass(frozen=True, slots=True)
class DomainListView:
    id: int
    name: str
    type: str
    url: str | None
    enabled: bool
    sync_interval: int | None
    last_sync_at: str | None
    created_at: str | None
    domain_count: int = 0
    has_file: bool = False


@dataclass(frozen=True, slots=True)
class SettingsView:
    default_sync_interval: int
    suppress_ipv6_manual_default: bool = True
    suppress_ipv6_auto_default: bool = True


@dataclass(frozen=True, slots=True)
class ResolveSummary:
    domain: str
    addresses: list[str]
    changed: bool
    exported: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ExportSummary:
    prefix_count: int
    path: str


def domain_to_view(domain: Any) -> DomainView:
    from dns2bgp_resolver.domain import Domain

    assert isinstance(domain, Domain)
    return DomainView(
        name=str(domain.name),
        enabled=domain.enabled,
        id=domain.id,
        source=domain.source,
        match_mode=getattr(domain, "match_mode", None) or "exact",
        suppress_ipv6=bool(getattr(domain, "suppress_ipv6", True)),
        addresses=[str(a.ip) for a in domain.addresses],
        next_resolve_at=domain.next_resolve_at.isoformat() if domain.next_resolve_at else None,
        last_resolved_at=domain.last_resolved_at.isoformat() if domain.last_resolved_at else None,
        last_error=domain.last_error,
    )
