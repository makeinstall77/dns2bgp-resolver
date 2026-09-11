from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Iterable

from dns2bgp_resolver.domain import RoutePolicy


def normalize_qname(name: str) -> str:
    return name.strip().strip(".").lower()


@dataclass(frozen=True, slots=True)
class IndexMatch:
    name: str
    route_policy: RoutePolicy = "announce"


class DomainIndex:
    """In-memory exact + parent-suffix matching (no IO on lookup)."""

    __slots__ = ("_exact", "_suffix", "_lock", "_size")

    def __init__(self) -> None:
        self._exact: dict[str, RoutePolicy] = {}
        self._suffix: dict[str, RoutePolicy] = {}
        self._lock = Lock()
        self._size = 0

    @property
    def size(self) -> int:
        with self._lock:
            return self._size

    def rebuild(
        self,
        names: set[str] | frozenset[str] | list[str] | Iterable[tuple] | None = None,
        *,
        rules: Iterable[tuple] | None = None,
    ) -> int:
        """Rebuild from bare names (all suffix/announce) or (name, mode[, policy]) rules."""
        exact: dict[str, RoutePolicy] = {}
        suffix: dict[str, RoutePolicy] = {}
        source = rules if rules is not None else names or ()
        for item in source:
            if isinstance(item, tuple):
                raw = item[0]
                mode = item[1] if len(item) > 1 else "suffix"
                policy: RoutePolicy = (
                    "direct" if len(item) > 2 and item[2] == "direct" else "announce"
                )
                n = normalize_qname(str(raw))
                if not n or "." not in n:
                    continue
                target = exact if mode == "exact" else suffix
                if policy == "direct" or n not in target:
                    target[n] = policy
            else:
                n = normalize_qname(item)
                if n and "." in n:
                    suffix[n] = "announce"
        with self._lock:
            self._exact = exact
            self._suffix = suffix
            self._size = len(set(exact) | set(suffix))
            return self._size

    def matches(self, qname: str) -> IndexMatch | None:
        """Return matched rule; if any match is direct, direct wins."""
        q = normalize_qname(qname)
        if not q or "." not in q:
            return None
        with self._lock:
            exact = self._exact
            suffix = self._suffix
        candidates: list[IndexMatch] = []
        if q in exact:
            candidates.append(IndexMatch(q, exact[q]))
        if q in suffix:
            candidates.append(IndexMatch(q, suffix[q]))
        parts = q.split(".")
        for i in range(1, len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in suffix:
                candidates.append(IndexMatch(candidate, suffix[candidate]))
        if not candidates:
            return None
        for match in candidates:
            if match.route_policy == "direct":
                return match
        return candidates[0]

    def contains_exact(self, name: str) -> bool:
        n = normalize_qname(name)
        with self._lock:
            return n in self._exact or n in self._suffix

    def names_snapshot(self) -> frozenset[str]:
        """All indexed names (exact ∪ suffix) for policy exporters."""
        with self._lock:
            return frozenset(self._exact) | frozenset(self._suffix)
