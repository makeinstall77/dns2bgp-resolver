from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.config import BirdSettings, RefreshSettings
from dns2bgp_resolver.domain import DomainName, IpAddress, ResolvedAddress, StaticPrefix
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import StaticFileBirdExporter
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


class FakeDns:
    async def resolve_a(self, name: DomainName) -> list[ResolvedAddress]:
        return [ResolvedAddress(ip=IpAddress("8.8.8.8"), ttl_seconds=60)]


@pytest.fixture
async def repo(tmp_path: Path):
    repository = SqlAlchemyDomainRepository(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    await repository.initialize()
    yield repository
    await repository.close()


def _pipeline(repo, bird_path: Path) -> ResolvePipeline:
    return ResolvePipeline(
        repository=repo,
        resolver=FakeDns(),
        exporter=StaticFileBirdExporter(
            BirdSettings(include_path=str(bird_path), birdc_enable=False)
        ),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        refresh=RefreshSettings(),
        export_path=str(bird_path),
        export_min_interval=0,
    )


@pytest.mark.asyncio
async def test_refuses_empty_wipe_without_allow_empty(repo, tmp_path: Path):
    bird_path = tmp_path / "routes.bird"
    pipe = _pipeline(repo, bird_path)
    await repo.add_static_prefix(StaticPrefix(cidr="1.2.3.0/24"))
    await pipe.export_routes()
    assert "1.2.3.0/24" in bird_path.read_text()

    await repo.remove_static_prefix("1.2.3.0/24")
    summary = await pipe.export_routes(allow_empty=False)
    assert summary.skipped is True
    assert "1.2.3.0/24" in bird_path.read_text()


@pytest.mark.asyncio
async def test_allow_empty_wipes_on_explicit_intent(repo, tmp_path: Path):
    bird_path = tmp_path / "routes.bird"
    pipe = _pipeline(repo, bird_path)
    await repo.add_static_prefix(StaticPrefix(cidr="1.2.3.0/24"))
    await pipe.export_routes()

    await repo.remove_static_prefix("1.2.3.0/24")
    summary = await pipe.export_after_mutation(allow_empty=True)
    assert summary.skipped is False
    assert summary.prefix_count == 0
    text = bird_path.read_text()
    assert "route " not in text or "route 1.2.3.0/24" not in text


@pytest.mark.asyncio
async def test_startup_empty_db_keeps_existing_bird_file(repo, tmp_path: Path):
    bird_path = tmp_path / "routes.bird"
    bird_path.write_text(
        "# generated\nprotocol static dns2bgp {\n  ipv4;\n"
        "  route 9.9.9.0/24 reject;\n}\n",
        encoding="utf-8",
    )
    pipe = _pipeline(repo, bird_path)
    summary = await pipe.export_routes()
    assert summary.skipped is True
    assert "9.9.9.0/24" in bird_path.read_text()


@pytest.mark.asyncio
async def test_first_install_empty_file_allowed(repo, tmp_path: Path):
    bird_path = tmp_path / "routes.bird"
    pipe = _pipeline(repo, bird_path)
    summary = await pipe.export_routes()
    assert summary.skipped is False
    assert bird_path.is_file()
