from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        ...


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
