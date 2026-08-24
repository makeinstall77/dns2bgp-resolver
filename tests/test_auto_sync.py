from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    CommandBus,
    ListDomainsCommand,
    ListDomainsHandler,
    RemoveDomainCommand,
    RemoveDomainHandler,
    SearchAutoDomainsCommand,
    SearchAutoDomainsHandler,
    SyncAutoListCommand,
    SyncAutoListHandler,
)
from dns2bgp_resolver.application.commands.exclude_keywords import (
    AddExcludeKeywordCommand,
    AddExcludeKeywordHandler,
    ListExcludeKeywordsCommand,
    ListExcludeKeywordsHandler,
)
from dns2bgp_resolver.application.ports.clock import Clock
from dns2bgp_resolver.application.services.auto_list_sync import (
    AutoListSyncService,
    apply_keyword_filter,
    parse_domain_lines,
)
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.application.ports.repository import DomainListCreate
from dns2bgp_resolver.config import AutoListSettings, BirdSettings, RefreshSettings
from dns2bgp_resolver.domain import Domain
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import StaticFileBirdExporter
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository


class FixedClock(Clock):
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


class FakeDownloader:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    async def download(self, url: str) -> str:
        self.calls.append(url)
        return self.text


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
def clock():
    return FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))


async def _add_url_list(repo, url: str = "http://test/list") -> int:
    created = await repo.add_domain_list(
        DomainListCreate(name="test", type="url", url=url, enabled=True)
    )
    return created.id


@pytest.fixture
async def pipeline(repo, bird_path: Path):
    from dns2bgp_resolver.application.ports.dns_resolver import DnsResolver
    from dns2bgp_resolver.domain import DomainName, IpAddress, ResolvedAddress

    class FakeDns(DnsResolver):
        async def resolve_a(self, name: DomainName) -> list[ResolvedAddress]:
            return []

    exporter = StaticFileBirdExporter(
        BirdSettings(include_path=str(bird_path), nexthop="wg0", birdc_enable=False)
    )
    fixed_clock = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    pipe = ResolvePipeline(
        repository=repo,
        resolver=FakeDns(),
        exporter=exporter,
        clock=fixed_clock,
        refresh=RefreshSettings(max_interval=86400, min_interval=60),
        export_path=str(bird_path),
        export_min_interval=0,
    )
    return pipe


@pytest.mark.asyncio
async def test_parse_domain_lines_skips_invalid_and_comments():
    text = "# comment\nexample.com\n\ninvalid domain\ntest.org\n"
    names, skipped = parse_domain_lines(text)
    assert names == {"example.com", "test.org"}
    assert skipped == 1


@pytest.mark.asyncio
async def test_apply_keyword_filter():
    names = {"casino.example.com", "example.com", "my-casino.org"}
    filtered = apply_keyword_filter(names, ["casino"])
    assert filtered == {"example.com"}


@pytest.mark.asyncio
async def test_list_due_skipped_during_sync(repo):
    await repo._write_lock.acquire()
    try:
        assert repo.sync_in_progress
        due = await repo.list_due(datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert due == []
    finally:
        repo._write_lock.release()


@pytest.mark.asyncio
async def test_sync_auto_domains_add_and_remove(repo):
    list_id = await _add_url_list(repo)
    result = await repo.sync_list_domains(list_id, {"a.com", "b.com"})
    assert result.added == 2
    assert result.removed == 0

    auto = await repo.search_auto("")
    assert auto[1] == 2

    result2 = await repo.sync_list_domains(list_id, {"b.com", "c.com"})
    assert result2.added == 1
    assert result2.removed == 1

    names = {d.name.value for d in (await repo.search_auto(""))[0]}
    assert names == {"b.com", "c.com"}


@pytest.mark.asyncio
async def test_sync_skips_manual_conflict(repo):
    await repo.add(Domain.create("shared.com", source="manual"))
    list_id = await _add_url_list(repo)
    result = await repo.sync_list_domains(list_id, {"shared.com", "auto.com"})
    assert result.added == 1
    assert result.skipped_manual == 1

    manual = await repo.list_manual()
    assert len(manual) == 1
    assert str(manual[0].name) == "shared.com"


@pytest.mark.asyncio
async def test_sync_does_not_remove_manual(repo):
    await repo.add(Domain.create("manual.com", source="manual"))
    list_id = await _add_url_list(repo)
    await repo.sync_list_domains(list_id, {"auto.com"})
    manual = await repo.list_manual()
    assert len(manual) == 1
    assert str(manual[0].name) == "manual.com"


@pytest.mark.asyncio
async def test_list_manual_excludes_auto(repo):
    await repo.add(Domain.create("manual.com", source="manual"))
    list_id = await _add_url_list(repo)
    await repo.sync_list_domains(list_id, {"auto.com"})
    manual = await repo.list_manual()
    assert [str(d.name) for d in manual] == ["manual.com"]


@pytest.mark.asyncio
async def test_search_auto_pagination(repo):
    list_id = await _add_url_list(repo)
    await repo.sync_list_domains(list_id, {f"domain{i}.com" for i in range(5)})
    items, total = await repo.search_auto("", offset=0, limit=2)
    assert total == 5
    assert len(items) == 2
    items2, _ = await repo.search_auto("", offset=2, limit=2)
    assert len(items2) == 2


@pytest.mark.asyncio
async def test_search_auto_query_filter(repo):
    list_id = await _add_url_list(repo)
    await repo.sync_list_domains(list_id, {"casino.com", "example.com", "my-casino.org"})
    items, total = await repo.search_auto("casino", offset=0, limit=10)
    assert total == 2
    assert {str(d.name) for d in items} == {"casino.com", "my-casino.org"}


@pytest.mark.asyncio
async def test_exclude_keyword_crud(repo):
    assert await repo.add_exclude_keyword("Casino") is True
    assert await repo.add_exclude_keyword("casino") is False
    keywords = await repo.list_exclude_keywords()
    assert keywords == ["casino"]
    assert await repo.remove_exclude_keyword("casino") is True
    assert await repo.remove_exclude_keyword("casino") is False


@pytest.mark.asyncio
async def test_auto_list_sync_service(repo, pipeline, bird_path: Path, clock):
    await _add_url_list(repo)
    downloader = FakeDownloader("good.com\ncasino-bad.com\n")
    await repo.add_exclude_keyword("casino")
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True, url="http://test/list"),
        clock=clock,
        downloader=downloader,
    )
    result = await service.sync()
    assert result.added == 1
    assert result.removed == 0
    items, total = await repo.search_auto("")
    assert total == 1
    assert str(items[0].name) == "good.com"
    assert bird_path.is_file()


@pytest.mark.asyncio
async def test_auto_list_sync_replace(repo, pipeline, clock):
    await _add_url_list(repo)
    downloader = FakeDownloader("first.com\n")
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True),
        clock=clock,
        downloader=downloader,
    )
    await service.sync()
    downloader.text = "second.com\n"
    result = await service.sync()
    assert result.added == 1
    assert result.removed == 1
    items, total = await repo.search_auto("")
    assert total == 1
    assert str(items[0].name) == "second.com"


@pytest.mark.asyncio
async def test_remove_auto_domain_fails(repo):
    list_id = await _add_url_list(repo)
    await repo.sync_list_domains(list_id, {"auto.com"})
    handler = RemoveDomainHandler(repo)
    result = await handler.handle(RemoveDomainCommand(name="auto.com"))
    assert not result.ok
    assert "domain list" in (result.error or "")


@pytest.mark.asyncio
async def test_list_domains_handler_manual_only(repo):
    await repo.add(Domain.create("manual.com", source="manual"))
    list_id = await _add_url_list(repo)
    await repo.sync_list_domains(list_id, {"auto.com"})
    handler = ListDomainsHandler(repo)
    result = await handler.handle(ListDomainsCommand())
    assert result.ok
    assert len(result.data or []) == 1
    assert result.data[0].name == "manual.com"


@pytest.mark.asyncio
async def test_search_auto_domains_command(repo):
    list_id = await _add_url_list(repo)
    await repo.sync_list_domains(list_id, {"alpha.com", "beta.com", "alphabet.com"})
    handler = SearchAutoDomainsHandler(repo)
    result = await handler.handle(SearchAutoDomainsCommand(query="alpha", page=1, page_size=2))
    assert result.ok
    assert result.data is not None
    assert result.data.total == 2
    assert result.data.pages == 1


@pytest.mark.asyncio
async def test_sync_auto_list_command(repo, pipeline, clock):
    await _add_url_list(repo)
    downloader = FakeDownloader("cmd.com\n")
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True),
        clock=clock,
        downloader=downloader,
    )
    handler = SyncAutoListHandler(service)
    result = await handler.handle(SyncAutoListCommand())
    assert result.ok
    assert result.data is not None
    assert result.data.added == 1


@pytest.mark.asyncio
async def test_filter_commands(repo):
    bus = CommandBus()
    bus.register(ListExcludeKeywordsCommand, ListExcludeKeywordsHandler(repo))
    bus.register(AddExcludeKeywordCommand, AddExcludeKeywordHandler(repo))

    add = await bus.execute(AddExcludeKeywordCommand(keyword="porn"))
    assert add.ok
    listed = await bus.execute(ListExcludeKeywordsCommand())
    assert listed.data == ["porn"]
