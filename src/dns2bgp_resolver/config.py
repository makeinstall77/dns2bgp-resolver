from __future__ import annotations

from pathlib import Path
from typing import Any

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


class BirdSettings(BaseModel):
    include_path: str = "./data/dns2bgp.routes"
    protocol_name: str = "dns2bgp"
    table: str = "master4"
    nexthop: str = "wg0"
    birdc_enable: bool = True
    birdc_bin: str = "birdc"
    birdc_socket: str = "/run/bird/bird.ctl"


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
