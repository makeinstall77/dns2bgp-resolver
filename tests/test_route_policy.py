from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dns2bgp_resolver.application.services.domain_index_service import DomainIndexService
from dns2bgp_resolver.application.services.passive_dns import PassiveDnsCollector
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.config import BirdSettings, RefreshSettings
from dns2bgp_resolver.domain import (
    Domain,
    DomainName,
    IpAddress,
    ResolvedAddress,
    StaticPrefix,
    summarize_prefixes,
)
from dns2bgp_resolver.domain.domain_index import DomainIndex
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import StaticFileBirdExporter
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


class FakeDns:
    def __init__(self, mapping: dict[str, list[str]] | None = None) -> None:
        self._mapping = mapping or {}

    async def resolve_a(self, name: DomainName) -> list[ResolvedAddress]:
        ips = self._mapping.get(str(name), ["8.8.8.8"])
        return [ResolvedAddress(ip=IpAddress(ip), ttl_seconds=60) for ip in ips]


@pytest.fixture
async def repo(tmp_path: Path):
    repository = SqlAlchemyDomainRepository(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    await repository.initialize()
    yield repository
    await repository.close()


def _pipeline(repo, tmp_path: Path, resolver=None) -> ResolvePipeline:
    bird_path = tmp_path / "routes.bird"
    return ResolvePipeline(
        repository=repo,
        resolver=resolver or FakeDns(),
        exporter=StaticFileBirdExporter(
            BirdSettings(include_path=str(bird_path), birdc_enable=False)
        ),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        refresh=RefreshSettings(),
        export_path=str(bird_path),
        export_min_interval=0,
    )


def test_index_direct_wins_over_vpn_suffix():
    idx = DomainIndex()
    idx.rebuild(
        rules=[
            ("example.com", "suffix", "vpn"),
            ("cdn.example.com", "exact", "direct"),
        ]
    )
    hit = idx.matches("cdn.example.com")
    assert hit is not None
    assert hit.route_policy == "direct"
    assert hit.name == "cdn.example.com"
    parent = idx.matches("www.example.com")
    assert parent is not None
    assert parent.route_policy == "vpn"
    assert parent.name == "example.com"


def test_summarize_excludes_direct_from_aggregate():
    vpn = ["1.2.3.1/32", "1.2.3.2/32", "1.2.3.4/32"]
    direct = ["1.2.3.4/32"]
    out = summarize_prefixes(vpn, exclude=direct)
    assert "1.2.3.0/24" not in out
    assert "1.2.3.4/32" not in out
    assert "1.2.3.1/32" in out
    assert "1.2.3.2/32" in out


def test_summarize_punches_static_direct_hole():
    vpn = ["1.2.3.0/24"]
    direct = ["1.2.3.10/32"]
    out = summarize_prefixes(vpn, exclude=direct)
    assert "1.2.3.0/24" not in out
    assert "1.2.3.10/32" not in out
    joined = " ".join(out)
    assert "1.2.3." in joined


@pytest.mark.asyncio
async def test_export_subtracts_direct_static(repo, tmp_path: Path):
    pipe = _pipeline(repo, tmp_path)
    await repo.add_static_prefix(StaticPrefix(cidr="8.8.8.0/24", name="vpn-pool"))
    await repo.add_static_prefix(
        StaticPrefix(cidr="8.8.8.8/32", name="exception", route_policy="direct")
    )
    summary = await pipe.export_routes()
    text = Path(pipe._export_path).read_text()
    assert "8.8.8.0/24" not in text
    assert "8.8.8.8/32" not in text
    assert summary.prefix_count >= 1


@pytest.mark.asyncio
async def test_cdn_collision_direct_domain_wins(repo, tmp_path: Path):
    resolver = FakeDns(
        {
            "vpn.example": ["1.1.1.1"],
            "direct.example": ["1.1.1.1"],
        }
    )
    pipe = _pipeline(repo, tmp_path, resolver=resolver)
    vpn = await repo.add(Domain.create("vpn.example", route_policy="vpn"))
    direct = await repo.add(Domain.create("direct.example", route_policy="direct"))
    await repo.replace_addresses(
        vpn.id,
        [ResolvedAddress(ip=IpAddress("1.1.1.1"), ttl_seconds=60)],
        resolved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        next_resolve_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    await repo.replace_addresses(
        direct.id,
        [ResolvedAddress(ip=IpAddress("1.1.1.1"), ttl_seconds=60)],
        resolved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        next_resolve_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    await pipe.export_routes()
    text = Path(pipe._export_path).read_text()
    assert "1.1.1.1/32" not in text


@pytest.mark.asyncio
async def test_passive_skips_direct_match(repo, tmp_path: Path):
    pipe = _pipeline(repo, tmp_path)
    idx = DomainIndex()
    idx.rebuild(rules=[("blocked.com", "suffix", "direct")])
    collector = PassiveDnsCollector(idx, pipe)
    await collector.on_response("cdn.blocked.com", ["1.2.3.4"])
    await pipe.flush_pending_export()
    assert collector.stats[1] == 0
    ips = await repo.list_passive_ips()
    assert ips == []


@pytest.mark.asyncio
async def test_index_rebuild_carries_route_policy(repo):
    await repo.add(Domain.create("vpn.example", route_policy="vpn"))
    await repo.add(Domain.create("ex.example", route_policy="direct"))
    svc = DomainIndexService(repo, DomainIndex())
    await svc.rebuild()
    assert svc.index.matches("vpn.example").route_policy == "vpn"
    assert svc.index.matches("ex.example").route_policy == "direct"


@pytest.mark.asyncio
async def test_route_policy_migration_default(repo):
    domain = await repo.add(Domain.create("plain.example"))
    assert domain.route_policy == "vpn"
    prefix = await repo.add_static_prefix(StaticPrefix(cidr="9.9.9.9/32"))
    assert prefix.route_policy == "vpn"
