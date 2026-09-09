from __future__ import annotations

from abc import ABC, abstractmethod


class RouteExporter(ABC):
    """Export current IP pool to bird (or another routing plane)."""

    @abstractmethod
    async def export(self, prefixes: list[str], *, allow_empty: bool = False) -> bool:
        """
        Persist routes so bird can read them even if this process dies.
        Reloading bird is best-effort and must not fail the export.

        If the on-disk file already has routes and ``prefixes`` is empty,
        skip the write unless ``allow_empty`` is True (explicit user wipe).
        Return False when the write was skipped.
        """
