from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from dns2bgp_resolver.config import Ipv6Settings

logger = logging.getLogger(__name__)


class UnboundCacheFlusher:
    """
    Best-effort unbound cache flush for newly suppressed domains.

    Replaces @DOMAIN@ in cache_flush_cmd. Failures are logged, never raised.
    """

    def __init__(self, settings: Ipv6Settings) -> None:
        self._settings = settings

    def _cmd_for(self, domain: str) -> list[str]:
        return [
            part.replace("@DOMAIN@", domain) for part in self._settings.cache_flush_cmd
        ]

    async def flush_zones(self, names: Iterable[str]) -> None:
        if not self._settings.cache_flush_enable:
            return
        cmd_template = list(self._settings.cache_flush_cmd)
        if not cmd_template:
            logger.warning("cache flush enabled but cache_flush_cmd is empty")
            return
        for raw in names:
            name = raw.strip(".").lower()
            if not name:
                continue
            cmd = self._cmd_for(name)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    logger.warning(
                        "cache flush failed for %s (rc=%s): %s %s",
                        name,
                        proc.returncode,
                        stdout.decode(errors="replace").strip(),
                        stderr.decode(errors="replace").strip(),
                    )
                else:
                    logger.info("flushed unbound zone %s", name)
            except FileNotFoundError:
                logger.warning("cache flush binary not found: %s", cmd[0])
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("cache flush error for %s: %s", name, exc)
