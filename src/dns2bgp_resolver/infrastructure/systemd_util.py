from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

logger = logging.getLogger(__name__)


def sd_notify(state: str) -> bool:
    """Send a systemd notification (READY=1, etc.). No-op if NOTIFY_SOCKET unset."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    try:
        if addr.startswith("@"):
            sock_addr = "\0" + addr[1:]
        else:
            sock_addr = addr
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.connect(sock_addr)
            sock.sendall(state.encode("utf-8"))
        finally:
            sock.close()
        return True
    except OSError:
        logger.debug("sd_notify failed for %r", state, exc_info=True)
        return False


def resolve_dnsdist_reload_cmd(cmd: list[str], *, key_file: str = "") -> list[str]:
    """
    Expand @KEY@ / @KEY_FILE@ placeholders in reload command.

    If key_file is set and cmd contains @KEY@, substitute file contents.
    """
    if not cmd:
        return cmd
    key = ""
    path = key_file.strip()
    if path and any("@KEY@" in part for part in cmd):
        try:
            key = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("cannot read dnsdist key file: %s", path)
    out: list[str] = []
    for part in cmd:
        if path and "@KEY_FILE@" in part:
            part = part.replace("@KEY_FILE@", path)
        if key and "@KEY@" in part:
            part = part.replace("@KEY@", key)
        out.append(part)
    return out
