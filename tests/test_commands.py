from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    CommandBus,
    ExportRoutesCommand,
    ExportRoutesHandler,
    ListDomainsCommand,
    ListDomainsHandler,
    RemoveDomainCommand,
    ResolveNowCommand,
    ResolveNowHandler,
)
from dns2bgp_resolver.application.ports.clock import Clock
from dns2bgp_resolver.application.ports.dns_resolver import DnsResolver
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.config import BirdSettings, RefreshSettings
from dns2bgp_resolver.container import AddDomainAndResolveHandler, RemoveDomainAndExportHandler
from dns2bgp_resolver.domain import DomainName, IpAddress, ResolvedAddress
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import StaticFileBirdExporter
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository


class FixedClock(Clock):
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


class FakeDns(DnsResolver):
    def __init__(self, mapping: dict[str, list[tuple[str, int]]]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def resolve_a(self, name: DomainName) -> list[ResolvedAddress]:
        self.calls.append(str(name))
        return [
            ResolvedAddress(ip=IpAddress(ip), ttl_seconds=ttl)
            for ip, ttl in self.mapping.get(str(name), [])
        ]


@pytest.fixture
async def repo(tmp_path: Path):
    db = tmp_path / "test.db"
    repository = SqlAlchemyDomainRepository(f"sqlite+aiosqlite:///{db}")
    await repository.initialize()
    yield repository
    await repository.close()


@pytest.fixture
def bird_path(tmp_path: Path) -> Path:
    return tmp_path / "routes.bird"


@pytest.fixture
async def pipeline(repo, bird_path: Path):
    dns = FakeDns({"example.com": [("1.2.3.4", 120), ("1.2.3.5", 120)]})
    exporter = StaticFileBirdExporter(
        BirdSettings(include_path=str(bird_path), nexthop="wg0", birdc_enable=False)
    )
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    pipe = ResolvePipeline(
        repository=repo,
        resolver=dns,
        exporter=exporter,
        clock=clock,
        refresh=RefreshSettings(max_interval=86400, min_interval=60),
        export_path=str(bird_path),
    )
    return pipe, dns, exporter


@pytest.fixture
async def bus(repo, pipeline):
    pipe, _, _ = pipeline
    command_bus = CommandBus()
    command_bus.register(AddDomainCommand, AddDomainAndResolveHandler(repo, pipe))
    command_bus.register(RemoveDomainCommand, RemoveDomainAndExportHandler(repo, pipe))
    command_bus.register(ListDomainsCommand, ListDomainsHandler(repo))
    command_bus.register(ResolveNowCommand, ResolveNowHandler(pipe))
    command_bus.register(ExportRoutesCommand, ExportRoutesHandler(pipe))
    return command_bus


@pytest.mark.asyncio
async def test_add_resolve_export(bus, bird_path: Path):
    result = await bus.execute(AddDomainCommand(name="example.com"))
    assert result.ok
    assert result.data is not None
    assert set(result.data.addresses) == {"1.2.3.4", "1.2.3.5"}
    assert bird_path.is_file()
    text = bird_path.read_text(encoding="utf-8")
    assert "route 1.2.3.4/32 reject;" in text
    assert "route 1.2.3.5/32 reject;" in text


@pytest.mark.asyncio
async def test_list_and_remove(bus, bird_path: Path):
    await bus.execute(AddDomainCommand(name="example.com"))
    listed = await bus.execute(ListDomainsCommand())
    assert listed.ok
    assert len(listed.data or []) == 1

    removed = await bus.execute(RemoveDomainCommand(name="example.com"))
    assert removed.ok
    text = bird_path.read_text(encoding="utf-8")
    assert "1.2.3.4" not in text

    listed2 = await bus.execute(ListDomainsCommand())
    assert listed2.data == []


@pytest.mark.asyncio
async def test_resolve_change_triggers_export(pipeline, repo, bird_path: Path):
    pipe, dns, _ = pipeline
    from dns2bgp_resolver.domain import Domain

    await repo.add(Domain.create("example.com"))
    first = await pipe.resolve_one(DomainName("example.com"))
    assert first.changed
    assert first.exported

    second = await pipe.resolve_one(DomainName("example.com"))
    assert not second.changed
    assert not second.exported

    dns.mapping["example.com"] = [("9.9.9.9", 60)]
    third = await pipe.resolve_one(DomainName("example.com"))
    assert third.changed
    assert "9.9.9.9/32" in bird_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_next_resolve_uses_min_ttl_capped(pipeline, repo):
    pipe, dns, _ = pipeline
    from dns2bgp_resolver.domain import Domain

    dns.mapping["ttl.test"] = [("8.8.8.8", 30)]  # below min_interval → 60
    await repo.add(Domain.create("ttl.test"))
    await pipe.resolve_one(DomainName("ttl.test"))
    domain = await repo.get(DomainName("ttl.test"))
    assert domain is not None
    assert domain.next_resolve_at is not None
    delta = (domain.next_resolve_at - domain.last_resolved_at).total_seconds()
    assert delta == 60

    dns.mapping["ttl.test"] = [("8.8.8.8", 100_000)]  # above max → 86400
    await pipe.resolve_one(DomainName("ttl.test"))
    domain = await repo.get(DomainName("ttl.test"))
    delta = (domain.next_resolve_at - domain.last_resolved_at).total_seconds()
    assert delta == 86400


@pytest.mark.asyncio
async def test_invalid_domain(bus):
    result = await bus.execute(AddDomainCommand(name="not a domain"))
    assert not result.ok


@pytest.mark.asyncio
async def test_bird_exporter_ip_nexthop(tmp_path: Path):
    path = tmp_path / "r.bird"
    exporter = StaticFileBirdExporter(
        BirdSettings(include_path=str(path), nexthop="10.0.0.1", birdc_enable=False)
    )
    await exporter.export(["1.1.1.1/32"])
    assert "route 1.1.1.1/32 reject;" in path.read_text(encoding="utf-8")
    assert (path.stat().st_mode & 0o777) == 0o664
    assert (path.parent.stat().st_mode & 0o777) == 0o755


@pytest.mark.asyncio
async def test_domain_name_normalization():
    assert str(DomainName("Example.COM.")) == "example.com"
