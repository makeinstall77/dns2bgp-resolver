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
    source: str = "manual"
    addresses: list[str] = field(default_factory=list)
    next_resolve_at: str | None = None
    last_resolved_at: str | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class AutoDomainSearchView:
    items: list[DomainView]
    total: int
    page: int
    pages: int
    page_size: int


@dataclass(frozen=True, slots=True)
class AutoSyncView:
    added: int
    removed: int
    skipped_manual: int


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
        source=domain.source,
        addresses=[str(a.ip) for a in domain.addresses],
        next_resolve_at=domain.next_resolve_at.isoformat() if domain.next_resolve_at else None,
        last_resolved_at=domain.last_resolved_at.isoformat() if domain.last_resolved_at else None,
        last_error=domain.last_error,
    )
