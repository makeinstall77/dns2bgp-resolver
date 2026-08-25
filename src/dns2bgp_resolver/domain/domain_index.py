from __future__ import annotations

from threading import Lock


def normalize_qname(name: str) -> str:
    return name.strip().strip(".").lower()


class DomainIndex:
    """In-memory exact set with parent-suffix matching (no IO on lookup)."""

    __slots__ = ("_names", "_lock", "_size")

    def __init__(self) -> None:
        self._names: frozenset[str] = frozenset()
        self._lock = Lock()
        self._size = 0

    @property
    def size(self) -> int:
        with self._lock:
            return self._size

    def rebuild(self, names: set[str] | frozenset[str] | list[str]) -> int:
        normalized = frozenset(
            n for n in (normalize_qname(x) for x in names) if n and "." in n
        )
        with self._lock:
            self._names = normalized
            self._size = len(normalized)
            return self._size

    def matches(self, qname: str) -> str | None:
        """Return matched rule (qname or parent) if listed, else None."""
        q = normalize_qname(qname)
        if not q or "." not in q:
            return None
        with self._lock:
            names = self._names
        if q in names:
            return q
        parts = q.split(".")
        # Skip single-label (TLD-only) suffixes
        for i in range(1, len(parts) - 1):
            suffix = ".".join(parts[i:])
            if suffix in names:
                return suffix
        return None

    def contains_exact(self, name: str) -> bool:
        n = normalize_qname(name)
        with self._lock:
            return n in self._names
