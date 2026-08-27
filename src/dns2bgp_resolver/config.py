from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    url: str = "sqlite+aiosqlite:///./data/dns2bgp.db"


class DnsSettings(BaseModel):
    nameservers: list[str] = Field(default_factory=list)
    timeout: float = 3.0


class RefreshSettings(BaseModel):
    max_interval: int = 86400
    min_interval: int = 60
    resolve_concurrency: int = 8
    resolve_batch_size: int = 100


class BirdSettings(BaseModel):
    include_path: str = "./data/dns2bgp.routes"
    protocol_name: str = "dns2bgp"
    table: str = "master4"
    nexthop: str = "wg0"
    birdc_enable: bool = True
    birdc_bin: str = "birdc"
    birdc_socket: str = "/run/bird/bird.ctl"
    # Coalesce resolve-triggered bird reloads (0 = export on every change).
    export_min_interval: int = 60


class WebSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    api_key: str = "change-me"


class TelegramSettings(BaseModel):
    token: str = ""
    allowed_user_ids: list[int] = Field(default_factory=list)


class AutoListSettings(BaseModel):
    enabled: bool = True
    url: str = "https://antifilter.download/list/domains.lst"
    sync_interval: int = 86400
    sync_on_startup: bool = True
    exclude_keywords: list[str] = Field(default_factory=list)
    max_removal_ratio: float = 0.5
    confirm_ttl_seconds: int = 3600


class DnstapSettings(BaseModel):
    enabled: bool = False
    listen_unix: str = "./data/dnstap.sock"
    """Unix socket path; empty to disable unix listen."""
    listen_tcp: str = ""
    """Optional TCP listen addr host:port (e.g. 127.0.0.1:9255). Prefer over unix if unbound drops frames on AF_UNIX."""
    socket_mode: int = 0o666


class Ipv6Settings(BaseModel):
    """
    off — IPv4 pool only (default).
    suppress — export DomainIndex for dnsdist AAAA NODATA (VPN is IPv4-only).
    announce — future: collect AAAA and export Bird IPv6 (stub for now).
    """

    mode: Literal["off", "suppress", "announce"] = "off"
    dnsdist_list_path: str = "./data/aaaa-suppress.domains"
    dnsdist_reload_enable: bool = False
    dnsdist_reload_cmd: list[str] = Field(
        default_factory=lambda: [
            "dnsdist",
            "-c",
            "127.0.0.1:5199",
            "reloadDns2bgpDomains()",
        ]
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DNS2BGP_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    dns: DnsSettings = Field(default_factory=DnsSettings)
    refresh: RefreshSettings = Field(default_factory=RefreshSettings)
    bird: BirdSettings = Field(default_factory=BirdSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    auto_list: AutoListSettings = Field(default_factory=AutoListSettings)
    dnstap: DnstapSettings = Field(default_factory=DnstapSettings)
    ipv6: Ipv6Settings = Field(default_factory=Ipv6Settings)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Settings:
        data: dict[str, Any] = {}
        path = Path(config_path) if config_path else None
        if path is None:
            for candidate in (Path("config.yaml"), Path("config.example.yaml")):
                if candidate.is_file():
                    path = candidate
                    break
        if path is not None and path.is_file():
            with path.open(encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"config root must be a mapping: {path}")
            data = loaded
        return cls(**data)
