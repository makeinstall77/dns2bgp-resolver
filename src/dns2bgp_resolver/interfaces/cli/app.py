from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import typer

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    ExportRoutesCommand,
    ListDomainsCommand,
    RemoveDomainCommand,
    ResolveNowCommand,
)
from dns2bgp_resolver.config import Settings
from dns2bgp_resolver.container import AppContainer, build_container

app = typer.Typer(name="dns2bgp", help="DNS → BGP pool resolver for VPN traffic steering", no_args_is_help=True)
logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load_container(config: Optional[Path]) -> AppContainer:
    settings = Settings.load(config)
    return build_container(settings)


def _run(coro):
    return asyncio.run(coro)


async def _with_container(config: Optional[Path], action):
    container = _load_container(config)
    await container.startup()
    try:
        return await action(container)
    finally:
        await container.shutdown()


@app.callback()
def main(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to YAML config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config


@app.command("add")
def add_domain(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Domain name to track"),
) -> None:
    """Add a domain, resolve it, and update the bird include file."""

    async def _action(container: AppContainer):
        result = await container.bus.execute(AddDomainCommand(name=name))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        view = result.data
        ips = ", ".join(view.addresses) if view and view.addresses else "(none yet)"
        typer.echo(f"{result.message}: {ips}")

    _run(_with_container(ctx.obj["config"], _action))


@app.command("remove")
def remove_domain(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Domain name to remove"),
) -> None:
    """Remove a domain and refresh the bird include file."""

    async def _action(container: AppContainer):
        result = await container.bus.execute(RemoveDomainCommand(name=name))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@app.command("list")
def list_domains(ctx: typer.Context) -> None:
    """List tracked domains and their addresses."""

    async def _action(container: AppContainer):
        result = await container.bus.execute(ListDomainsCommand())
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        domains = result.data or []
        if not domains:
            typer.echo("(no domains)")
            return
        for d in domains:
            ips = ", ".join(d.addresses) if d.addresses else "-"
            err = f" err={d.last_error}" if d.last_error else ""
            typer.echo(f"{d.name}\t{ips}{err}")

    _run(_with_container(ctx.obj["config"], _action))


@app.command("resolve")
def resolve_domains(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(None, help="Domain to resolve (all if omitted)"),
) -> None:
    """Force-resolve one or all domains."""

    async def _action(container: AppContainer):
        result = await container.bus.execute(ResolveNowCommand(name=name))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        for s in result.data or []:
            status = "changed" if s.changed else "unchanged"
            if s.error:
                typer.echo(f"{s.domain}: ERROR {s.error}")
            else:
                typer.echo(f"{s.domain}: {status} [{', '.join(s.addresses)}]")

    _run(_with_container(ctx.obj["config"], _action))


@app.command("export")
def export_routes(ctx: typer.Context) -> None:
    """Rewrite the bird include file from the current IP pool."""

    async def _action(container: AppContainer):
        result = await container.bus.execute(ExportRoutesCommand())
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        summary = result.data
        typer.echo(f"exported {summary.prefix_count} prefix(es) → {summary.path}")

    _run(_with_container(ctx.obj["config"], _action))


@app.command("serve")
def serve(
    ctx: typer.Context,
    host: Optional[str] = typer.Option(None, help="Web bind host"),
    port: Optional[int] = typer.Option(None, help="Web bind port"),
    no_telegram: bool = typer.Option(False, help="Disable telegram bot"),
    no_web: bool = typer.Option(False, help="Disable web UI/API"),
) -> None:
    """Run scheduler + web UI + telegram bot."""

    async def _action(container: AppContainer):
        from dns2bgp_resolver.interfaces.runtime import run_services

        await run_services(
            container,
            host=host,
            port=port,
            enable_web=not no_web,
            enable_telegram=not no_telegram,
        )

    _run(_with_container(ctx.obj["config"], _action))


if __name__ == "__main__":
    app()
