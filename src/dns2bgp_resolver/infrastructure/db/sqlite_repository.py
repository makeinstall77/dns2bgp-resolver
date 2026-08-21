from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dns2bgp_resolver.application.errors import DomainAlreadyExistsError
from dns2bgp_resolver.application.ports.repository import DomainRepository
from dns2bgp_resolver.domain import Domain, DomainName, IpAddress, ResolvedAddress
from dns2bgp_resolver.infrastructure.db.models import AddressRow, Base, DomainRow


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

    async def close(self) -> None:
        await self._engine.dispose()

    async def add(self, domain: Domain) -> Domain:
        row = DomainRow(name=str(domain.name), enabled=domain.enabled)
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
