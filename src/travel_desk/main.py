from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from travel_desk import __version__
from travel_desk.api import (
    routes_admin,
    routes_approvals,
    routes_health,
    routes_policies,
    routes_reports,
    routes_trips,
    routes_users,
)
from travel_desk.api.errors import install_error_handlers, new_trace_id
from travel_desk.config import Settings, get_settings, warn_live_credential_gaps
from travel_desk.db.database import initialize_database
from travel_desk.llm.slots import build_slot_extractor
from travel_desk.llm.vision import build_invitation_extractor
from travel_desk.network_guard import LOG_NAME, NetworkGuard, allowed_hosts_for
from travel_desk.policy.extraction import build_rule_extractor
from travel_desk.policy.rag import build_policy_qa
from travel_desk.workflow.engine import Workflow

logger = logging.getLogger("travel_desk")

# The workbench loads native ES modules, which browsers refuse unless served as JavaScript.
# On Windows, Python's mimetypes reads the registry and can map .js to text/plain (IMP-86).
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # HC-06 / AC-021: the egress boundary is on for the whole app lifetime and records every
        # attempt, so the evidence covers the real process too (not just the test client).
        guard = NetworkGuard(
            allowed_hosts=allowed_hosts_for(resolved.llm_mode, resolved.dashscope_base_url,
                                            resolved.provider_mode,
                                            qweather_api_host=resolved.qweather_api_host),
            log_path=resolved.data_dir / LOG_NAME)
        guard.install()
        app.state.network_guard = guard
        initialize_database(resolved)
        workflow = await Workflow(resolved).open()
        # Before serving: abort runs cut off mid-planning, re-drive pending deliveries (IMP-40).
        report = await workflow.recover()
        logger.info("startup recovery aborted=%d delivered=%d still_pending=%d", len(report["aborted"]),
                    len(report["delivered"]), len(report["still_pending"]))
        app.state.workflow = workflow
        try:
            yield
        finally:
            await workflow.close()
            guard.uninstall()

    app = FastAPI(
        title="Travel Compliance Desk",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = resolved
    # Set again (installed) by the lifespan; the placeholder keeps routes readable without startup.
    app.state.network_guard = NetworkGuard(
        allowed_hosts=allowed_hosts_for(resolved.llm_mode, resolved.dashscope_base_url,
                                        resolved.provider_mode,
                                        qweather_api_host=resolved.qweather_api_host))
    warn_live_credential_gaps(resolved)
    # Replaceable in tests; built from llm_mode (fake / recorded / real).
    app.state.slot_extractor = build_slot_extractor(resolved)
    app.state.invitation_extractor = build_invitation_extractor(resolved)
    app.state.rule_extractor = build_rule_extractor(resolved)
    app.state.policy_qa = build_policy_qa(resolved)

    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next):
        request.state.trace_id = new_trace_id(request.headers.get("X-Trace-Id"))
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        # The workbench has no build step and no versioned file names, so a heuristically cached
        # module would keep running old code after an update; revalidate via ETag instead (IMP-94).
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    install_error_handlers(app)
    app.include_router(routes_health.router)
    app.include_router(routes_users.router)
    app.include_router(routes_trips.router)
    app.include_router(routes_approvals.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_policies.router)
    app.include_router(routes_reports.router)

    app.mount("/static", StaticFiles(directory=resolved.static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(resolved.static_dir / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    return app


app = create_app()
