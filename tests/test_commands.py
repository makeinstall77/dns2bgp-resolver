from __future__ import annotations

import asyncio
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
        export_min_interval=0,
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
    assert "route 1.2.3.0/24 reject;" in text
    assert "route 1.2.3.4/32" not in text


@pytest.mark.asyncio
async def test_list_and_remove(bus, bird_path: Path):
    await bus.execute(AddDomainCommand(name="example.com"))
    listed = await bus.execute(ListDomainsCommand())
    assert listed.ok
    assert listed.data is not None
    assert len(listed.data.items) == 1

    removed = await bus.execute(RemoveDomainCommand(name="example.com"))
    assert removed.ok
    text = bird_path.read_text(encoding="utf-8")
    assert "1.2.3.4" not in text

    listed2 = await bus.execute(ListDomainsCommand())
    assert listed2.data is not None
    assert listed2.data.items == []


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
    assert "9.9.9.0/24" in bird_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_resolve_export_coalesced_by_interval(repo, bird_path: Path):
    dns = FakeDns(
        {
            "a.example": [("1.1.1.1", 120)],
            "b.example": [("2.2.2.2", 120)],
        }
    )
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
        export_min_interval=0.2,
    )
    from dns2bgp_resolver.domain import Domain

    await repo.add(Domain.create("a.example"))
    await repo.add(Domain.create("b.example"))

    first = await pipe.resolve_one(DomainName("a.example"))
    assert first.exported
    assert "1.1.1.0/24" in bird_path.read_text(encoding="utf-8")
    assert "2.2.2.0/24" not in bird_path.read_text(encoding="utf-8")

    second = await pipe.resolve_one(DomainName("b.example"))
    assert second.changed
    assert not second.exported
    assert "2.2.2.0/24" not in bird_path.read_text(encoding="utf-8")

    assert pipe._flush_task is not None
    await asyncio.wait_for(pipe._flush_task, timeout=1.0)
    assert "2.2.2.0/24" in bird_path.read_text(encoding="utf-8")


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
async def test_replace_addresses_upsert_keeps_stable_ips(repo):
    from dns2bgp_resolver.domain import Domain
    from dns2bgp_resolver.infrastructure.db.models import AddressRow
    from sqlalchemy import select

    await repo.add(Domain.create("stable.example"))
    domain = await repo.get(DomainName("stable.example"))
    assert domain is not None and domain.id is not None
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await repo.replace_addresses(
        domain.id,
        [ResolvedAddress(ip=IpAddress("1.1.1.1"), ttl_seconds=60)],
        resolved_at=now,
        next_resolve_at=now,
    )
    async with repo._session_factory() as session:
        rows = (
            await session.execute(
                select(AddressRow).where(AddressRow.domain_id == domain.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        first_id = rows[0].id
        first_seen = rows[0].first_seen

    later = datetime(2026, 1, 2, tzinfo=timezone.utc)
    await repo.replace_addresses(
        domain.id,
        [ResolvedAddress(ip=IpAddress("1.1.1.1"), ttl_seconds=120)],
        resolved_at=later,
        next_resolve_at=later,
    )
    async with repo._session_factory() as session:
        rows = (
            await session.execute(
                select(AddressRow).where(AddressRow.domain_id == domain.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == first_id
        assert rows[0].first_seen == first_seen
        assert rows[0].ttl_seconds == 120
        assert rows[0].last_seen.replace(tzinfo=timezone.utc) == later


@pytest.mark.asyncio
async def test_replace_addresses_upsert_swaps_changed_ip(repo):
    from dns2bgp_resolver.domain import Domain

    await repo.add(Domain.create("swap.example"))
    domain = await repo.get(DomainName("swap.example"))
    assert domain is not None and domain.id is not None
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await repo.replace_addresses(
        domain.id,
        [ResolvedAddress(ip=IpAddress("1.1.1.1"), ttl_seconds=60)],
        resolved_at=now,
        next_resolve_at=now,
    )
    await repo.replace_addresses(
        domain.id,
        [ResolvedAddress(ip=IpAddress("9.9.9.9"), ttl_seconds=60)],
        resolved_at=now,
        next_resolve_at=now,
    )
    updated = await repo.get(DomainName("swap.example"))
    assert updated is not None
    assert {str(a.ip) for a in updated.addresses} == {"9.9.9.9"}


@pytest.mark.asyncio
async def test_resolve_due_batch_size_and_concurrency(repo, bird_path: Path):
    from dns2bgp_resolver.domain import Domain

    class SlowDns(DnsResolver):
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def resolve_a(self, name: DomainName) -> list[ResolvedAddress]:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.05)
            self.active -= 1
            return [ResolvedAddress(ip=IpAddress("1.2.3.4"), ttl_seconds=60)]

    for i in range(5):
        await repo.add(Domain.create(f"d{i}.example"))

    dns = SlowDns()
    exporter = StaticFileBirdExporter(
        BirdSettings(include_path=str(bird_path), nexthop="wg0", birdc_enable=False)
    )
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    pipe = ResolvePipeline(
        repository=repo,
        resolver=dns,
        exporter=exporter,
        clock=clock,
        refresh=RefreshSettings(
            max_interval=86400,
            min_interval=60,
            resolve_concurrency=3,
            resolve_batch_size=2,
        ),
        export_path=str(bird_path),
        export_min_interval=0,
    )
    first = await pipe.resolve_due()
    assert len(first) == 2
    assert dns.max_active <= 3
    second = await pipe.resolve_due()
    assert len(second) == 2
    third = await pipe.resolve_due()
    assert len(third) == 1


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


def test_is_announcable_ipv4():
    from dns2bgp_resolver.domain import is_announcable_ipv4

    assert is_announcable_ipv4("1.2.3.4")
    assert is_announcable_ipv4("8.8.8.8")
    assert not is_announcable_ipv4("127.0.0.1")
    assert not is_announcable_ipv4("10.0.0.1")
    assert not is_announcable_ipv4("192.168.1.1")
    assert not is_announcable_ipv4("172.16.0.1")
    assert not is_announcable_ipv4("224.0.0.1")
    assert not is_announcable_ipv4("0.0.0.0")
    assert not is_announcable_ipv4("169.254.1.1")


@pytest.mark.asyncio
async def test_resolve_filters_non_announcable_ips(repo, bird_path: Path):
    dns = FakeDns(
        {
            "bad.example": [
                ("1.2.3.4", 120),
                ("127.0.0.1", 120),
                ("10.0.0.1", 120),
                ("224.0.0.1", 120),
            ]
        }
    )
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
        export_min_interval=0,
    )
    from dns2bgp_resolver.domain import Domain

    await repo.add(Domain.create("bad.example"))
    summary = await pipe.resolve_one(DomainName("bad.example"))
    assert summary.addresses == ["1.2.3.4"]
    assert summary.changed
    domain = await repo.get(DomainName("bad.example"))
    assert domain is not None
    assert [str(a.ip) for a in domain.addresses] == ["1.2.3.4"]
    text = bird_path.read_text(encoding="utf-8")
    assert "1.2.3.0/24" in text
    assert "127.0.0.0/24" not in text
    assert "10.0.0.0/24" not in text
    assert "224.0.0.0/24" not in text
