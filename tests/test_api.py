"""TASK-006: HTTP layer — routes, SSE framing, permissions (AC-002/003/009, docs/api.md).

Everything goes through `TestClient` against the real app (lifespan opens the
workflow and its checkpoint, Fake/recorded model mode, Demo providers). The
fake slot extractor is a deterministic parser, not a model; tests that need a
model failure swap `app.state.slot_extractor`.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from travel_desk.config import REPO_ROOT, Settings
from travel_desk.llm.dashscope import ModelUnavailable

INVITATION = REPO_ROOT / "tests" / "fixtures" / "invitations" / "shanghai_supplier_conference.png"
S01 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
       "purpose": "供应商大会", "declared_budget_fen": 400000}
S02 = {"origin": "北京", "destination": "上海", "start_date": "2026-10-25", "end_date": "2026-10-26",
       "purpose": "客户拜访", "declared_budget_fen": 250000}

ZHANG, LISI, ZHAO, WANG = "u-zhangsan", "u-lisi", "u-zhaoliu", "u-wangwu"


def h(user: str | None = None, key: str | None = None) -> dict[str, str]:
    headers = {}
    if user:
        headers["X-Demo-User"] = user
    if key:
        headers["Idempotency-Key"] = key
    return headers


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse frames strictly: every frame must carry id, event and a JSON data envelope that agrees."""
    frames = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        fields: dict[str, str] = {}
        for line in lines:
            name, _, value = line.partition(":")
            fields[name] = value[1:] if value.startswith(" ") else value
        assert set(fields) == {"id", "event", "data"}, fields
        event = json.loads(fields["data"])
        assert event["type"] == fields["event"]
        assert event["event_id"] == fields["id"]
        frames.append(event)
    return frames


def stream(client: TestClient, path: str, body: dict[str, Any], *, user: str, key: str | None = None):
    response = client.post(path, json=body, headers=h(user, key))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    terminals = [e for e in events if e["type"] in ("done", "error")]
    assert len(terminals) == 1 and events[-1] is terminals[0], [e["type"] for e in events]
    trace_ids = {e["trace_id"] for e in events}
    assert trace_ids == {response.headers["X-Trace-Id"]}
    return events


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def create(client: TestClient, user: str = ZHANG, text: str | None = None) -> dict[str, Any]:
    response = client.post("/api/trips", json={"text": text} if text else {}, headers=h(user))
    assert response.status_code == 201, response.text
    return response.json()


def trip_with(client: TestClient, slots: dict[str, Any], user: str = ZHANG) -> str:
    trip_id = create(client, user)["trip"]["id"]
    response = client.patch(f"/api/trips/{trip_id}/slots", json={"expected_revision": 1, "slots": slots},
                            headers=h(user))
    assert response.status_code == 200, response.text
    return trip_id


def get_trip(client: TestClient, trip_id: str, user: str = ZHANG) -> dict[str, Any]:
    response = client.get(f"/api/trips/{trip_id}", headers=h(user))
    assert response.status_code == 200, response.text
    return response.json()


def audit_events(client: TestClient, trip_id: str, event_type: str | None = None) -> list[dict[str, Any]]:
    response = client.get(f"/api/trips/{trip_id}/audit", headers=h(ZHANG))
    assert response.status_code == 200
    events = response.json()["events"]
    return [e for e in events if event_type is None or e["type"] == event_type]


def error_code(response) -> str:
    return response.json()["error"]["code"]


def pending_s01(client: TestClient) -> str:
    trip_id = trip_with(client, S01)
    events = stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG,
                    key="submit-1")
    assert events[-1]["payload"]["status"] == "PENDING_APPROVAL"
    return trip_id


def message(client: TestClient, trip_id: str, text: str, revision: int = 1, key: str | None = None):
    return stream(client, f"/api/trips/{trip_id}/messages", {"text": text, "expected_revision": revision},
                  user=ZHANG, key=key)


def slots_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    (event,) = [e for e in events if e["type"] == "slots"]
    return event["payload"]


def token_text(events: list[dict[str, Any]]) -> str:
    return "".join(e["payload"]["text"] for e in events if e["type"] == "token")


# ------------------------------------------------------------------ AC-002 conversation


def test_ac002_s05_asks_only_for_missing_fields_then_updates_without_repeating(client: TestClient):
    trip_id = create(client)["trip"]["id"]

    first = message(client, trip_id, "下周去杭州")
    assert types(first) == ["status", "slots", "token", "done"]
    assert first[0]["payload"]["stage"] == "extracting"
    payload = slots_event(first)
    assert payload["slots"]["destination"] == "杭州"
    # "下周" is vague: no date is invented, origin is not taken from home_city (S-05).
    assert payload["missing_fields"] == ["origin", "start_date", "end_date", "purpose", "declared_budget_fen"]
    assert payload["generator"] == "fake" and payload["source"] == "model"
    ask = token_text(first)
    for label in ("出发城市", "出发日期", "返回日期", "出差事由", "申报预算"):
        assert label in ask
    assert "目的城市" not in ask.split("还需要")[1]

    second = message(client, trip_id, "从北京出发，10月28日到10月30日，参加供应商大会，预算四千元")
    payload = slots_event(second)
    assert payload["missing_fields"] == [] and payload["invalid_fields"] == []
    assert payload["slots"]["declared_budget_fen"] == 400000
    ask = token_text(second)
    assert "还需要" not in ask and "信息已齐全" in ask

    third = message(client, trip_id, "预算改成五千")
    payload = slots_event(third)
    assert payload["changes"] == [{"field": "declared_budget_fen", "before": 400000, "after": 500000}]
    ask = token_text(third)
    assert "还需要" not in ask and "5,000.00 元" in ask

    trip = get_trip(client, trip_id)
    assert trip["status"] == "COLLECTING" and trip["revision"] == 1
    assert trip["slots"]["declared_budget_fen"] == 500000
    changes = audit_events(client, trip_id, "slots_changed")
    assert [c["payload"]["origin"] for c in changes] == ["message"] * 3
    assert changes[-1]["payload"]["changes"] == [
        {"field": "declared_budget_fen", "before": 400000, "after": 500000}]
    asked = [e["payload"]["missing_fields"] for e in audit_events(client, trip_id, "message_processed")]
    assert asked == [["origin", "start_date", "end_date", "purpose", "declared_budget_fen"], [], []]


def test_create_with_text_runs_the_first_turn(client: TestClient):
    body = create(client, text="从北京去上海，预算四千元")
    assert body["trip"]["slots"]["origin"] == "北京" and body["trip"]["slots"]["destination"] == "上海"
    assistant = body["assistant"]
    assert assistant["text_generator"] == "template"
    assert assistant["missing_fields"] == ["start_date", "end_date", "purpose"]
    assert "出发日期" in assistant["text"]


def test_invalid_extracted_value_is_not_written_and_is_asked_again(client: TestClient):
    trip_id = create(client)["trip"]["id"]
    events = message(client, trip_id, "从北京去上海，2026-02-30 出发")
    payload = slots_event(events)
    assert payload["slots"]["start_date"] is None
    assert [(f["field"], f["value"]) for f in payload["invalid_fields"]] == [("start_date", "2026-02-30")]
    assert "无法识别" in token_text(events)


def test_message_idempotency_replays_without_a_second_write(client: TestClient):
    trip_id = create(client)["trip"]["id"]
    message(client, trip_id, "去杭州", key="m-1")
    replay = message(client, trip_id, "去杭州", key="m-1")
    assert types(replay) == ["trip_status", "done"] and replay[0]["payload"]["replayed"] is True
    assert len(audit_events(client, trip_id, "message_processed")) == 1
    reused = client.post(f"/api/trips/{trip_id}/messages", json={"text": "去成都", "expected_revision": 1},
                         headers=h(ZHANG, "m-1"))
    assert reused.status_code == 422 and error_code(reused) == "IDEMPOTENCY_KEY_REUSED"


def test_message_preflight_errors_are_plain_json(client: TestClient):
    trip_id = create(client)["trip"]["id"]
    url = f"/api/trips/{trip_id}/messages"
    blank = client.post(url, json={"text": "  ", "expected_revision": 1}, headers=h(ZHANG))
    assert blank.status_code == 422 and error_code(blank) == "VALIDATION_ERROR"
    stale = client.post(url, json={"text": "去杭州", "expected_revision": 2}, headers=h(ZHANG))
    assert stale.status_code == 409 and error_code(stale) == "STALE_REVISION"
    assert stale.json()["error"]["details"]["current_revision"] == 1
    other = client.post(url, json={"text": "去杭州", "expected_revision": 1}, headers=h(LISI))
    assert other.status_code == 403
    hidden = client.post(url, json={"text": "去杭州", "expected_revision": 1}, headers=h(ZHAO))
    assert hidden.status_code == 404


def test_message_model_failure_ends_the_stream_with_one_error_and_no_write(client: TestClient):
    class Down:
        name = "down"

        async def extract(self, text, today):
            raise ModelUnavailable("模型服务暂时不可用", retryable=True, error_type="APITimeoutError")

    client.app.state.slot_extractor = Down()
    trip_id = create(client)["trip"]["id"]
    events = message(client, trip_id, "去杭州")
    assert types(events) == ["status", "error"]
    error = events[-1]["payload"]
    assert error["code"] == "MODEL_UNAVAILABLE" and error["retryable"] is True
    assert error["details"] == {"error_type": "APITimeoutError"}
    assert get_trip(client, trip_id)["slots"]["destination"] is None
    assert audit_events(client, trip_id, "error")[-1]["payload"]["stage"] == "slot_extraction"

    created = create(client, text="去杭州")
    assert created["assistant"]["error"]["code"] == "MODEL_UNAVAILABLE"
    assert created["trip"]["status"] == "COLLECTING"


def test_message_in_policy_blocked_returns_to_collecting(client: TestClient):
    trip_id = trip_with(client, {**S02, "start_date": "2026-10-19", "end_date": "2026-10-20"})
    events = stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG, key="s-1")
    assert events[-1]["payload"]["status"] == "POLICY_BLOCKED"
    events = message(client, trip_id, "改成10月25日到10月26日")
    assert types(events) == ["status", "slots", "trip_status", "token", "done"]
    assert events[2]["payload"]["status"] == "COLLECTING"
    assert events[2]["payload"]["previous_status"] == "POLICY_BLOCKED"
    assert get_trip(client, trip_id)["revision"] == 1
    assert len(audit_events(client, trip_id, "blocked_cleared")) == 1


def test_messages_are_rejected_after_submit(client: TestClient):
    trip_id = pending_s01(client)
    response = client.post(f"/api/trips/{trip_id}/messages", json={"text": "去杭州", "expected_revision": 1},
                           headers=h(ZHANG))
    assert response.status_code == 409 and error_code(response) == "INVALID_STATE"


# ------------------------------------------------------------------ AC-003 invitation image


def upload(client: TestClient, data: bytes, name: str = "invite.png", user: str = ZHANG,
           content_type: str = "image/png"):
    return client.post("/api/uploads", files={"file": (name, data, content_type)}, headers=h(user))


def test_ac003_image_extraction_is_a_proposal_until_confirmed(client: TestClient):
    trip_id = create(client)["trip"]["id"]
    data = INVITATION.read_bytes()
    uploaded = upload(client, data)
    assert uploaded.status_code == 201, uploaded.text
    info = uploaded.json()
    assert info["content_type"] == "image/png" and info["size_bytes"] == len(data)

    extracted = client.post(f"/api/trips/{trip_id}/image-extractions", json={"upload_id": info["id"]},
                            headers=h(ZHANG))
    assert extracted.status_code == 201, extracted.text
    proposal = extracted.json()
    assert proposal["status"] == "pending" and proposal["source"] == "model"
    assert proposal["generator"] == "recorded:model"
    assert proposal["fields"]["destination"] == "上海"
    assert proposal["fields"]["start_date"] == "2026-10-28"

    untouched = get_trip(client, trip_id)
    assert untouched["slots"]["destination"] is None and untouched["slots"]["purpose"] is None
    assert audit_events(client, trip_id, "slots_changed") == []

    edited = {**proposal["fields"], "purpose": "供应商大会"}
    confirmed = client.post(f"/api/trips/{trip_id}/image-extractions/{proposal['id']}/confirm",
                            json={"expected_revision": 1, "fields": edited}, headers=h(ZHANG))
    assert confirmed.status_code == 200, confirmed.text
    slots = confirmed.json()["slots"]
    assert slots["destination"] == "上海" and slots["purpose"] == "供应商大会"
    assert slots["end_date"] == "2026-10-30" and slots["origin"] is None
    (confirm_audit,) = audit_events(client, trip_id, "image_confirmed")
    assert confirm_audit["payload"]["edited_fields"] == ["purpose"]
    (change,) = audit_events(client, trip_id, "slots_changed")
    assert change["payload"]["origin"] == "image"
    assert change["payload"]["extraction_id"] == proposal["id"]

    again = client.post(f"/api/trips/{trip_id}/image-extractions/{proposal['id']}/confirm",
                        json={"expected_revision": 1}, headers=h(ZHANG))
    assert again.status_code == 409 and error_code(again) == "EXTRACTION_NOT_PENDING"


def test_upload_type_and_size_are_checked_by_content(client: TestClient, settings: Settings):
    text_as_png = upload(client, b"just text, not an image", name="fake.png")
    assert text_as_png.status_code == 415 and error_code(text_as_png) == "UNSUPPORTED_MEDIA_TYPE"
    too_big = upload(client, b"\x89PNG\r\n\x1a\n" + b"\0" * settings.max_upload_bytes)
    assert too_big.status_code == 413 and error_code(too_big) == "PAYLOAD_TOO_LARGE"
    jpeg_named_txt = upload(client, b"\xff\xd8\xff\xe0" + b"\0" * 64, name="x.txt", content_type="text/plain")
    assert jpeg_named_txt.status_code == 201 and jpeg_named_txt.json()["content_type"] == "image/jpeg"
    admin = upload(client, INVITATION.read_bytes(), user=WANG)
    assert admin.status_code == 403
    assert list(settings.upload_dir.iterdir()) and len(list(settings.upload_dir.iterdir())) == 1


def test_extraction_needs_own_upload_and_collecting_state(client: TestClient):
    upload_id = upload(client, INVITATION.read_bytes(), user=LISI).json()["id"]
    trip_id = create(client)["trip"]["id"]
    foreign = client.post(f"/api/trips/{trip_id}/image-extractions", json={"upload_id": upload_id},
                          headers=h(ZHANG))
    assert foreign.status_code == 404 and error_code(foreign) == "UPLOAD_NOT_FOUND"
    missing = client.post(f"/api/trips/{trip_id}/image-extractions", json={}, headers=h(ZHANG))
    assert missing.status_code == 422


# ------------------------------------------------------------------ submit / AC-009 over HTTP


def test_s01_over_http_submit_stream_then_approve_delivers_once(client: TestClient):
    trip_id = trip_with(client, S01)
    events = stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG,
                    key="submit-1")
    assert types(events)[:1] == ["trip_status"] and "plan" in types(events)
    approval_index = types(events).index("approval_required")
    assert events[approval_index + 1]["type"] == "trip_status"
    assert events[approval_index + 1]["payload"]["status"] == "PENDING_APPROVAL"
    assert events[-1]["payload"] == {"status": "PENDING_APPROVAL", "revision": 1}

    replay = stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG,
                    key="submit-1")
    assert types(replay) == ["trip_status", "done"] and replay[0]["payload"]["replayed"] is True

    pending = client.get("/api/approvals?status=pending", headers=h(LISI)).json()["approvals"]
    assert [a["trip_id"] for a in pending] == [trip_id]
    card = pending[0]["card"]
    assert card["budget_delta_fen"] == card["cost_breakdown"]["budget_delta_fen"]
    assert client.get("/api/approvals?status=pending", headers=h(ZHAO)).json()["approvals"] == []

    body = {"revision": 1, "decision": "approve"}
    first = client.post(f"/api/trips/{trip_id}/decision", json=body, headers=h(LISI, "d-1"))
    assert first.status_code == 200, first.text
    assert first.json()["trip"]["status"] == "DELIVERED"
    assert first.json()["approval"]["decision"] == "approve"

    replayed = client.post(f"/api/trips/{trip_id}/decision", json=body, headers=h(LISI, "d-1"))
    assert replayed.status_code == 200 and replayed.json() == first.json()
    reused = client.post(f"/api/trips/{trip_id}/decision", json={**body, "decision": "reject",
                                                                 "comment": "x"}, headers=h(LISI, "d-1"))
    assert reused.status_code == 422 and error_code(reused) == "IDEMPOTENCY_KEY_REUSED"
    other_key = client.post(f"/api/trips/{trip_id}/decision", json=body, headers=h(LISI, "d-2"))
    assert other_key.status_code == 409 and error_code(other_key) == "ALREADY_DECIDED"

    emails = client.get(f"/api/outbox?trip_id={trip_id}", headers=h(WANG)).json()["emails"]
    assert [e["kind"] for e in emails] == ["trip_delivery"]
    itinerary = client.get(f"/api/trips/{trip_id}/itinerary", headers=h(ZHANG))
    assert itinerary.status_code == 200 and itinerary.json()["revision"] == 1
    assert client.get(f"/api/trips/{trip_id}/itinerary?revision=2", headers=h(ZHANG)).status_code == 404


def test_ac009_decision_errors_follow_the_documented_order(client: TestClient):
    trip_id = pending_s01(client)
    url = f"/api/trips/{trip_id}/decision"
    approve = {"revision": 1, "decision": "approve"}
    assert client.post(url, json=approve).status_code == 401
    no_key = client.post(url, json=approve, headers=h(LISI))
    assert no_key.status_code == 400 and error_code(no_key) == "IDEMPOTENCY_KEY_REQUIRED"
    bad = client.post(url, json={"revision": 1, "decision": "maybe"}, headers=h(LISI, "k"))
    assert bad.status_code == 400 and error_code(bad) == "INVALID_DECISION"
    missing = client.post("/api/trips/no-such-trip/decision", json=approve, headers=h(LISI, "k"))
    assert missing.status_code == 404
    for actor, code in ((ZHANG, "FORBIDDEN"), (WANG, "FORBIDDEN"), (ZHAO, "NOT_DIRECT_MANAGER")):
        response = client.post(url, json=approve, headers=h(actor, "k"))
        assert response.status_code == 403 and error_code(response) == code, actor
    stale = client.post(url, json={**approve, "revision": 2}, headers=h(LISI, "k"))
    assert stale.status_code == 409 and error_code(stale) == "STALE_REVISION"
    no_comment = client.post(url, json={"revision": 1, "decision": "reject", "comment": " "},
                             headers=h(LISI, "k"))
    assert no_comment.status_code == 422 and error_code(no_comment) == "COMMENT_REQUIRED"
    assert get_trip(client, trip_id)["status"] == "PENDING_APPROVAL"


def test_submit_preflight_errors(client: TestClient):
    trip_id = create(client)["trip"]["id"]
    url = f"/api/trips/{trip_id}/submit"
    no_key = client.post(url, json={"expected_revision": 1}, headers=h(ZHANG))
    assert no_key.status_code == 400
    incomplete = client.post(url, json={"expected_revision": 1}, headers=h(ZHANG, "s"))
    assert incomplete.status_code == 422 and error_code(incomplete) == "SLOTS_INCOMPLETE"
    assert incomplete.json()["error"]["details"]["missing_fields"][0] == "origin"
    other = client.post(url, json={"expected_revision": 1}, headers=h(LISI, "s"))
    assert other.status_code == 403


def test_delivery_retry_is_admin_only_and_state_checked(client: TestClient):
    trip_id = pending_s01(client)
    url = f"/api/trips/{trip_id}/delivery/retry"
    assert client.post(url, headers=h(LISI)).status_code == 403
    wrong_state = client.post(url, headers=h(WANG))
    assert wrong_state.status_code == 409 and error_code(wrong_state) == "INVALID_STATE"


# ------------------------------------------------------------------ permission matrix


def test_identity_is_required_and_must_be_known(client: TestClient):
    assert error_code(client.get("/api/trips")) == "UNAUTHENTICATED"
    unknown = client.get("/api/trips", headers=h("u-nobody"))
    assert unknown.status_code == 401 and error_code(unknown) == "UNKNOWN_DEMO_USER"
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/users").status_code == 200


def test_trip_visibility_and_ownership(client: TestClient):
    trip_id = trip_with(client, S02)
    assert client.post("/api/trips", json={}, headers=h(WANG)).status_code == 403
    assert client.get(f"/api/trips/{trip_id}", headers=h(LISI)).status_code == 200
    assert client.get(f"/api/trips/{trip_id}", headers=h(WANG)).status_code == 200
    hidden = client.get(f"/api/trips/{trip_id}", headers=h(ZHAO))
    assert hidden.status_code == 404
    assert client.get(f"/api/trips/{trip_id}/audit", headers=h(ZHAO)).status_code == 404
    patch = client.patch(f"/api/trips/{trip_id}/slots", json={"expected_revision": 1, "slots": {"purpose": "x"}},
                         headers=h(LISI))
    assert patch.status_code == 403


def test_trip_list_scopes(client: TestClient):
    mine = trip_with(client, S02)
    own_manager_trip = create(client, LISI)["trip"]["id"]

    def listed(user: str, scope: str | None = None):
        url = "/api/trips" + (f"?scope={scope}" if scope else "")
        return client.get(url, headers=h(user))

    assert [t["id"] for t in listed(ZHANG).json()["trips"]] == [mine]
    assert listed(ZHANG, "reports").status_code == 403
    assert listed(ZHANG, "everything").status_code == 422
    assert [t["id"] for t in listed(LISI).json()["trips"]] == [own_manager_trip]
    reports = listed(LISI, "reports").json()["trips"]
    assert [t["id"] for t in reports] == [mine] and reports[0]["employee_name"] == "张三"
    assert listed(ZHAO, "reports").json()["trips"] == []
    assert {t["id"] for t in listed(WANG).json()["trips"]} == {mine, own_manager_trip}
    assert listed(WANG, "mine").status_code == 403
    summary = reports[0]
    assert {"status", "revision", "origin", "destination", "total_fen", "known_subtotal_fen",
            "policy_version", "updated_at"} <= set(summary)


def test_role_gated_endpoints(client: TestClient):
    assert client.get("/api/approvals", headers=h(ZHANG)).status_code == 403
    assert client.get("/api/approvals", headers=h(WANG)).status_code == 403
    assert client.get("/api/outbox", headers=h(LISI)).status_code == 403
    assert client.get("/api/outbox?kind=spam", headers=h(WANG)).status_code == 422
    assert client.get("/api/demo/faults", headers=h(ZHANG)).status_code == 403
    assert client.get("/api/health/providers", headers=h(LISI)).status_code == 403
    assert client.post("/api/demo/reset", json={"confirmation": "RESET_DEMO_DATA"},
                       headers=h(LISI)).status_code == 403


def test_faults_toggle_and_show_in_provider_health(client: TestClient):
    bad = client.post("/api/demo/faults", json={"fault": "meteor", "enabled": True}, headers=h(WANG))
    assert bad.status_code == 400 and error_code(bad) == "UNKNOWN_FAULT"
    not_bool = client.post("/api/demo/faults", json={"fault": "transport_timeout", "enabled": "yes"},
                           headers=h(WANG))
    assert not_bool.status_code == 422
    on = client.post("/api/demo/faults", json={"fault": "transport_timeout", "enabled": True}, headers=h(WANG))
    assert on.status_code == 200
    health = client.get("/api/health/providers", headers=h(WANG)).json()
    status = {p["name"]: p["status"] for p in health["providers"]}
    assert status["transport"] == "fault_injected" and status["weather"] == "ok"
    assert health["llm_mode"] == "fake" and health["llm"]["api_key_configured"] is None
    assert "sk-" not in json.dumps(health)


def _submit_s02_under_fault(client: TestClient, fault: str, key: str) -> dict[str, Any]:
    """Inject one fault, run the S-02 input over HTTP, return the resulting trip (fault cleared)."""
    assert client.post("/api/demo/faults", json={"fault": fault, "enabled": True},
                       headers=h(WANG)).status_code == 200
    trip_id = trip_with(client, S02)
    stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG, key=key)
    trip = get_trip(client, trip_id)
    trip["provider_calls"] = {e["payload"]["provider"]: e["payload"]["status"]
                              for e in audit_events(client, trip_id, "provider_call")}
    assert client.post("/api/demo/faults", json={"fault": fault, "enabled": False},
                       headers=h(WANG)).status_code == 200
    return trip


def test_ac013_fault_injection_degrades_the_plan_over_http(client: TestClient):
    """AC-013 / §6.6: each fault degrades exactly as specified, never inventing data.

    The planning layer is covered by tests/test_planning.py; this is the API-result
    half (the same assertions scripts/ac013_faults.py makes against a real subprocess).
    """
    reasons = lambda t: [r["code"] for r in t["approval_reasons"]]  # noqa: E731

    # ---- transport timeout → transport manual, incomplete, must approve ---------
    t = _submit_s02_under_fault(client, "transport_timeout", "ac013-transport")
    plan, cost = t["plan"], t["cost_breakdown"]
    assert plan["transport"]["status"] == "manual_required" and plan["incomplete"] is True
    assert plan["missing"] == ["transport"]
    assert (plan["transport"]["outbound"], plan["transport"]["return"]) == (None, None)  # no fabricated id
    assert (cost["total_fen"], cost["known_subtotal_fen"], cost["transport_fen"]) == (None, 60000, None)
    assert cost["missing_components"] == ["transport"]
    assert "PLAN_INCOMPLETE" in reasons(t) and t["status"] == "PENDING_APPROVAL"
    assert plan["hotel"]["status"] == "selected" and plan["weather"]["status"] == "ok"
    assert t["provider_calls"]["transport"] == "timeout" and t["provider_calls"]["weather"] == "ok"

    # ---- no protocol hotel (Shanghai has no backup) → hotel manual, must approve -
    hhot = _submit_s02_under_fault(client, "no_protocol_hotel", "ac013-hotel")
    plan, cost = hhot["plan"], hhot["cost_breakdown"]
    assert plan["hotel"]["status"] == "manual_required" and plan["hotel"]["candidate"] is None
    assert plan["incomplete"] is True and plan["missing"] == ["hotel"]
    assert "协议酒店库不可用" in plan["hotel"]["note"]
    assert (cost["total_fen"], cost["known_subtotal_fen"], cost["hotel_fen"]) == (None, 150000, None)
    assert cost["missing_components"] == ["hotel"]
    assert "PLAN_INCOMPLETE" in reasons(hhot) and hhot["status"] == "PENDING_APPROVAL"
    assert hhot["provider_calls"] == {"transport": "ok", "hotel_contract": "unavailable",
                                      "hotel_backup": "ok", "weather": "ok"}
    assert plan["transport"]["status"] == "selected" and plan["weather"]["status"] == "ok"

    # ---- weather unavailable → omitted and noted, planning NOT blocked -----------
    w = _submit_s02_under_fault(client, "weather_unavailable", "ac013-weather")
    plan, cost = w["plan"], w["cost_breakdown"]
    assert plan["weather"]["status"] == "unavailable" and plan["weather"]["days"] == []
    assert "已省略" in plan["weather"]["note"]
    assert plan["incomplete"] is False and plan["missing"] == []
    assert plan["transport"]["status"] == "selected" and plan["hotel"]["status"] == "selected"
    assert cost["total_fen"] == 190000 and reasons(w) == [] and w["violations"] == []
    assert w["status"] == "DELIVERED"  # S-02 auto-approves despite the missing weather
    assert w["provider_calls"]["weather"] == "unavailable"


def test_demo_reset_requires_confirmation_and_clears_trips(client: TestClient):
    trip_id = pending_s01(client)
    missing = client.post("/api/demo/reset", json={}, headers=h(WANG))
    assert missing.status_code == 422 and error_code(missing) == "CONFIRMATION_REQUIRED"
    done = client.post("/api/demo/reset", json={"confirmation": "RESET_DEMO_DATA"}, headers=h(WANG))
    assert done.status_code == 200 and done.json() == {"status": "reset"}
    assert client.get(f"/api/trips/{trip_id}", headers=h(ZHANG)).status_code == 404
    assert client.get("/api/trips", headers=h(WANG)).json()["trips"] == []


def _snapshot(settings: Settings) -> dict[str, Any]:
    """Read-only DB snapshot for AC-016: dynamic counts, seed content, checkpoint threads."""
    app = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    app.row_factory = sqlite3.Row
    try:
        dynamic = ["trips", "approvals", "outbox_emails", "idempotency_keys", "itineraries",
                   "uploads", "image_extractions"]
        counts = {t: app.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in dynamic}
        counts["audit_events"] = app.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        audit_types = [r["type"] for r in app.execute("SELECT type FROM audit_events ORDER BY seq")]
        versions = {r["version"]: r["status"] for r in app.execute(
            "SELECT version, status FROM policy_versions")}
        rules = {r["version"]: r["n"] for r in app.execute(
            "SELECT version, COUNT(*) AS n FROM policy_rules GROUP BY version")}
        employees = app.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        with_prefs = [r["id"] for r in app.execute(
            "SELECT id FROM employees WHERE preferences_json NOT IN ('{}', '')")]
        faults = {r["key"]: bool(r["enabled"]) for r in app.execute("SELECT key, enabled FROM faults")}
    finally:
        app.close()
    threads: list[str] = []
    if settings.checkpoint_db_path.exists():
        cp = sqlite3.connect(f"file:{settings.checkpoint_db_path}?mode=ro", uri=True)
        cp.row_factory = sqlite3.Row
        try:
            tables = {r["name"] for r in cp.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "checkpoints" in tables:
                threads = sorted(r[0] for r in cp.execute("SELECT DISTINCT thread_id FROM checkpoints"))
        finally:
            cp.close()
    return {"counts": counts, "audit_types": audit_types, "versions": versions, "rules": rules,
            "employees": employees, "with_prefs": with_prefs, "faults": faults, "threads": threads}


def test_ac016_demo_reset_clears_all_dynamic_data_and_restores_seed(client: TestClient,
                                                                    settings: Settings):
    """AC-016 / REQ-025: create every kind of dynamic data, reset, verify clear + seed restore."""
    # Trip A → DELIVERED (approval + itinerary + Outbox email); Trip B → PENDING_APPROVAL (live thread).
    trip_a = trip_with(client, S01)
    stream(client, f"/api/trips/{trip_a}/submit", {"expected_revision": 1}, user=ZHANG, key="a-submit")
    approved = client.post(f"/api/trips/{trip_a}/decision", json={"revision": 1, "decision": "approve"},
                           headers=h(LISI, "a-decide"))
    assert approved.status_code == 200 and approved.json()["trip"]["status"] == "DELIVERED"
    trip_b = trip_with(client, S01)
    stream(client, f"/api/trips/{trip_b}/submit", {"expected_revision": 1}, user=ZHANG, key="b-submit")
    # A preference and an injected fault.
    assert client.put(f"/api/users/{ZHANG}/preferences",
                      json={"avoid_early_departure": True, "notes": "靠窗"},
                      headers=h(ZHANG)).status_code == 200
    assert client.post("/api/demo/faults", json={"fault": "transport_timeout", "enabled": True},
                       headers=h(WANG)).status_code == 200

    before = _snapshot(settings)
    assert before["counts"]["trips"] == 2 and before["counts"]["approvals"] >= 2
    assert before["counts"]["outbox_emails"] >= 1 and before["counts"]["itineraries"] >= 1
    assert before["counts"]["idempotency_keys"] >= 1 and before["counts"]["audit_events"] > 1
    assert before["with_prefs"] == [ZHANG] and before["faults"]["transport_timeout"] is True
    assert len(before["threads"]) >= 2
    assert before["versions"] == {"v1.0": "archived", "v1.2": "published"} and before["employees"] == 4

    # Authorisation and confirmation gates; failed attempts must not reset anything.
    assert client.post("/api/demo/reset", json={"confirmation": "RESET_DEMO_DATA"},
                       headers=h(ZHANG)).status_code == 403
    assert client.post("/api/demo/reset", json={"confirmation": "RESET_DEMO_DATA"},
                       headers=h(LISI)).status_code == 403
    wrong = client.post("/api/demo/reset", json={"confirmation": "RESET"}, headers=h(WANG))
    assert wrong.status_code == 422 and error_code(wrong) == "CONFIRMATION_REQUIRED"
    assert _snapshot(settings)["counts"]["trips"] == 2

    assert client.post("/api/demo/reset", json={"confirmation": "RESET_DEMO_DATA"},
                       headers=h(WANG)).status_code == 200

    after = _snapshot(settings)
    # Every dynamic table is empty; faults off; preferences cleared; checkpoint threads deleted.
    assert all(after["counts"][t] == 0 for t in
               ["trips", "approvals", "outbox_emails", "idempotency_keys", "itineraries",
                "uploads", "image_extractions"]), after["counts"]
    assert after["faults"]["transport_timeout"] is False and after["with_prefs"] == []
    assert after["threads"] == []
    # Seed restored: 4 employees, v1.0 archived + v1.2 published, same rule counts per version.
    assert after["employees"] == 4 and after["versions"] == {"v1.0": "archived", "v1.2": "published"}
    assert after["rules"] == before["rules"]
    # Append-only audit: only the sanctioned demo_reset event survives (HC-11 exception).
    assert after["counts"]["audit_events"] == 1 and after["audit_types"] == ["demo_reset"]
    # The API surface agrees, and admin reads add no audit events.
    assert client.get("/api/trips", headers=h(WANG)).json()["trips"] == []
    assert client.get(f"/api/trips/{trip_a}", headers=h(ZHANG)).status_code == 404
    assert client.get("/api/approvals?status=pending", headers=h(LISI)).json()["approvals"] == []
    assert client.get("/api/outbox", headers=h(WANG)).json()["emails"] == []
    assert _snapshot(settings)["counts"]["audit_events"] == 1


def test_error_envelope_and_trace_id(client: TestClient):
    response = client.get("/api/trips/nope", headers={**h(ZHANG), "X-Trace-Id": "trace-abc-1"})
    assert response.status_code == 404
    assert response.headers["X-Trace-Id"] == "trace-abc-1"
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details", "trace_id"} and error["trace_id"] == "trace-abc-1"
    invalid = client.post("/api/trips", content=b"{not json", headers={**h(ZHANG),
                                                                       "Content-Type": "application/json"})
    assert invalid.status_code == 400 and error_code(invalid) == "INVALID_JSON"


# REQ-020 categories → the audit event types that satisfy each (AC-012). Mirrors
# scripts/ac012_audit.py so the exported-evidence matrix is also a durable test.
REQ020_CATEGORIES: dict[str, set[str]] = {
    "槽位变化": {"slots_changed", "trip_created", "message_processed", "image_confirmed"},
    "检索条款与引用": {"rules_evaluated", "policy_bound", "policy_question"},
    "规则结果": {"rules_evaluated", "validation_blocked", "blocked_cleared", "revalidated"},
    "Provider 调用摘要与来源": {"provider_call", "candidates_selected"},
    "方案版本": {"plan_created"},
    "审批决定": {"approval_requested", "decision_recorded", "auto_approved", "approval_superseded"},
    "通知与交付": {"notification_written", "delivery_written", "delivery_failed"},
}


def test_ac012_audit_timeline_covers_req020_categories(client: TestClient):
    """AC-012 / REQ-020: a full S-01 lifecycle records every audit category but errors."""
    trip_id = trip_with(client, S01)
    stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG, key="ac012-submit")
    approved = client.post(f"/api/trips/{trip_id}/decision", json={"revision": 1, "decision": "approve"},
                           headers=h(LISI, "ac012-decide"))
    assert approved.status_code == 200 and approved.json()["trip"]["status"] == "DELIVERED"

    events = audit_events(client, trip_id)
    seen = {e["type"] for e in events}
    for category, wanted in REQ020_CATEGORIES.items():
        assert seen & wanted, f"audit timeline missing category {category}: have {sorted(seen)}"

    # The 错误 category: a recorded Provider fault. Inject the transport timeout and run S-02;
    # the audit must capture the degraded provider_call (status=timeout), not silently drop it.
    assert client.post("/api/demo/faults", json={"fault": "transport_timeout", "enabled": True},
                       headers=h(WANG)).status_code == 200
    fault_trip = trip_with(client, S02)
    stream(client, f"/api/trips/{fault_trip}/submit", {"expected_revision": 1}, user=ZHANG, key="ac012-f")
    calls = [e["payload"] for e in audit_events(client, fault_trip, "provider_call")]
    timeouts = [c for c in calls if c.get("provider") == "transport" and c.get("status") == "timeout"]
    assert timeouts, f"transport timeout not recorded in audit: {calls}"
    client.post("/api/demo/faults", json={"fault": "transport_timeout", "enabled": False}, headers=h(WANG))

    # The dedicated `error` and `delivery_failed` audit event types are proven elsewhere:
    # test_message_model_failure_ends_the_stream_with_one_error_and_no_write (error / slot_extraction)
    # and test_workflow.py's delivery-retry test (delivery_failed). Together with the recorded
    # Provider fault above, REQ-020's "错误" category is fully covered.
