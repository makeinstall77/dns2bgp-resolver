from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.config import BirdSettings, DnstapSettings
from dns2bgp_resolver.domain import ip_to_prefix24, is_announcable_ipv4, is_announcable_prefix
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import (
    count_routes_in_file,
    query_bird_route_count,
)


class _IndexSize(Protocol):
    @property
    def size(self) -> int: ...


class _PassiveStats(Protocol):
    @property
    def stats(self) -> tuple[int, int]: ...


@dataclass(frozen=True, slots=True)
class HostResources:
    cpu_percent: float | None
    mem_used_mb: float
    mem_total_mb: float
    mem_percent: float
    disk_used_gb: float
    disk_total_gb: float
    disk_percent: float
    process_rss_mb: float


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    manual_domains: int
    auto_domains: int
    index_size: int
    static_prefixes: int
    static_enabled: int
    passive_ips: int
    domain_lists: int
    lists_enabled: int
    due_resolve: int
    exclude_keywords: int
    pool_manual_ips: int
    pool_passive_ips: int
    pool_unique_prefixes: int
    bird_file_routes: int | None
    bird_live_routes: int | None
    dnstap_enabled: bool
    dnstap_seen: int
    dnstap_matched: int
    resources: HostResources
    collected_at: float


_prev_cpu: tuple[float, float] | None = None


def _read_cpu_times() -> tuple[float, float] | None:
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            parts = fh.readline().split()
        vals = [float(x) for x in parts[1:8]]
        idle = vals[3] + vals[4]
        total = sum(vals)
        return idle, total
    except OSError:
        return None


def _cpu_percent() -> float | None:
    global _prev_cpu
    cur = _read_cpu_times()
    if cur is None:
        return None
    if _prev_cpu is None:
        _prev_cpu = cur
        return None
    idle_d = cur[0] - _prev_cpu[0]
    total_d = cur[1] - _prev_cpu[1]
    _prev_cpu = cur
    if total_d <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - idle_d / total_d)))


def _meminfo() -> tuple[float, float, float]:
    mem_total = mem_available = 0.0
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    mem_total = float(line.split()[1]) / 1024.0
                elif line.startswith("MemAvailable:"):
                    mem_available = float(line.split()[1]) / 1024.0
    except OSError:
        return 0.0, 0.0, 0.0
    used = max(0.0, mem_total - mem_available)
    pct = (used / mem_total * 100.0) if mem_total else 0.0
    return used, mem_total, pct


def _process_rss_mb() -> float:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def collect_host_resources(disk_path: str | Path) -> HostResources:
    used_mb, total_mb, mem_pct = _meminfo()
    try:
        usage = shutil.disk_usage(disk_path)
        disk_used = usage.used / (1024**3)
        disk_total = usage.total / (1024**3)
        disk_pct = usage.used / usage.total * 100.0 if usage.total else 0.0
    except OSError:
        disk_used = disk_total = disk_pct = 0.0
    return HostResources(
        cpu_percent=_cpu_percent(),
        mem_used_mb=used_mb,
        mem_total_mb=total_mb,
        mem_percent=mem_pct,
        disk_used_gb=disk_used,
        disk_total_gb=disk_total,
        disk_percent=disk_pct,
        process_rss_mb=_process_rss_mb(),
    )


async def collect_service_status(
    *,
    repository: DomainRepository,
    domain_index: _IndexSize,
    passive_collector: _PassiveStats,
    bird: BirdSettings,
    dnstap: DnstapSettings,
    clock_now: datetime,
) -> ServiceStatus:
    _, manual_total = await repository.list_manual(offset=0, limit=1)
    _, auto_total = await repository.search_auto("", offset=0, limit=1)
    static = await repository.list_static_prefixes()
    passive_ips = await repository.list_passive_ips()
    lists = await repository.list_domain_lists()
    due = await repository.list_due(clock_now)
    keywords = await repository.list_exclude_keywords()
    manual_ips = await repository.all_active_ips()

    prefixes: set[str] = set()
    manual_ann = 0
    for ip in manual_ips:
        if is_announcable_ipv4(ip):
            manual_ann += 1
            prefixes.add(ip_to_prefix24(ip))
    passive_ann = 0
    for ip in passive_ips:
        if is_announcable_ipv4(ip):
            passive_ann += 1
            prefixes.add(ip_to_prefix24(ip))
    static_enabled = 0
    for item in static:
        if item.enabled and is_announcable_prefix(item.cidr):
            static_enabled += 1
            prefixes.add(item.cidr)

    bird_file = await asyncio.to_thread(count_routes_in_file, bird.include_path)
    bird_live: int | None = None
    if bird.birdc_enable:
        bird_live = await query_bird_route_count(bird)

    disk_path = Path(bird.include_path).parent
    if not disk_path.exists():
        disk_path = Path.cwd()
    resources = await asyncio.to_thread(collect_host_resources, disk_path)
    dnstap_seen, dnstap_matched = passive_collector.stats

    return ServiceStatus(
        manual_domains=manual_total,
        auto_domains=auto_total,
        index_size=domain_index.size,
        static_prefixes=len(static),
        static_enabled=static_enabled,
        passive_ips=len(passive_ips),
        domain_lists=len(lists),
        lists_enabled=sum(1 for x in lists if x.enabled),
        due_resolve=len(due),
        exclude_keywords=len(keywords),
        pool_manual_ips=manual_ann,
        pool_passive_ips=passive_ann,
        pool_unique_prefixes=len(prefixes),
        bird_file_routes=bird_file,
        bird_live_routes=bird_live,
        dnstap_enabled=dnstap.enabled,
        dnstap_seen=dnstap_seen,
        dnstap_matched=dnstap_matched,
        resources=resources,
        collected_at=time.time(),
    )


def format_status_text(status: ServiceStatus, *, live_left_sec: int | None = None) -> str:
    r = status.resources
    cpu = f"{r.cpu_percent:.0f}%" if r.cpu_percent is not None else "…"
    bird_file = "—" if status.bird_file_routes is None else str(status.bird_file_routes)
    bird_live = "—" if status.bird_live_routes is None else str(status.bird_live_routes)

    lines = [
        "📊 Статус dns2bgp-resolver",
        "",
        "Данные:",
        f"• Manual доменов: {status.manual_domains}",
        f"• Auto доменов: {status.auto_domains} (индекс: {status.index_size})",
        f"• Static prefixes: {status.static_enabled}/{status.static_prefixes}",
        f"• Passive IP (dnstap): {status.passive_ips}",
        f"• Списки: {status.lists_enabled}/{status.domain_lists} вкл.",
        f"• Exclude keywords: {status.exclude_keywords}",
        f"• Ожидают resolve: {status.due_resolve}",
        "",
        "BGP pool:",
        f"• Manual IP → /24: {status.pool_manual_ips}",
        f"• Passive IP → /24: {status.pool_passive_ips}",
        f"• Уникальных префиксов: {status.pool_unique_prefixes}",
        f"• Файл bird: {bird_file}",
        f"• Анонсы bird (live): {bird_live}",
        "",
        "dnstap:",
        f"• {'вкл' if status.dnstap_enabled else 'выкл'}"
        f" — seen {status.dnstap_seen}, matched {status.dnstap_matched}",
        "",
        "Хост:",
        f"• CPU: {cpu}",
        f"• RAM: {r.mem_used_mb:.0f}/{r.mem_total_mb:.0f} МБ ({r.mem_percent:.0f}%)",
        f"• Диск: {r.disk_used_gb:.1f}/{r.disk_total_gb:.1f} ГБ ({r.disk_percent:.0f}%)",
        f"• Процесс RSS: {r.process_rss_mb:.0f} МБ",
        f"• PID: {os.getpid()}",
    ]
    if live_left_sec is not None and live_left_sec > 0:
        lines.append("")
        lines.append(f"⏱ автообновление ещё ~{live_left_sec // 60}м {live_left_sec % 60}с")
    elif live_left_sec == 0:
        lines.append("")
        lines.append("⏱ автообновление завершено")
    return "\n".join(lines)
