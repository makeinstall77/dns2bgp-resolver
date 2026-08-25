from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dns2bgp_resolver.application.errors import DomainAlreadyExistsError
from dns2bgp_resolver.application.ports.repository import (
    AutoSyncResult,
    DEFAULT_SYNC_INTERVAL_KEY,
    DEFAULT_SYNC_INTERVAL_SECONDS,
    DomainListCreate,
    DomainListUpdate,
    DomainRepository,
    SyncPendingConfirmation,
    SyncPreview,
)
from dns2bgp_resolver.domain import (
    Domain,
    DomainList,
    DomainName,
    IpAddress,
    PassiveHit,
    ResolvedAddress,
    StaticPrefix,
)
from dns2bgp_resolver.infrastructure.db.models import (
    AddressRow,
    AppSettingRow,
    AutoExcludeKeywordRow,
    Base,
    DomainListRow,
    DomainRow,
    PassiveHitRow,
    StaticPrefixRow,
    SyncPendingConfirmationRow,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500
_SQLITE_BUSY_TIMEOUT_MS = 30_000


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
        list_id=row.list_id,
        enabled=row.enabled,
        match_mode=getattr(row, "match_mode", None) or "suffix",  # type: ignore[arg-type]
        created_at=_ensure_aware(row.created_at),
        next_resolve_at=_ensure_aware(row.next_resolve_at),
        last_resolved_at=_ensure_aware(row.last_resolved_at),
        last_error=row.last_error,
        addresses=[
            ResolvedAddress(ip=IpAddress(a.ip), ttl_seconds=a.ttl_seconds) for a in row.addresses
        ],
    )


def _row_to_static_prefix(row: StaticPrefixRow) -> StaticPrefix:
    return StaticPrefix(
        id=row.id,
        cidr=row.cidr,
        name=row.name,
        enabled=row.enabled,
        created_at=_ensure_aware(row.created_at),
    )


def _row_to_domain_list(row: DomainListRow) -> DomainList:
    return DomainList(
        id=row.id,
        name=row.name,
        type=row.type,  # type: ignore[arg-type]
        url=row.url,
        file_content=row.file_content,
        enabled=row.enabled,
        sync_interval=row.sync_interval,
        last_sync_at=_ensure_aware(row.last_sync_at),
        created_at=_ensure_aware(row.created_at),
    )


def _row_to_pending(row: SyncPendingConfirmationRow) -> SyncPendingConfirmation:
    names = frozenset(json.loads(row.target_names))
    return SyncPendingConfirmation(
        token=row.token,
        list_id=row.list_id,
        list_name=row.list_name,
        target_names=names,
        would_add=row.would_add,
        would_remove=row.would_remove,
        current_count=row.current_count,
        created_at=_ensure_aware(row.created_at) or row.created_at,
        expires_at=_ensure_aware(row.expires_at) or row.expires_at,
    )


class SqlAlchemyDomainRepository(DomainRepository):
    """Works with SQLite and PostgreSQL via SQLAlchemy async URL."""

    def __init__(self, database_url: str) -> None:
        connect_args: dict[str, float] = {}
        if database_url.startswith("sqlite"):
            connect_args["timeout"] = 30.0
        self._engine = create_async_engine(database_url, echo=False, connect_args=connect_args)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )
        self._write_lock = asyncio.Lock()

    @property
    def sync_in_progress(self) -> bool:
        return self._write_lock.locked()

    async def _configure_sqlite(self, conn) -> None:
        await conn.execute(text(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}"))
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))

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
                if "list_id" not in columns:
                    await conn.execute(text("ALTER TABLE domains ADD COLUMN list_id INTEGER"))
                    await conn.execute(
                        text("CREATE INDEX IF NOT EXISTS ix_domains_list_id ON domains (list_id)")
                    )
                if "match_mode" not in columns:
                    await conn.execute(
                        text(
                            "ALTER TABLE domains ADD COLUMN match_mode VARCHAR(16) "
                            "NOT NULL DEFAULT 'suffix'"
                        )
                    )
                await self._configure_sqlite(conn)
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
                result = await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'domains' AND column_name = 'list_id'"
                    )
                )
                if result.fetchone() is None:
                    await conn.execute(text("ALTER TABLE domains ADD COLUMN list_id INTEGER"))
                result = await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'domains' AND column_name = 'match_mode'"
                    )
                )
                if result.fetchone() is None:
                    await conn.execute(
                        text(
                            "ALTER TABLE domains ADD COLUMN match_mode VARCHAR(16) "
                            "NOT NULL DEFAULT 'suffix'"
                        )
                    )

    async def close(self) -> None:
        await self._engine.dispose()

    async def add(self, domain: Domain) -> Domain:
        row = DomainRow(
            name=str(domain.name),
            source=domain.source,
            list_id=domain.list_id,
            enabled=domain.enabled,
            match_mode=domain.match_mode,
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

    async def get_by_id(self, domain_id: int) -> Domain | None:
        async with self._session_factory() as session:
            result = await session.execute(select(DomainRow).where(DomainRow.id == domain_id))
            row = result.scalar_one_or_none()
            return _row_to_domain(row) if row else None

    async def list_all(self) -> list[Domain]:
        async with self._session_factory() as session:
            result = await session.execute(select(DomainRow).order_by(DomainRow.name))
            rows = result.scalars().all()
            return [_row_to_domain(r) for r in rows]

    async def list_manual(
        self, *, offset: int = 0, limit: int | None = None
    ) -> tuple[list[Domain], int]:
        async with self._session_factory() as session:
            base = select(DomainRow).where(DomainRow.source == "manual")
            count_result = await session.execute(
                select(func.count()).select_from(base.subquery())
            )
            total = count_result.scalar_one()
            query = base.order_by(DomainRow.name).offset(offset)
            if limit is not None:
                query = query.limit(limit)
            result = await session.execute(query)
            rows = result.scalars().all()
            return [_row_to_domain(r) for r in rows], total

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
            result = await session.execute(select(DomainListRow.id).limit(1))
            list_id = result.scalar_one_or_none()
        if list_id is None:
            return await self._sync_domains_legacy(names)
        return await self.sync_list_domains(list_id, names)

    async def sync_list_domains(self, list_id: int, names: set[str]) -> AutoSyncResult:
        async with self._write_lock:
            async with self._session_factory() as session:
                manual_result = await session.execute(
                    select(DomainRow.name).where(DomainRow.source == "manual")
                )
                manual_names = {row[0] for row in manual_result.fetchall()}

            skipped_manual = len(names & manual_names)
            target = names - manual_names

            async with self._engine.begin() as conn:
                if conn.dialect.name == "sqlite":
                    await self._configure_sqlite(conn)

                await conn.execute(
                    text(
                        "CREATE TEMP TABLE IF NOT EXISTS sync_target "
                        "(name VARCHAR(253) PRIMARY KEY)"
                    )
                )
                await conn.execute(text("DELETE FROM sync_target"))
                target_list = sorted(target)
                for offset in range(0, len(target_list), _BATCH_SIZE):
                    batch = target_list[offset : offset + _BATCH_SIZE]
                    await conn.execute(
                        text("INSERT INTO sync_target (name) VALUES (:name)"),
                        [{"name": name} for name in batch],
                    )

                remove_result = await conn.execute(
                    text(
                        "DELETE FROM domains WHERE source = 'auto' AND list_id = :list_id "
                        "AND name NOT IN (SELECT name FROM sync_target)"
                    ),
                    {"list_id": list_id},
                )
                removed = remove_result.rowcount if remove_result.rowcount >= 0 else 0

                add_result = await conn.execute(
                    text(
                        "INSERT INTO domains (name, source, list_id, enabled, match_mode) "
                        "SELECT t.name, 'auto', :list_id, 1, 'suffix' FROM sync_target t "
                        "WHERE NOT EXISTS (SELECT 1 FROM domains d WHERE d.name = t.name)"
                    ),
                    {"list_id": list_id},
                )
                added = add_result.rowcount if add_result.rowcount >= 0 else 0

            return AutoSyncResult(
                added=added,
                removed=removed,
                skipped_manual=skipped_manual,
            )

    async def _sync_domains_legacy(self, names: set[str]) -> AutoSyncResult:
        async with self._write_lock:
            async with self._session_factory() as session:
                manual_result = await session.execute(
                    select(DomainRow.name).where(DomainRow.source == "manual")
                )
                manual_names = {row[0] for row in manual_result.fetchall()}

            skipped_manual = len(names & manual_names)
            target = names - manual_names

            async with self._engine.begin() as conn:
                if conn.dialect.name == "sqlite":
                    await self._configure_sqlite(conn)

                await conn.execute(
                    text(
                        "CREATE TEMP TABLE IF NOT EXISTS sync_target "
                        "(name VARCHAR(253) PRIMARY KEY)"
                    )
                )
                await conn.execute(text("DELETE FROM sync_target"))
                target_list = sorted(target)
                for offset in range(0, len(target_list), _BATCH_SIZE):
                    batch = target_list[offset : offset + _BATCH_SIZE]
                    await conn.execute(
                        text("INSERT INTO sync_target (name) VALUES (:name)"),
                        [{"name": name} for name in batch],
                    )

                remove_result = await conn.execute(
                    text(
                        "DELETE FROM domains WHERE source = 'auto' "
                        "AND name NOT IN (SELECT name FROM sync_target)"
                    )
                )
                removed = remove_result.rowcount if remove_result.rowcount >= 0 else 0

                add_result = await conn.execute(
                    text(
                        "INSERT INTO domains (name, source, enabled, match_mode) "
                        "SELECT t.name, 'auto', 1, 'suffix' FROM sync_target t "
                        "WHERE NOT EXISTS (SELECT 1 FROM domains d WHERE d.name = t.name)"
                    )
                )
                added = add_result.rowcount if add_result.rowcount >= 0 else 0

            return AutoSyncResult(
                added=added,
                removed=removed,
                skipped_manual=skipped_manual,
            )

    async def preview_list_sync(self, list_id: int, names: set[str]) -> SyncPreview:
        async with self._session_factory() as session:
            manual_result = await session.execute(
                select(DomainRow.name).where(DomainRow.source == "manual")
            )
            manual_names = {row[0] for row in manual_result.fetchall()}

            current_result = await session.execute(
                select(DomainRow.name).where(
                    DomainRow.source == "auto", DomainRow.list_id == list_id
                )
            )
            current = {row[0] for row in current_result.fetchall()}

        skipped_manual = len(names & manual_names)
        target = names - manual_names
        would_add = len(target - current)
        would_remove = len(current - target)
        return SyncPreview(
            list_id=list_id,
            current_count=len(current),
            target_count=len(target),
            would_add=would_add,
            would_remove=would_remove,
            skipped_manual=skipped_manual,
            target_names=frozenset(target),
        )

    async def save_sync_pending(
        self,
        *,
        token: str,
        list_id: int,
        list_name: str,
        target_names: set[str],
        would_add: int,
        would_remove: int,
        current_count: int,
        created_at: datetime,
        expires_at: datetime,
    ) -> SyncPendingConfirmation:
        payload = json.dumps(sorted(target_names))
        async with self._session_factory() as session:
            existing = await session.execute(
                select(SyncPendingConfirmationRow).where(
                    SyncPendingConfirmationRow.list_id == list_id
                )
            )
            old = existing.scalar_one_or_none()
            if old is not None:
                await session.delete(old)
                await session.flush()
            row = SyncPendingConfirmationRow(
                token=token,
                list_id=list_id,
                list_name=list_name,
                target_names=payload,
                would_add=would_add,
                would_remove=would_remove,
                current_count=current_count,
                created_at=created_at,
                expires_at=expires_at,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_pending(row)

    async def get_sync_pending(self, token: str) -> SyncPendingConfirmation | None:
        async with self._session_factory() as session:
            row = await session.get(SyncPendingConfirmationRow, token)
            return _row_to_pending(row) if row else None

    async def get_sync_pending_by_list(self, list_id: int) -> SyncPendingConfirmation | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(SyncPendingConfirmationRow).where(
                    SyncPendingConfirmationRow.list_id == list_id
                )
            )
            row = result.scalar_one_or_none()
            return _row_to_pending(row) if row else None

    async def delete_sync_pending(self, token: str) -> bool:
        async with self._session_factory() as session:
            row = await session.get(SyncPendingConfirmationRow, token)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def cleanup_expired_sync_pending(self, now: datetime) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(SyncPendingConfirmationRow).where(
                    SyncPendingConfirmationRow.expires_at <= now
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def clear_list_domains(self, list_id: int) -> int:
        async with self._write_lock:
            async with self._session_factory() as session:
                domain_ids = select(DomainRow.id).where(
                    DomainRow.source == "auto", DomainRow.list_id == list_id
                )
                await session.execute(
                    delete(AddressRow).where(AddressRow.domain_id.in_(domain_ids))
                )
                result = await session.execute(
                    delete(DomainRow).where(
                        DomainRow.source == "auto", DomainRow.list_id == list_id
                    )
                )
                await session.commit()
                return int(result.rowcount or 0)

    async def list_domain_lists(self) -> list[DomainList]:
        async with self._session_factory() as session:
            result = await session.execute(select(DomainListRow).order_by(DomainListRow.name))
            return [_row_to_domain_list(r) for r in result.scalars().all()]

    async def get_domain_list(self, list_id: int) -> DomainList | None:
        async with self._session_factory() as session:
            row = await session.get(DomainListRow, list_id)
            return _row_to_domain_list(row) if row else None

    async def add_domain_list(self, data: DomainListCreate) -> DomainList:
        row = DomainListRow(
            name=data.name,
            type=data.type,
            url=data.url,
            file_content=data.file_content,
            enabled=data.enabled,
            sync_interval=data.sync_interval,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_domain_list(row)

    async def update_domain_list(self, list_id: int, data: DomainListUpdate) -> DomainList | None:
        async with self._session_factory() as session:
            row = await session.get(DomainListRow, list_id)
            if row is None:
                return None
            if data.name is not None:
                row.name = data.name
            if data.enabled is not None:
                row.enabled = data.enabled
            if data.unset_sync_interval:
                row.sync_interval = None
            elif data.sync_interval is not None:
                row.sync_interval = data.sync_interval
            if data.url is not None:
                row.url = data.url
            if data.file_content is not None:
                row.file_content = data.file_content
            await session.commit()
            await session.refresh(row)
            return _row_to_domain_list(row)

    async def remove_domain_list(self, list_id: int) -> bool:
        async with self._session_factory() as session:
            row = await session.get(DomainListRow, list_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def mark_list_synced(self, list_id: int, synced_at: datetime) -> None:
        async with self._session_factory() as session:
            row = await session.get(DomainListRow, list_id)
            if row is None:
                return
            row.last_sync_at = synced_at
            await session.commit()

    async def get_default_sync_interval(self) -> int:
        async with self._session_factory() as session:
            row = await session.get(AppSettingRow, DEFAULT_SYNC_INTERVAL_KEY)
            if row is None:
                return DEFAULT_SYNC_INTERVAL_SECONDS
            try:
                return int(row.value)
            except ValueError:
                return DEFAULT_SYNC_INTERVAL_SECONDS

    async def set_default_sync_interval(self, seconds: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(AppSettingRow, DEFAULT_SYNC_INTERVAL_KEY)
            if row is None:
                session.add(AppSettingRow(key=DEFAULT_SYNC_INTERVAL_KEY, value=str(seconds)))
            else:
                row.value = str(seconds)
            await session.commit()

    async def seed_domain_list(
        self,
        *,
        name: str,
        list_type: str,
        url: str | None,
        sync_interval: int,
    ) -> DomainList:
        async with self._session_factory() as session:
            existing = await session.execute(select(DomainListRow.id).limit(1))
            if existing.scalar_one_or_none() is not None:
                result = await session.execute(select(DomainListRow).limit(1))
                row = result.scalar_one()
                return _row_to_domain_list(row)

            session.add(
                AppSettingRow(
                    key=DEFAULT_SYNC_INTERVAL_KEY,
                    value=str(sync_interval),
                )
            )
            list_row = DomainListRow(
                name=name,
                type=list_type,
                url=url,
                enabled=True,
            )
            session.add(list_row)
            await session.flush()

            await session.execute(
                text(
                    "UPDATE domains SET list_id = :list_id "
                    "WHERE source = 'auto' AND list_id IS NULL"
                ),
                {"list_id": list_row.id},
            )
            await session.commit()
            await session.refresh(list_row)
            return _row_to_domain_list(list_row)

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
        if self.sync_in_progress:
            logger.debug("skipping list_due while auto sync is in progress")
            return []
        async with self._session_factory() as session:
            result = await session.execute(
                select(DomainRow)
                .where(DomainRow.enabled.is_(True))
                .where(DomainRow.source == "manual")
                .where(DomainRow.match_mode != "suffix")
                .where(
                    (DomainRow.next_resolve_at.is_(None)) | (DomainRow.next_resolve_at <= now)
                )
                .order_by(DomainRow.name)
            )
            rows = result.scalars().all()
            return [_row_to_domain(r) for r in rows]

    async def list_index_names(self) -> list[str]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DomainRow.name).where(DomainRow.enabled.is_(True))
            )
            return list(result.scalars().all())

    async def list_index_rules(self) -> list[tuple[str, str]]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DomainRow.name, DomainRow.match_mode).where(
                    DomainRow.enabled.is_(True)
                )
            )
            return [(name, mode or "suffix") for name, mode in result.all()]

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

            by_ip = {a.ip: a for a in row.addresses}
            new_by_ip = {str(a.ip): a for a in addresses}
            for ip, addr_row in list(by_ip.items()):
                if ip not in new_by_ip:
                    row.addresses.remove(addr_row)
            for ip, addr in new_by_ip.items():
                existing = by_ip.get(ip)
                if existing is not None:
                    existing.ttl_seconds = addr.ttl_seconds
                    existing.last_seen = resolved_at
                else:
                    row.addresses.append(
                        AddressRow(
                            ip=ip,
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
                .where(DomainRow.source == "manual")
                .where(DomainRow.match_mode != "suffix")
                .where(AddressRow.family == 4)
            )
            return list(result.scalars().all())

    async def add_static_prefix(self, prefix: StaticPrefix) -> StaticPrefix:
        row = StaticPrefixRow(cidr=prefix.cidr, name=prefix.name, enabled=prefix.enabled)
        async with self._session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DomainAlreadyExistsError(prefix.cidr) from exc
            await session.refresh(row)
            return _row_to_static_prefix(row)

    async def remove_static_prefix(self, cidr: str) -> bool:
        from ipaddress import ip_network

        normalized = str(ip_network(cidr, strict=False))
        async with self._session_factory() as session:
            result = await session.execute(
                select(StaticPrefixRow).where(StaticPrefixRow.cidr == normalized)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def list_static_prefixes(self) -> list[StaticPrefix]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(StaticPrefixRow).order_by(StaticPrefixRow.cidr)
            )
            return [_row_to_static_prefix(r) for r in result.scalars().all()]

    async def upsert_passive_hit(
        self, ip: str, matched_name: str, *, seen_at: datetime
    ) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(select(PassiveHitRow).where(PassiveHitRow.ip == ip))
            row = result.scalar_one_or_none()
            if row is None:
                session.add(
                    PassiveHitRow(
                        ip=ip,
                        matched_name=matched_name,
                        first_seen=seen_at,
                        last_seen=seen_at,
                    )
                )
                await session.commit()
                return True
            row.matched_name = matched_name
            row.last_seen = seen_at
            await session.commit()
            return False

    async def list_passive_ips(self) -> list[str]:
        async with self._session_factory() as session:
            result = await session.execute(select(PassiveHitRow.ip))
            return list(result.scalars().all())

    async def list_passive_hits(self, *, limit: int = 100) -> list[PassiveHit]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(PassiveHitRow)
                .order_by(PassiveHitRow.last_seen.desc())
                .limit(limit)
            )
            return [
                PassiveHit(
                    ip=r.ip,
                    matched_name=r.matched_name,
                    first_seen=_ensure_aware(r.first_seen),
                    last_seen=_ensure_aware(r.last_seen),
                )
                for r in result.scalars().all()
            ]


# Alias for clarity in composition root
SqliteDomainRepository = SqlAlchemyDomainRepository
