from __future__ import annotations

from pathlib import Path

import pytest

from dns2bgp_resolver.application.ports.repository import DomainListCreate, DomainListUpdate
from dns2bgp_resolver.domain import ip_to_prefix24
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository


@pytest.fixture
async def repo(tmp_path: Path):
    db = tmp_path / "lists.db"
    repository = SqlAlchemyDomainRepository(f"sqlite+aiosqlite:///{db}")
    await repository.initialize()
    yield repository
    await repository.close()


@pytest.mark.asyncio
async def test_domain_list_crud(repo):
    created = await repo.add_domain_list(
        DomainListCreate(name="src1", type="url", url="https://example.com/a.lst")
    )
    assert created.id > 0

    updated = await repo.update_domain_list(
        created.id, DomainListUpdate(enabled=False, sync_interval=3600)
    )
    assert updated is not None
    assert updated.enabled is False
    assert updated.sync_interval == 3600

    assert await repo.remove_domain_list(created.id) is True
    assert await repo.get_domain_list(created.id) is None


@pytest.mark.asyncio
async def test_per_list_sync_isolation(repo):
    list_a = await repo.add_domain_list(
        DomainListCreate(name="a", type="url", url="http://a", enabled=True)
    )
    list_b = await repo.add_domain_list(
        DomainListCreate(name="b", type="url", url="http://b", enabled=True)
    )

    await repo.sync_list_domains(list_a.id, {"a.com", "shared.com"})
    await repo.sync_list_domains(list_b.id, {"b.com"})

    await repo.sync_list_domains(list_a.id, {"a.com"})
    items, total = await repo.search_auto("")
    assert total == 2
    names = {d.name.value for d in items}
    assert names == {"a.com", "b.com"}


@pytest.mark.asyncio
async def test_clear_vs_remove(repo):
    created = await repo.add_domain_list(
        DomainListCreate(name="file", type="file", file_content="x.com\n", enabled=True)
    )
    await repo.sync_list_domains(created.id, {"x.com", "y.com"})
    cleared = await repo.clear_list_domains(created.id)
    assert cleared == 2
    _, total = await repo.search_auto("")
    assert total == 0
    assert await repo.get_domain_list(created.id) is not None


@pytest.mark.asyncio
async def test_clear_list_domains_bulk(repo):
    created = await repo.add_domain_list(
        DomainListCreate(name="big", type="url", url="http://big", enabled=True)
    )
    names = {f"d{i}.example.com" for i in range(2000)}
    await repo.sync_list_domains(created.id, names)
    cleared = await repo.clear_list_domains(created.id)
    assert cleared == 2000
    _, total = await repo.search_auto("")
    assert total == 0


@pytest.mark.asyncio
async def test_seed_only_once_after_list_removed(repo):
    seeded = await repo.seed_domain_list(
        name="antifilter",
        list_type="url",
        url="http://new",
        sync_interval=86400,
    )
    assert seeded is not None
    assert seeded.name == "antifilter"
    assert await repo.get_default_sync_interval() == 86400

    await repo.clear_list_domains(seeded.id)
    assert await repo.remove_domain_list(seeded.id) is True

    again = await repo.seed_domain_list(
        name="antifilter",
        list_type="url",
        url="http://new",
        sync_interval=86400,
    )
    assert again is None
    assert await repo.list_domain_lists() == []
    assert await repo.get_default_sync_interval() == 86400


@pytest.mark.asyncio
async def test_default_sync_interval(repo):
    assert await repo.get_default_sync_interval() == 86400
    await repo.set_default_sync_interval(7200)
    assert await repo.get_default_sync_interval() == 7200


def test_ip_to_prefix24_dedup():
    assert ip_to_prefix24("1.2.3.4") == "1.2.3.0/24"
    assert ip_to_prefix24("1.2.3.5") == "1.2.3.0/24"
    prefixes = {ip_to_prefix24("1.2.3.4"), ip_to_prefix24("1.2.3.5")}
    assert prefixes == {"1.2.3.0/24"}


def test_summarize_prefixes():
    from dns2bgp_resolver.domain import summarize_prefixes

    assert summarize_prefixes(["1.2.3.4/32"]) == ["1.2.3.4/32"]
    assert summarize_prefixes(["1.2.3.4/32", "1.2.3.10/32"]) == ["1.2.3.0/24"]
    assert summarize_prefixes(["1.2.3.0/24", "1.2.2.0/24"]) == ["1.2.2.0/23"]
    assert summarize_prefixes(["1.2.2.0/23", "1.2.0.0/23"]) == ["1.2.0.0/22"]
    assert summarize_prefixes(["1.2.3.4/32", "1.2.3.0/24"]) == ["1.2.3.0/24"]
    assert summarize_prefixes(["8.8.8.8/32", "1.2.3.4/32", "1.2.3.5/32"]) == [
        "1.2.3.0/24",
        "8.8.8.8/32",
    ]


def test_drop_covered_matches_pairwise_and_stays_fast():
    import random
    import time
    from ipaddress import IPv4Network

    from dns2bgp_resolver.domain import _drop_covered

    def brute(nets: list[IPv4Network]) -> set[IPv4Network]:
        uniq = list(set(nets))
        return {n for n in uniq if not any(n != o and n.subnet_of(o) for o in uniq)}

    rng = random.Random(0)
    samples: list[list[IPv4Network]] = [
        [],
        [IPv4Network("10.0.0.1/32")],
        [IPv4Network("10.0.0.0/8"), IPv4Network("10.1.2.3/32"), IPv4Network("11.0.0.0/8")],
        [IPv4Network("1.2.3.0/24"), IPv4Network("1.2.3.0/24")],
        [IPv4Network("1.2.2.0/24"), IPv4Network("1.2.3.0/24")],
    ]
    mixed: list[IPv4Network] = []
    for _ in range(40):
        addr = rng.randrange(1 << 32)
        length = rng.choice((8, 16, 20, 24, 28, 32))
        base = addr & (0xFFFFFFFF << (32 - length))
        mixed.append(IPv4Network((base, length)))
        mixed.append(IPv4Network((base, 32)))
    samples.append(mixed)

    for nets in samples:
        assert set(_drop_covered(nets)) == brute(nets)

    hosts = [
        IPv4Network(f"1.{(i >> 16) & 255}.{(i >> 8) & 255}.{i & 255}/32")
        for i in range(8000)
    ]
    started = time.perf_counter()
    kept = _drop_covered(hosts)
    elapsed = time.perf_counter() - started
    assert len(kept) == 8000
    assert elapsed < 0.5
