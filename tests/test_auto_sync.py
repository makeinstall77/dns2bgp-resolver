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
from dns2bgp_resolver.application.ports.sync_alert import SyncAlertNotifier
from dns2bgp_resolver.application.ports.repository import SyncPendingConfirmation
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


class RecordingNotifier(SyncAlertNotifier):
    def __init__(self) -> None:
        self.calls: list[SyncPendingConfirmation] = []

    async def notify_dangerous_sync(self, pending: SyncPendingConfirmation) -> None:
        self.calls.append(pending)

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

    manual, _total = await repo.list_manual()
    assert len(manual) == 1
    assert str(manual[0].name) == "shared.com"


@pytest.mark.asyncio
async def test_sync_does_not_remove_manual(repo):
    await repo.add(Domain.create("manual.com", source="manual"))
    list_id = await _add_url_list(repo)
    await repo.sync_list_domains(list_id, {"auto.com"})
    manual, _total = await repo.list_manual()
    assert len(manual) == 1
    assert str(manual[0].name) == "manual.com"


@pytest.mark.asyncio
async def test_list_manual_excludes_auto(repo):
    await repo.add(Domain.create("manual.com", source="manual"))
    list_id = await _add_url_list(repo)
    await repo.sync_list_domains(list_id, {"auto.com"})
    manual, total = await repo.list_manual()
    assert total == 1
    assert [str(d.name) for d in manual] == ["manual.com"]


@pytest.mark.asyncio
async def test_list_manual_pagination(repo):
    for i in range(5):
        await repo.add(Domain.create(f"m{i}.example.com", source="manual"))
    page1, total = await repo.list_manual(offset=0, limit=2)
    assert total == 5
    assert len(page1) == 2
    page2, _ = await repo.list_manual(offset=2, limit=2)
    assert len(page2) == 2
    page3, _ = await repo.list_manual(offset=4, limit=2)
    assert len(page3) == 1
    names = {str(d.name) for d in page1 + page2 + page3}
    assert len(names) == 5


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
    list_id = await _add_url_list(repo)
    downloader = FakeDownloader("first.com\n")
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True),
        clock=clock,
        downloader=downloader,
    )
    await service.sync_list(list_id)
    downloader.text = "second.com\n"
    blocked = await service.sync_list(list_id)
    assert blocked is not None
    assert blocked.needs_confirmation
    confirmed = await service.confirm_pending(blocked.pending_token or "")
    assert confirmed is not None
    assert confirmed.added == 1
    assert confirmed.removed == 1
    items, total = await repo.search_auto("")
    assert total == 1
    assert str(items[0].name) == "second.com"


@pytest.mark.asyncio
async def test_sync_blocks_empty_target(repo, pipeline, clock):
    list_id = await _add_url_list(repo)
    downloader = FakeDownloader("a.com\nb.com\n")
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True, max_removal_ratio=0.5),
        clock=clock,
        downloader=downloader,
    )
    await service.sync_list(list_id)
    downloader.text = ""
    result = await service.sync_list(list_id)
    assert result is not None
    assert result.needs_confirmation
    assert result.would_remove == 2
    assert result.pending_token
    items, total = await repo.search_auto("")
    assert total == 2
    pending = await repo.get_sync_pending(result.pending_token)
    assert pending is not None


@pytest.mark.asyncio
async def test_sync_blocks_high_removal_ratio(repo, pipeline, clock):
    list_id = await _add_url_list(repo)
    downloader = FakeDownloader("a.com\nb.com\nc.com\nd.com\n")
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True, max_removal_ratio=0.5),
        clock=clock,
        downloader=downloader,
    )
    await service.sync_list(list_id)
    downloader.text = "a.com\n"
    result = await service.sync_list(list_id)
    assert result is not None
    assert result.needs_confirmation
    assert result.would_remove == 3
    items, total = await repo.search_auto("")
    assert total == 4


@pytest.mark.asyncio
async def test_sync_force_bypasses_guards(repo, pipeline, clock):
    list_id = await _add_url_list(repo)
    downloader = FakeDownloader("a.com\nb.com\n")
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True, max_removal_ratio=0.5),
        clock=clock,
        downloader=downloader,
    )
    await service.sync_list(list_id)
    downloader.text = ""
    result = await service.sync_list(list_id, force=True)
    assert result is not None
    assert not result.needs_confirmation
    assert result.removed == 2
    items, total = await repo.search_auto("")
    assert total == 0


@pytest.mark.asyncio
async def test_confirm_pending_applies_snapshot(repo, pipeline, clock):
    list_id = await _add_url_list(repo)
    downloader = FakeDownloader("a.com\nb.com\nc.com\n")
    notifier = RecordingNotifier()
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True, max_removal_ratio=0.5),
        clock=clock,
        downloader=downloader,
        notifier=notifier,
    )
    await service.sync_list(list_id)
    downloader.text = "a.com\n"
    blocked = await service.sync_list(list_id)
    assert blocked is not None and blocked.needs_confirmation
    assert len(notifier.calls) == 1
    token = blocked.pending_token
    assert token

    # Simulate restart: new service/repo still sees pending
    pending = await repo.get_sync_pending(token)
    assert pending is not None
    assert pending.target_names == frozenset({"a.com"})

    confirmed = await service.confirm_pending(token)
    assert confirmed is not None
    assert confirmed.removed == 2
    assert confirmed.added == 0
    items, total = await repo.search_auto("")
    assert total == 1
    assert str(items[0].name) == "a.com"
    assert await repo.get_sync_pending(token) is None


@pytest.mark.asyncio
async def test_cancel_pending_leaves_list(repo, pipeline, clock):
    list_id = await _add_url_list(repo)
    downloader = FakeDownloader("a.com\nb.com\n")
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True),
        clock=clock,
        downloader=downloader,
    )
    await service.sync_list(list_id)
    downloader.text = ""
    blocked = await service.sync_list(list_id)
    assert blocked is not None and blocked.pending_token
    assert await service.cancel_pending(blocked.pending_token)
    items, total = await repo.search_auto("")
    assert total == 2
    assert await repo.get_sync_pending(blocked.pending_token) is None


@pytest.mark.asyncio
async def test_cleanup_expired_pending(repo, pipeline, clock):
    list_id = await _add_url_list(repo)
    downloader = FakeDownloader("a.com\n")
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True, confirm_ttl_seconds=1),
        clock=clock,
        downloader=downloader,
    )
    await service.sync_list(list_id)
    downloader.text = ""
    blocked = await service.sync_list(list_id)
    assert blocked is not None and blocked.pending_token
    from datetime import timedelta

    expired_clock = FixedClock(clock.now() + timedelta(seconds=10))
    service2 = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True, confirm_ttl_seconds=1),
        clock=expired_clock,
        downloader=downloader,
    )
    removed = await service2.cleanup_expired_pending()
    assert removed == 1
    assert await repo.get_sync_pending(blocked.pending_token) is None


@pytest.mark.asyncio
async def test_dedup_pending_does_not_renotify(repo, pipeline, clock):
    list_id = await _add_url_list(repo)
    downloader = FakeDownloader("a.com\nb.com\n")
    notifier = RecordingNotifier()
    service = AutoListSyncService(
        repository=repo,
        pipeline=pipeline,
        settings=AutoListSettings(enabled=True),
        clock=clock,
        downloader=downloader,
        notifier=notifier,
    )
    await service.sync_list(list_id)
    downloader.text = ""
    first = await service.sync_list(list_id)
    second = await service.sync_list(list_id)
    assert first is not None and second is not None
    assert first.pending_token == second.pending_token
    assert len(notifier.calls) == 1


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
    assert result.data is not None
    assert len(result.data.items) == 1
    assert result.data.items[0].name == "manual.com"
    assert result.data.items[0].id is not None


@pytest.mark.asyncio
async def test_list_domains_command_pagination(repo):
    for i in range(5):
        await repo.add(Domain.create(f"p{i}.example.com", source="manual"))
    handler = ListDomainsHandler(repo)
    result = await handler.handle(ListDomainsCommand(page=2, page_size=2))
    assert result.ok
    assert result.data is not None
    assert result.data.total == 5
    assert result.data.page == 2
    assert result.data.pages == 3
    assert len(result.data.items) == 2


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
