"""
PostgreSQL adapter hook.

Use the same SqlAlchemyDomainRepository with a postgresql+asyncpg:// URL:

    database:
      url: "postgresql+asyncpg://user:pass@localhost/dns2bgp"

Install optional dependency: pip install 'dns2bgp-resolver[postgres]'
"""

from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository

PostgresDomainRepository = SqlAlchemyDomainRepository

__all__ = ["PostgresDomainRepository"]
