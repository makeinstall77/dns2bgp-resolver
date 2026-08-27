from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DomainListRow(Base):
    __tablename__ = "domain_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sync_interval: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AppSettingRow(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class DomainRow(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(253), unique=True, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", index=True)
    list_id: Mapped[int | None] = mapped_column(
        ForeignKey("domain_lists.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    match_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="suffix", server_default="suffix"
    )
    suppress_ipv6: Mapped[str] = mapped_column(
        String(8), nullable=False, default="default", server_default="default"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    next_resolve_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    addresses: Mapped[list["AddressRow"]] = relationship(
        "AddressRow",
        back_populates="domain",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class StaticPrefixRow(Base):
    __tablename__ = "static_prefixes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cidr: Mapped[str] = mapped_column(String(43), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PassiveHitRow(Base):
    __tablename__ = "passive_hits"
    __table_args__ = (UniqueConstraint("ip", name="uq_passive_ip"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip: Mapped[str] = mapped_column(String(45), nullable=False, index=True)
    matched_name: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AddressRow(Base):
    __tablename__ = "addresses"
    __table_args__ = (UniqueConstraint("domain_id", "ip", name="uq_domain_ip"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    # family reserved for future IPv6 (4 or 6)
    family: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    domain: Mapped[DomainRow] = relationship("DomainRow", back_populates="addresses")


class AutoExcludeKeywordRow(Base):
    __tablename__ = "auto_exclude_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)


class SyncPendingConfirmationRow(Base):
    __tablename__ = "sync_pending_confirmations"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    list_id: Mapped[int] = mapped_column(
        ForeignKey("domain_lists.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    list_name: Mapped[str] = mapped_column(String(128), nullable=False)
    target_names: Mapped[str] = mapped_column(Text, nullable=False)
    would_add: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    would_remove: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
