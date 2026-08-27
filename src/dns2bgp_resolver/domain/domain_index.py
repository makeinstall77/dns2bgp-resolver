from __future__ import annotations

from threading import Lock
from typing import Iterable


def normalize_qname(name: str) -> str:
    return name.strip().strip(".").lower()


class DomainIndex:
    """In-memory exact + parent-suffix matching (no IO on lookup)."""

    __slots__ = ("_exact", "_suffix", "_lock", "_size")

    def __init__(self) -> None:
        self._exact: frozenset[str] = frozenset()
        self._suffix: frozenset[str] = frozenset()
        self._lock = Lock()
        self._size = 0

    @property
    def size(self) -> int:
        with self._lock:
            return self._size

    def rebuild(
        self,
        names: set[str] | frozenset[str] | list[str] | Iterable[tuple[str, str]] | None = None,
        *,
        rules: Iterable[tuple[str, str]] | None = None,
    ) -> int:
        """Rebuild from bare names (all suffix) or (name, match_mode) rules."""
        exact: set[str] = set()
        suffix: set[str] = set()
        source = rules if rules is not None else names or ()
        for item in source:
            if isinstance(item, tuple):
                raw, mode = item
                n = normalize_qname(raw)
                if not n or "." not in n:
                    continue
                if mode == "exact":
                    exact.add(n)
                else:
                    suffix.add(n)
            else:
                n = normalize_qname(item)
                if n and "." in n:
                    suffix.add(n)
        with self._lock:
            self._exact = frozenset(exact)
            self._suffix = frozenset(suffix)
            self._size = len(exact | suffix)
            return self._size

    def matches(self, qname: str) -> str | None:
        """Return matched rule (qname or parent suffix) if listed, else None."""
        q = normalize_qname(qname)
        if not q or "." not in q:
            return None
        with self._lock:
            exact = self._exact
            suffix = self._suffix
        if q in exact or q in suffix:
            return q
        parts = q.split(".")
        for i in range(1, len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in suffix:
                return candidate
        return None

    def contains_exact(self, name: str) -> bool:
        n = normalize_qname(name)
        with self._lock:
            return n in self._exact or n in self._suffix

    def names_snapshot(self) -> frozenset[str]:
        """All indexed names (exact ∪ suffix) for policy exporters."""
        with self._lock:
            return self._exact | self._suffix
