from __future__ import annotations


class Dns2BgpError(Exception):
    """Base application error."""


class DomainAlreadyExistsError(Dns2BgpError):
    def __init__(self, name: str) -> None:
        super().__init__(f"domain already exists: {name}")
        self.name = name


class DomainNotFoundError(Dns2BgpError):
    def __init__(self, name: str) -> None:
        super().__init__(f"domain not found: {name}")
        self.name = name


class ValidationError(Dns2BgpError):
    pass
