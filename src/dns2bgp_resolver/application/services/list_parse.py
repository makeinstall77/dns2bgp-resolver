from __future__ import annotations

import logging
from dataclasses import dataclass

from dns2bgp_resolver.domain import (
    StaticPrefix,
    format_domain_label,
    is_announcable_prefix,
    parse_domain_input,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImportParseResult:
    domains: set[str]
    prefixes: set[str]
    skipped: int


def _try_prefix(raw: str) -> str | None:
    cidr_part = raw.split(None, 1)[0]
    try:
        prefix = StaticPrefix(cidr=cidr_part)
    except ValueError:
        return None
    if not is_announcable_prefix(prefix.cidr):
        return None
    return prefix.cidr


def _try_domain(raw: str) -> str | None:
    if any(ch.isspace() for ch in raw):
        return None
    try:
        name, mode = parse_domain_input(raw)
    except ValueError:
        return None
    label = format_domain_label(str(name), mode)
    if "." not in str(name):
        return None
    return label


def parse_import_lines(text: str) -> ImportParseResult:
    """Parse mixed domain/CIDR list. Prefixes (IP/CIDR) take priority over domains."""
    domains: set[str] = set()
    prefixes: set[str] = set()
    skipped = 0
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        cidr = _try_prefix(raw)
        if cidr is not None:
            prefixes.add(cidr)
            continue
        label = _try_domain(raw)
        if label is not None:
            domains.add(label)
            continue
        skipped += 1
        logger.debug("skipped invalid import line: %r", raw)
    return ImportParseResult(domains=domains, prefixes=prefixes, skipped=skipped)
