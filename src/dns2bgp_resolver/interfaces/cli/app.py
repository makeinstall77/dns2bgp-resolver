from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import typer

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    AddDomainListCommand,
    AddPrefixCommand,
    ClearDomainListCommand,
    ExportRoutesCommand,
    GetSettingsCommand,
    ListDomainListsCommand,
    ListDomainsCommand,
    ListPrefixesCommand,
    RemoveDomainCommand,
    RemoveDomainListCommand,
    RemovePrefixCommand,
    ResolveNowCommand,
    SetDefaultSyncIntervalCommand,
    SyncAutoListCommand,
    SyncDomainListCommand,
    UpdateDomainListCommand,
)
from dns2bgp_resolver.config import Settings
from dns2bgp_resolver.container import AppContainer, build_container

app = typer.Typer(name="dns2bgp", help="DNS → BGP pool resolver for VPN traffic steering", no_args_is_help=True)
lists_app = typer.Typer(name="lists", help="Manage domain lists")
settings_app = typer.Typer(name="settings", help="Runtime settings")
prefixes_app = typer.Typer(name="prefixes", help="Static IP/CIDR prefixes")
app.add_typer(lists_app)
app.add_typer(settings_app)
app.add_typer(prefixes_app)
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
    name: str = typer.Argument(..., help="Domain or *.example.com suffix mask"),
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


@prefixes_app.command("add")
def prefixes_add(
    ctx: typer.Context,
    cidr: str = typer.Argument(..., help="IPv4 address or CIDR (e.g. 149.154.160.0/20)"),
    name: str = typer.Option("", "--name", "-n", help="Optional label"),
) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(
            AddPrefixCommand(cidr=cidr, name=name.strip() or None)
        )
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@prefixes_app.command("remove")
def prefixes_remove(
    ctx: typer.Context,
    cidr: str = typer.Argument(...),
) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(RemovePrefixCommand(cidr=cidr))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@prefixes_app.command("list")
def prefixes_list(ctx: typer.Context) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(ListPrefixesCommand())
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        items = result.data.items if result.data else []
        if not items:
            typer.echo("(no static prefixes)")
            return
        for item in items:
            label = f"\t{item.name}" if item.name else ""
            typer.echo(f"{item.cidr}{label}")

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
        domains = result.data.items if result.data else []
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


@app.command("sync-auto")
def sync_auto(ctx: typer.Context) -> None:
    """Sync all enabled domain lists."""

    async def _action(container: AppContainer):
        result = await container.bus.execute(SyncAutoListCommand())
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        data = result.data
        if data:
            typer.echo(
                f"sync: added={data.added} removed={data.removed} "
                f"skipped_manual={data.skipped_manual}"
            )
        else:
            typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@lists_app.command("show")
def lists_show(ctx: typer.Context) -> None:
    """List configured domain lists."""

    async def _action(container: AppContainer):
        result = await container.bus.execute(ListDomainListsCommand())
        settings = await container.bus.execute(GetSettingsCommand())
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        default = settings.data.default_sync_interval if settings.data else 86400
        items = result.data or []
        if not items:
            typer.echo(f"(no lists, default interval={default}s)")
            return
        for item in items:
            state = "on" if item.enabled else "off"
            interval = item.sync_interval or default
            src = item.url if item.type == "url" else "file"
            typer.echo(f"[{item.id}] {item.name}\t{item.type}\t{state}\t{interval}s\t{src}")

    _run(_with_container(ctx.obj["config"], _action))


@lists_app.command("add-url")
def lists_add_url(
    ctx: typer.Context,
    url: str = typer.Argument(...),
    name: str = typer.Option("", "--name", "-n"),
    interval: Optional[int] = typer.Option(None, "--interval", "-i"),
) -> None:
    async def _action(container: AppContainer):
        list_name = name.strip() or url.rsplit("/", 1)[-1]
        result = await container.bus.execute(
            AddDomainListCommand(name=list_name, type="url", url=url, sync_interval=interval)
        )
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@lists_app.command("add-file")
def lists_add_file(
    ctx: typer.Context,
    path: Path = typer.Argument(...),
    name: str = typer.Option("", "--name", "-n"),
) -> None:
    async def _action(container: AppContainer):
        content = path.read_text(encoding="utf-8")
        list_name = name.strip() or path.stem
        result = await container.bus.execute(
            AddDomainListCommand(name=list_name, type="file", file_content=content)
        )
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@lists_app.command("enable")
def lists_enable(ctx: typer.Context, list_id: int = typer.Argument(...)) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(UpdateDomainListCommand(id=list_id, enabled=True))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@lists_app.command("disable")
def lists_disable(ctx: typer.Context, list_id: int = typer.Argument(...)) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(UpdateDomainListCommand(id=list_id, enabled=False))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@lists_app.command("clear")
def lists_clear(ctx: typer.Context, list_id: int = typer.Argument(...)) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(ClearDomainListCommand(id=list_id))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@lists_app.command("remove")
def lists_remove(ctx: typer.Context, list_id: int = typer.Argument(...)) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(RemoveDomainListCommand(id=list_id))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@lists_app.command("sync")
def lists_sync(
    ctx: typer.Context,
    list_id: Optional[int] = typer.Argument(None),
) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(SyncDomainListCommand(id=list_id))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        data = result.data
        if data:
            typer.echo(
                f"sync: added={data.added} removed={data.removed} "
                f"skipped_manual={data.skipped_manual}"
            )
        else:
            typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@settings_app.command("sync-interval")
def settings_sync_interval(
    ctx: typer.Context,
    seconds: int = typer.Argument(..., min=60),
) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(SetDefaultSyncIntervalCommand(seconds=seconds))
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(result.message)

    _run(_with_container(ctx.obj["config"], _action))


@settings_app.command("show")
def settings_show(ctx: typer.Context) -> None:
    async def _action(container: AppContainer):
        result = await container.bus.execute(GetSettingsCommand())
        if not result.ok:
            typer.secho(result.error or "error", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        assert result.data is not None
        typer.echo(f"default_sync_interval={result.data.default_sync_interval}")

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
