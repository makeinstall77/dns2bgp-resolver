from __future__ import annotations

from abc import ABC, abstractmethod


class RouteExporter(ABC):
    """Export current IP pool to bird (or another routing plane)."""

    @abstractmethod
    async def export(self, prefixes: list[str]) -> None:
        """
        Persist routes so bird can read them even if this process dies.
        Reloading bird is best-effort and must not fail the export.
        """
