from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    ListDomainsCommand,
    RemoveDomainCommand,
    ResolveNowCommand,
)
from dns2bgp_resolver.container import AppContainer

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class DomainCreate(BaseModel):
    name: str = Field(min_length=1, max_length=253)


def create_app(container: AppContainer) -> FastAPI:
    app = FastAPI(title="dns2bgp-resolver", version="0.1.0")
    app.state.container = container

    def require_api_key(
        x_api_key: Annotated[str | None, Header()] = None,
        api_key: Annotated[str | None, Query()] = None,
    ) -> None:
        expected = container.settings.web.api_key
        provided = x_api_key or api_key
        if expected and provided != expected:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")

    Auth = Annotated[None, Depends(require_api_key)]

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        result = await container.bus.execute(ListDomainsCommand())
        domains = result.data or []
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "domains": domains,
                "api_key": container.settings.web.api_key,
                "error": None,
            },
        )

    @app.post("/ui/add")
    async def ui_add(
        name: Annotated[str, Form()],
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        if container.settings.web.api_key and api_key != container.settings.web.api_key:
            raise HTTPException(status_code=401, detail="invalid api key")
        result = await container.bus.execute(AddDomainCommand(name=name.strip()))
        if not result.ok:
            # Still redirect; flash via query is enough for MVP
            return RedirectResponse(url=f"/?error={result.error}", status_code=303)
        return RedirectResponse(url="/", status_code=303)

    @app.post("/ui/remove")
    async def ui_remove(
        name: Annotated[str, Form()],
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        if container.settings.web.api_key and api_key != container.settings.web.api_key:
            raise HTTPException(status_code=401, detail="invalid api key")
        await container.bus.execute(RemoveDomainCommand(name=name.strip()))
        return RedirectResponse(url="/", status_code=303)

    @app.get("/api/domains")
    async def api_list(_: Auth):
        result = await container.bus.execute(ListDomainsCommand())
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        return [d.__dict__ for d in (result.data or [])]

    @app.post("/api/domains", status_code=201)
    async def api_add(body: DomainCreate, _: Auth):
        result = await container.bus.execute(AddDomainCommand(name=body.name))
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error)
        return result.data.__dict__ if result.data else {}

    @app.delete("/api/domains/{name}")
    async def api_remove(name: str, _: Auth):
        result = await container.bus.execute(RemoveDomainCommand(name=name))
        if not result.ok:
            raise HTTPException(status_code=404, detail=result.error)
        return {"removed": name}

    @app.post("/api/resolve")
    async def api_resolve(_: Auth, name: str | None = None):
        result = await container.bus.execute(ResolveNowCommand(name=name))
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error)
        return [s.__dict__ for s in (result.data or [])]

    return app
