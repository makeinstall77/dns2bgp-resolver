from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dns2bgp_resolver.application.errors import DomainAlreadyExistsError
from dns2bgp_resolver.application.ports.repository import AutoSyncResult, DomainRepository
from dns2bgp_resolver.domain import Domain, DomainName, IpAddress, ResolvedAddress
from dns2bgp_resolver.infrastructure.db.models import (
    AddressRow,
    AutoExcludeKeywordRow,
    Base,
    DomainRow,
)

_BATCH_SIZE = 500


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _row_to_domain(row: DomainRow) -> Domain:
    return Domain(
        id=row.id,
        name=DomainName(row.name),
        source=row.source,  # type: ignore[arg-type]
        enabled=row.enabled,
        created_at=_ensure_aware(row.created_at),
        next_resolve_at=_ensure_aware(row.next_resolve_at),
        last_resolved_at=_ensure_aware(row.last_resolved_at),
        last_error=row.last_error,
        addresses=[
            ResolvedAddress(ip=IpAddress(a.ip), ttl_seconds=a.ttl_seconds) for a in row.addresses
        ],
    )


class SqlAlchemyDomainRepository(DomainRepository):
    """Works with SQLite and PostgreSQL via SQLAlchemy async URL."""

    def __init__(self, database_url: str) -> None:
        self._engine = create_async_engine(database_url, echo=False)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    async def initialize(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if conn.dialect.name == "sqlite":
                result = await conn.execute(text("PRAGMA table_info(domains)"))
                columns = {row[1] for row in result.fetchall()}
                if "source" not in columns:
                    await conn.execute(
                        text(
                            "ALTER TABLE domains ADD COLUMN source VARCHAR(16) "
                            "NOT NULL DEFAULT 'manual'"
                        )
                    )
                    await conn.execute(
                        text("CREATE INDEX IF NOT EXISTS ix_domains_source ON domains (source)")
                    )
            elif conn.dialect.name == "postgresql":
                result = await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'domains' AND column_name = 'source'"
                    )
                )
                if result.fetchone() is None:
                    await conn.execute(
                        text(
                            "ALTER TABLE domains ADD COLUMN source VARCHAR(16) "
                            "NOT NULL DEFAULT 'manual'"
                        )
                    )

    async def close(self) -> None:
        await self._engine.dispose()

    async def add(self, domain: Domain) -> Domain:
        row = DomainRow(
            name=str(domain.name),
            source=domain.source,
            enabled=domain.enabled,
        )
        async with self._session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DomainAlreadyExistsError(str(domain.name)) from exc
            await session.refresh(row)
            return _row_to_domain(row)

    async def remove(self, name: DomainName) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(select(DomainRow).where(DomainRow.name == str(name)))
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def get(self, name: DomainName) -> Domain | None:
        async with self._session_factory() as session:
            result = await session.execute(select(DomainRow).where(DomainRow.name == str(name)))
            row = result.scalar_one_or_none()
            return _row_to_domain(row) if row else None

    async def list_all(self) -> list[Domain]:
        async with self._session_factory() as session:
            result = await session.execute(select(DomainRow).order_by(DomainRow.name))
            rows = result.scalars().all()
            return [_row_to_domain(r) for r in rows]

    async def list_manual(self) -> list[Domain]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DomainRow)
                .where(DomainRow.source == "manual")
                .order_by(DomainRow.name)
            )
            rows = result.scalars().all()
            return [_row_to_domain(r) for r in rows]

    async def search_auto(
        self, query: str, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[Domain], int]:
        async with self._session_factory() as session:
            base = select(DomainRow).where(DomainRow.source == "auto")
            if query:
                pattern = f"%{query.lower()}%"
                base = base.where(func.lower(DomainRow.name).like(pattern))
            count_result = await session.execute(
                select(func.count()).select_from(base.subquery())
            )
            total = count_result.scalar_one()
            result = await session.execute(
                base.order_by(DomainRow.name).offset(offset).limit(limit)
            )
            rows = result.scalars().all()
            return [_row_to_domain(r) for r in rows], total

    async def sync_auto_domains(self, names: set[str]) -> AutoSyncResult:
        async with self._session_factory() as session:
            manual_result = await session.execute(
                select(DomainRow.name).where(DomainRow.source == "manual")
            )
            manual_names = {row[0] for row in manual_result.fetchall()}
            skipped_manual = len(names & manual_names)
            target = names - manual_names

            current_result = await session.execute(
                select(DomainRow).where(DomainRow.source == "auto")
            )
            current_rows = {row.name: row for row in current_result.scalars().all()}

            to_remove = set(current_rows) - target
            to_add = target - set(current_rows)

            for name in to_remove:
                await session.delete(current_rows[name])

            added = 0
            batch: list[DomainRow] = []
            for name in sorted(to_add):
                batch.append(DomainRow(name=name, source="auto", enabled=True))
                if len(batch) >= _BATCH_SIZE:
                    session.add_all(batch)
                    await session.flush()
                    added += len(batch)
                    batch.clear()
            if batch:
                session.add_all(batch)
                added += len(batch)

            await session.commit()
            return AutoSyncResult(
                added=added,
                removed=len(to_remove),
                skipped_manual=skipped_manual,
            )

    async def list_exclude_keywords(self) -> list[str]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AutoExcludeKeywordRow.keyword).order_by(AutoExcludeKeywordRow.keyword)
            )
            return list(result.scalars().all())

    async def add_exclude_keyword(self, keyword: str) -> bool:
        normalized = keyword.strip().lower()
        if not normalized:
            return False
        async with self._session_factory() as session:
            existing = await session.execute(
                select(AutoExcludeKeywordRow).where(
                    AutoExcludeKeywordRow.keyword == normalized
                )
            )
            if existing.scalar_one_or_none() is not None:
                return False
            session.add(AutoExcludeKeywordRow(keyword=normalized))
            await session.commit()
            return True

    async def remove_exclude_keyword(self, keyword: str) -> bool:
        normalized = keyword.strip().lower()
        async with self._session_factory() as session:
            result = await session.execute(
                select(AutoExcludeKeywordRow).where(
                    AutoExcludeKeywordRow.keyword == normalized
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def list_due(self, now: datetime) -> list[Domain]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DomainRow)
                .where(DomainRow.enabled.is_(True))
                .where(
                    (DomainRow.next_resolve_at.is_(None)) | (DomainRow.next_resolve_at <= now)
                )
                .order_by(DomainRow.name)
            )
            rows = result.scalars().all()
            return [_row_to_domain(r) for r in rows]

    async def replace_addresses(
        self,
        domain_id: int,
        addresses: list[ResolvedAddress],
        *,
        resolved_at: datetime,
        next_resolve_at: datetime,
        error: str | None = None,
    ) -> Domain:
        async with self._session_factory() as session:
            row = await session.get(DomainRow, domain_id)
            if row is None:
                raise ValueError(f"domain id not found: {domain_id}")

            row.addresses.clear()
            await session.flush()

            for addr in addresses:
                row.addresses.append(
                    AddressRow(
                        ip=str(addr.ip),
                        family=addr.ip.family,
                        ttl_seconds=addr.ttl_seconds,
                        first_seen=resolved_at,
                        last_seen=resolved_at,
                    )
                )
            row.last_resolved_at = resolved_at
            row.next_resolve_at = next_resolve_at
            row.last_error = error
            await session.commit()
            await session.refresh(row)
            return _row_to_domain(row)

    async def mark_resolve_error(
        self,
        domain_id: int,
        *,
        error: str,
        next_resolve_at: datetime,
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(DomainRow, domain_id)
            if row is None:
                return
            row.last_error = error
            row.next_resolve_at = next_resolve_at
            await session.commit()

    async def all_active_ips(self) -> list[str]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AddressRow.ip)
                .join(DomainRow)
                .where(DomainRow.enabled.is_(True))
                .where(AddressRow.family == 4)
            )
            return list(result.scalars().all())


# Alias for clarity in composition root
SqliteDomainRepository = SqlAlchemyDomainRepository
