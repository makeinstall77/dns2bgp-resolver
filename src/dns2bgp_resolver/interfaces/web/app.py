from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from dns2bgp_resolver.application.commands import (
    AddDomainCommand,
    AddDomainListCommand,
    AddExcludeKeywordCommand,
    AddPrefixCommand,
    ClearDomainListCommand,
    GetSettingsCommand,
    ListDomainListsCommand,
    ListDomainsCommand,
    ListExcludeKeywordsCommand,
    ListPrefixesCommand,
    RemoveDomainCommand,
    RemoveDomainListCommand,
    RemoveExcludeKeywordCommand,
    RemovePrefixCommand,
    ResolveNowCommand,
    SearchAutoDomainsCommand,
    SetDefaultSyncIntervalCommand,
    SyncAutoListCommand,
    SyncDomainListCommand,
    UpdateDomainListCommand,
)
from dns2bgp_resolver.container import AppContainer

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class DomainCreate(BaseModel):
    name: str = Field(min_length=1, max_length=253)


class PrefixCreate(BaseModel):
    cidr: str = Field(min_length=1, max_length=43)
    name: str | None = None


class KeywordCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=64)


class DomainListCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(pattern="^(url|file)$")
    url: str | None = None
    file_content: str | None = None
    sync_interval: int | None = None


class DomainListUpdateBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    sync_interval: int | None = None
    url: str | None = None
    file_content: str | None = None
    clear_sync_interval: bool = False


class SettingsUpdateBody(BaseModel):
    default_sync_interval: int = Field(ge=60)


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

    def _check_form_api_key(api_key: str) -> None:
        expected = container.settings.web.api_key
        if expected and api_key != expected:
            raise HTTPException(status_code=401, detail="invalid api key")

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        lists_result = await container.bus.execute(ListDomainListsCommand())
        settings_result = await container.bus.execute(GetSettingsCommand())
        lists = lists_result.data or []
        default_interval = (
            settings_result.data.default_sync_interval if settings_result.data else 86400
        )
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "lists": lists,
                "default_interval": default_interval,
                "api_key": container.settings.web.api_key,
                "error": request.query_params.get("error"),
                "message": request.query_params.get("message"),
            },
        )

    @app.post("/ui/settings/interval")
    async def ui_settings_interval(
        seconds: Annotated[int, Form()],
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        _check_form_api_key(api_key)
        result = await container.bus.execute(SetDefaultSyncIntervalCommand(seconds=seconds))
        if not result.ok:
            return RedirectResponse(url=f"/settings?error={result.error}", status_code=303)
        return RedirectResponse(url=f"/settings?message={result.message}", status_code=303)

    @app.post("/ui/lists/add-url")
    async def ui_lists_add_url(
        name: Annotated[str, Form()],
        url: Annotated[str, Form()],
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        _check_form_api_key(api_key)
        result = await container.bus.execute(
            AddDomainListCommand(name=name.strip(), type="url", url=url.strip())
        )
        if not result.ok:
            return RedirectResponse(url=f"/settings?error={result.error}", status_code=303)
        return RedirectResponse(url=f"/settings?message={result.message}", status_code=303)

    @app.post("/ui/lists/add-file")
    async def ui_lists_add_file(
        file: Annotated[UploadFile, File()],
        name: Annotated[str, Form()] = "",
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        _check_form_api_key(api_key)
        content = (await file.read()).decode("utf-8", errors="replace")
        list_name = name.strip() or (file.filename or "upload").rsplit(".", 1)[0]
        result = await container.bus.execute(
            AddDomainListCommand(name=list_name, type="file", file_content=content)
        )
        if not result.ok:
            return RedirectResponse(url=f"/settings?error={result.error}", status_code=303)
        return RedirectResponse(url=f"/settings?message={result.message}", status_code=303)

    @app.post("/ui/lists/toggle")
    async def ui_lists_toggle(
        id: Annotated[int, Form()],
        enabled: Annotated[str, Form()],
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        _check_form_api_key(api_key)
        result = await container.bus.execute(
            UpdateDomainListCommand(id=id, enabled=enabled == "1")
        )
        if not result.ok:
            return RedirectResponse(url=f"/settings?error={result.error}", status_code=303)
        return RedirectResponse(url="/settings", status_code=303)

    @app.post("/ui/lists/sync")
    async def ui_lists_sync(
        id: Annotated[int, Form()],
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        _check_form_api_key(api_key)
        result = await container.bus.execute(SyncDomainListCommand(id=id))
        if not result.ok:
            return RedirectResponse(url=f"/settings?error={result.error}", status_code=303)
        return RedirectResponse(url=f"/settings?message={result.message}", status_code=303)

    @app.post("/ui/lists/sync-all")
    async def ui_lists_sync_all(api_key: Annotated[str, Form()] = "") -> RedirectResponse:
        _check_form_api_key(api_key)
        result = await container.bus.execute(SyncDomainListCommand())
        if not result.ok:
            return RedirectResponse(url=f"/settings?error={result.error}", status_code=303)
        return RedirectResponse(url=f"/settings?message={result.message}", status_code=303)

    @app.post("/ui/lists/clear")
    async def ui_lists_clear(
        id: Annotated[int, Form()],
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        _check_form_api_key(api_key)
        result = await container.bus.execute(ClearDomainListCommand(id=id))
        if not result.ok:
            return RedirectResponse(url=f"/settings?error={result.error}", status_code=303)
        return RedirectResponse(url=f"/settings?message={result.message}", status_code=303)

    @app.post("/ui/lists/remove")
    async def ui_lists_remove(
        id: Annotated[int, Form()],
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        _check_form_api_key(api_key)
        result = await container.bus.execute(RemoveDomainListCommand(id=id))
        if not result.ok:
            return RedirectResponse(url=f"/settings?error={result.error}", status_code=303)
        return RedirectResponse(url=f"/settings?message={result.message}", status_code=303)

    @app.get("/api/lists")
    async def api_lists(_: Auth):
        result = await container.bus.execute(ListDomainListsCommand())
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        settings = await container.bus.execute(GetSettingsCommand())
        return {
            "items": [item.__dict__ for item in (result.data or [])],
            "default_sync_interval": settings.data.default_sync_interval if settings.data else 86400,
        }

    @app.post("/api/lists", status_code=201)
    async def api_lists_add(body: DomainListCreateBody, _: Auth):
        result = await container.bus.execute(
            AddDomainListCommand(
                name=body.name,
                type=body.type,
                url=body.url,
                file_content=body.file_content,
                sync_interval=body.sync_interval,
            )
        )
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error)
        return result.data.__dict__ if result.data else {}

    @app.patch("/api/lists/{list_id}")
    async def api_lists_update(list_id: int, body: DomainListUpdateBody, _: Auth):
        result = await container.bus.execute(
            UpdateDomainListCommand(
                id=list_id,
                name=body.name,
                enabled=body.enabled,
                sync_interval=body.sync_interval,
                url=body.url,
                file_content=body.file_content,
                clear_sync_interval=body.clear_sync_interval,
            )
        )
        if not result.ok:
            raise HTTPException(status_code=404, detail=result.error)
        return result.data.__dict__ if result.data else {}

    @app.delete("/api/lists/{list_id}")
    async def api_lists_remove(list_id: int, _: Auth):
        result = await container.bus.execute(RemoveDomainListCommand(id=list_id))
        if not result.ok:
            raise HTTPException(status_code=404, detail=result.error)
        return {"removed": result.data}

    @app.post("/api/lists/{list_id}/sync")
    async def api_lists_sync(list_id: int, _: Auth):
        result = await container.bus.execute(SyncDomainListCommand(id=list_id))
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        return result.data.__dict__ if result.data else {}

    @app.post("/api/lists/{list_id}/clear")
    async def api_lists_clear(list_id: int, _: Auth):
        result = await container.bus.execute(ClearDomainListCommand(id=list_id))
        if not result.ok:
            raise HTTPException(status_code=404, detail=result.error)
        return {"cleared": result.data}

    @app.get("/api/settings")
    async def api_settings_get(_: Auth):
        result = await container.bus.execute(GetSettingsCommand())
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        return result.data.__dict__ if result.data else {}

    @app.patch("/api/settings")
    async def api_settings_patch(body: SettingsUpdateBody, _: Auth):
        result = await container.bus.execute(
            SetDefaultSyncIntervalCommand(seconds=body.default_sync_interval)
        )
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error)
        return result.data.__dict__ if result.data else {}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, tab: str = "manual") -> HTMLResponse:
        result = await container.bus.execute(ListDomainsCommand())
        domains = result.data.items if result.data else []
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "tab": tab,
                "domains": domains,
                "api_key": container.settings.web.api_key,
                "error": request.query_params.get("error"),
            },
        )

    @app.get("/auto", response_class=HTMLResponse)
    async def auto_page(
        request: Request,
        q: str = "",
        page: int = 1,
    ) -> HTMLResponse:
        page_size = 50
        search_result = await container.bus.execute(
            SearchAutoDomainsCommand(query=q, page=page, page_size=page_size)
        )
        search = search_result.data
        filters_result = await container.bus.execute(ListExcludeKeywordsCommand())
        filters = filters_result.data or []
        return templates.TemplateResponse(
            request,
            "auto.html",
            {
                "query": q,
                "search": search,
                "filters": filters,
                "api_key": container.settings.web.api_key,
                "error": request.query_params.get("error"),
                "message": request.query_params.get("message"),
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

    @app.post("/ui/auto/filter/add")
    async def ui_filter_add(
        keyword: Annotated[str, Form()],
        q: Annotated[str, Form()] = "",
        page: Annotated[int, Form()] = 1,
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        if container.settings.web.api_key and api_key != container.settings.web.api_key:
            raise HTTPException(status_code=401, detail="invalid api key")
        result = await container.bus.execute(AddExcludeKeywordCommand(keyword=keyword.strip()))
        params = f"q={q}&page={page}"
        if not result.ok:
            return RedirectResponse(url=f"/auto?{params}&error={result.error}", status_code=303)
        return RedirectResponse(url=f"/auto?{params}&message={result.message}", status_code=303)

    @app.post("/ui/auto/filter/remove")
    async def ui_filter_remove(
        keyword: Annotated[str, Form()],
        q: Annotated[str, Form()] = "",
        page: Annotated[int, Form()] = 1,
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        if container.settings.web.api_key and api_key != container.settings.web.api_key:
            raise HTTPException(status_code=401, detail="invalid api key")
        result = await container.bus.execute(RemoveExcludeKeywordCommand(keyword=keyword.strip()))
        params = f"q={q}&page={page}"
        if not result.ok:
            return RedirectResponse(url=f"/auto?{params}&error={result.error}", status_code=303)
        return RedirectResponse(url=f"/auto?{params}&message={result.message}", status_code=303)

    @app.post("/ui/auto/sync")
    async def ui_auto_sync(
        q: Annotated[str, Form()] = "",
        page: Annotated[int, Form()] = 1,
        api_key: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        if container.settings.web.api_key and api_key != container.settings.web.api_key:
            raise HTTPException(status_code=401, detail="invalid api key")
        result = await container.bus.execute(SyncAutoListCommand())
        params = f"q={q}&page={page}"
        if not result.ok:
            return RedirectResponse(url=f"/auto?{params}&error={result.error}", status_code=303)
        return RedirectResponse(url=f"/auto?{params}&message={result.message}", status_code=303)

    @app.get("/api/domains")
    async def api_list(_: Auth):
        result = await container.bus.execute(ListDomainsCommand())
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        return [d.__dict__ for d in (result.data.items if result.data else [])]

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

    @app.get("/api/auto/domains")
    async def api_auto_list(
        _: Auth,
        q: str = "",
        page: int = 1,
        page_size: int = 50,
    ):
        result = await container.bus.execute(
            SearchAutoDomainsCommand(query=q, page=page, page_size=page_size)
        )
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        data = result.data
        return {
            "items": [d.__dict__ for d in (data.items if data else [])],
            "total": data.total if data else 0,
            "page": data.page if data else 1,
            "pages": data.pages if data else 1,
            "page_size": data.page_size if data else page_size,
        }

    @app.get("/api/auto/filters")
    async def api_filters_list(_: Auth):
        result = await container.bus.execute(ListExcludeKeywordsCommand())
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        return {"keywords": result.data or []}

    @app.post("/api/auto/filters", status_code=201)
    async def api_filter_add(body: KeywordCreate, _: Auth):
        result = await container.bus.execute(AddExcludeKeywordCommand(keyword=body.keyword))
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error)
        return {"keyword": result.data}

    @app.delete("/api/auto/filters/{keyword}")
    async def api_filter_remove(keyword: str, _: Auth):
        result = await container.bus.execute(RemoveExcludeKeywordCommand(keyword=keyword))
        if not result.ok:
            raise HTTPException(status_code=404, detail=result.error)
        return {"removed": keyword}

    @app.post("/api/auto/sync")
    async def api_auto_sync(_: Auth):
        result = await container.bus.execute(SyncAutoListCommand())
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        data = result.data
        return data.__dict__ if data else {}

    @app.post("/api/resolve")
    async def api_resolve(_: Auth, name: str | None = None):
        result = await container.bus.execute(ResolveNowCommand(name=name))
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error)
        return [s.__dict__ for s in (result.data or [])]

    @app.get("/api/prefixes")
    async def api_list_prefixes(_: Auth):
        result = await container.bus.execute(ListPrefixesCommand())
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        return [p.__dict__ for p in (result.data or [])]

    @app.post("/api/prefixes", status_code=201)
    async def api_add_prefix(body: PrefixCreate, _: Auth):
        result = await container.bus.execute(AddPrefixCommand(cidr=body.cidr, name=body.name))
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error)
        return result.data.__dict__ if result.data else {}

    @app.delete("/api/prefixes/{cidr:path}")
    async def api_remove_prefix(cidr: str, _: Auth):
        result = await container.bus.execute(RemovePrefixCommand(cidr=cidr))
        if not result.ok:
            raise HTTPException(status_code=404, detail=result.error)
        return {"removed": cidr}

    @app.get("/api/index/stats")
    async def api_index_stats(_: Auth):
        return {
            "index_size": container.domain_index.size,
            "passive_hits": container.passive_collector.stats[1],
            "dnstap_seen": container.passive_collector.stats[0],
            "dnstap_enabled": container.dnstap_server is not None,
        }

    return app
