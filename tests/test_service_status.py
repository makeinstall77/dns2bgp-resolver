from __future__ import annotations

from pathlib import Path

from dns2bgp_resolver.application.services.service_status import (
    HostResources,
    ServiceStatus,
    format_status_text,
)
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import (
    count_routes_in_file,
    _parse_bird_count,
)


def test_count_routes_in_file(tmp_path: Path) -> None:
    path = tmp_path / "routes"
    path.write_text(
        "protocol static dns2bgp {\n"
        "  ipv4;\n"
        "  route 1.2.3.0/24 reject;\n"
        "  route 5.6.7.0/24 reject;\n"
        "}\n",
        encoding="utf-8",
    )
    assert count_routes_in_file(path) == 2
    assert count_routes_in_file(tmp_path / "missing") is None


def test_parse_bird_count() -> None:
    assert _parse_bird_count("123 of 123 networks") == 123
    assert _parse_bird_count("Total: 42\n") == 42


def test_format_status_text() -> None:
    status = ServiceStatus(
        manual_domains=1,
        auto_domains=2,
        index_size=3,
        static_prefixes=4,
        static_enabled=3,
        passive_ips=5,
        domain_lists=1,
        lists_enabled=1,
        due_resolve=0,
        exclude_keywords=0,
        pool_manual_ips=1,
        pool_passive_ips=5,
        pool_unique_prefixes=7,
        bird_file_routes=7,
        bird_live_routes=7,
        dnstap_enabled=True,
        dnstap_seen=10,
        dnstap_matched=4,
        resources=HostResources(
            cpu_percent=12.0,
            mem_used_mb=100.0,
            mem_total_mb=1000.0,
            mem_percent=10.0,
            disk_used_gb=1.0,
            disk_total_gb=10.0,
            disk_percent=10.0,
            process_rss_mb=50.0,
        ),
        collected_at=0.0,
    )
    text = format_status_text(status, live_left_sec=65)
    assert "Manual доменов: 1" in text
    assert "Анонсы bird (live): 7" in text
    assert "автообновление ещё ~1м 5с" in text
    assert "CPU: 12%" in text
