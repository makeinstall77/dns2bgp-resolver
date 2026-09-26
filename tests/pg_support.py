"""Helpers for optional PostgreSQL tests (DNS2BGP_TEST_PG_URL)."""

from __future__ import annotations

import os

from sqlalchemy import text

from dns2bgp_resolver.infrastructure.db.models import Base
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository


def postgres_test_url() -> str | None:
    """Return the test DSN, or None if unset.

    The database name must end with ``_test`` so a production URL cannot be used.
    """
    url = os.environ.get("DNS2BGP_TEST_PG_URL", "").strip()
    if not url:
        return None
    db_name = url.rsplit("/", 1)[-1].split("?")[0]
    if not db_name.endswith("_test"):
        raise RuntimeError(
            "DNS2BGP_TEST_PG_URL database name must end with _test "
            "(refusing to run destructive tests)"
        )
    return url


async def make_pg_repository(url: str) -> SqlAlchemyDomainRepository:
    repository = SqlAlchemyDomainRepository(url)
    await repository.initialize()
    return repository


async def drop_pg_schema(repository: SqlAlchemyDomainRepository) -> None:
    async with repository._engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await repository.close()


async def assert_enabled_is_true(repository: SqlAlchemyDomainRepository, name: str) -> None:
    async with repository._engine.connect() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM domains WHERE name = :name AND enabled IS TRUE"),
            {"name": name},
        )
        count = result.scalar_one()
    assert count == 1
    items, _total = await repository.search_auto(name)
    match = [item for item in items if str(item.name) == name]
    assert match and match[0].enabled is True
