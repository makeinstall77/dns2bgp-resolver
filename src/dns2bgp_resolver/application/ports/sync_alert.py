from __future__ import annotations

from abc import ABC, abstractmethod

from dns2bgp_resolver.application.ports.repository import SyncPendingConfirmation


class SyncAlertNotifier(ABC):
    @abstractmethod
    async def notify_dangerous_sync(self, pending: SyncPendingConfirmation) -> None:
        """Alert operators that a list sync needs Confirm/Cancel."""
