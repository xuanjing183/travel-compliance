"""Policy endpoints #18–#23 (docs/api.md §3)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, Header, Request, UploadFile

from travel_desk.api.deps import current_user
from travel_desk.api.errors import get_trace_id
from travel_desk.services import policies, policy_agent, policy_qa

router = APIRouter(prefix="/api", tags=["policies"])


@router.post("/policy/ask")
async def ask(request: Request, body: Any = Body(None), user: dict = Depends(current_user)):
    state = request.app.state
    return await policy_qa.ask(state.settings, state.policy_qa, user, body, trace_id=get_trace_id(request))


@router.post("/policy/ask-agent")
async def ask_agent(request: Request, body: Any = Body(None), user: dict = Depends(current_user)):
    """Enhanced agent chain (real mode); fake/recorded falls back to the delivered path."""
    state = request.app.state
    return await policy_agent.ask_agent(state.settings, state.policy_agent, state.policy_qa, user, body,
                                        trace_id=get_trace_id(request))


@router.get("/policies")
def list_policies(request: Request, user: dict = Depends(current_user)):
    return policies.list_policies(request.app.state.settings, user)


@router.get("/policies/{version}")
def get_policy(version: str, request: Request, user: dict = Depends(current_user)):
    return policies.get_policy(request.app.state.settings, user, version)


@router.post("/policies", status_code=201)
async def upload_policy(request: Request, file: UploadFile | None = File(None), version: Any = Form(None),
                        effective_date: Any = Form(None), user: dict = Depends(current_user)):
    settings = request.app.state.settings
    data = await file.read(settings.max_upload_bytes + 1) if file is not None else None
    return policies.upload_policy(settings, user, version=version, effective_date=effective_date,
                                  filename=file.filename if file is not None else None, data=data,
                                  trace_id=get_trace_id(request))


@router.post("/policies/{version}/extract")
async def extract(version: str, request: Request, body: Any = Body(None), user: dict = Depends(current_user)):
    state = request.app.state
    return await policies.extract_rules(state.settings, state.rule_extractor, user, version, body,
                                        trace_id=get_trace_id(request))


@router.patch("/policies/{version}/rules/{rule_id}")
def update_rule(version: str, rule_id: str, request: Request, body: Any = Body(None),
                user: dict = Depends(current_user)):
    return policies.update_rule(request.app.state.settings, user, version, rule_id, body,
                                trace_id=get_trace_id(request))


@router.post("/policies/{version}/publish")
def publish(version: str, request: Request, user: dict = Depends(current_user),
            idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    return policies.publish(request.app.state.settings, user, version, idempotency_key=idempotency_key,
                            trace_id=get_trace_id(request))
