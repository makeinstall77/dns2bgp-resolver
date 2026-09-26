"""PostgreSQL auto-sync: boolean enabled, idempotent insert (PG18 + asyncpg).

Skipped unless DNS2BGP_TEST_PG_URL points at a database whose name ends with _test.
"""

from __future__ import annotations

import pytest

from dns2bgp_resolver.application.ports.repository import DomainListCreate
from pg_support import (
    assert_enabled_is_true,
    drop_pg_schema,
    make_pg_repository,
    postgres_test_url,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest.fixture
async def pg_repo():
    url = postgres_test_url()
    if url is None:
        pytest.skip("DNS2BGP_TEST_PG_URL is not set")
    repository = await make_pg_repository(url)
    try:
        yield repository
    finally:
        await drop_pg_schema(repository)


async def _add_url_list(repo, url: str = "http://test/list") -> int:
    created = await repo.add_domain_list(
        DomainListCreate(name="test", type="url", url=url, enabled=True)
    )
    return created.id


async def test_pg_sync_list_inserts_enabled_true_and_is_idempotent(pg_repo):
    list_id = await _add_url_list(pg_repo)
    names = {"auto-sync.example"}
    first = await pg_repo.sync_list_domains(list_id, names)
    assert first.added == 1
    assert first.removed == 0
    await assert_enabled_is_true(pg_repo, "auto-sync.example")

    second = await pg_repo.sync_list_domains(list_id, names)
    assert second.added == 0
    assert second.removed == 0
    await assert_enabled_is_true(pg_repo, "auto-sync.example")


async def test_pg_sync_legacy_inserts_enabled_true_and_is_idempotent(pg_repo):
    first = await pg_repo.sync_auto_domains({"legacy-auto.example"})
    assert first.added == 1
    await assert_enabled_is_true(pg_repo, "legacy-auto.example")

    second = await pg_repo.sync_auto_domains({"legacy-auto.example"})
    assert second.added == 0
    assert second.removed == 0
    await assert_enabled_is_true(pg_repo, "legacy-auto.example")
