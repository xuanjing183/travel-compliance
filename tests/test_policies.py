"""Policy upload, extraction, review and publication (#19–#23; AC-005, S-12/AC-024 via the API).

AC-005 uses independent test version numbers (v9.x-test) so v1.0/v1.2/v1.3 stay untouched;
the extraction output is the recorded fixture of §7.1.1 (`source: hand_written`).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from test_api import (
    LISI,
    S01,
    WANG,
    ZHANG,
    audit_events,
    error_code,
    get_trip,
    h,
    pending_s01,
    stream,
    trip_with,
)
from test_preferences_revalidate import V13_HOTEL_QUOTE, approvals, revalidate

from travel_desk.config import Settings
from travel_desk.db.database import read_session
from travel_desk.domain.policy import CLOSED_RULE_TYPES

MD = "tests/fixtures/policies/v1.2.md"
CLOSED_IDS = ["RULE-CITY-001", "RULE-HOTEL-001", "RULE-HOTEL-002", "RULE-MEAL-001", "RULE-CLASS-001",
              "RULE-ADVANCE-001", "RULE-AUTO-001", "RULE-DURATION-001"]
INVALID_ID, UNSUPPORTED_ID = "RULE-DURATION-002", "RULE-OTHER-001"


def upload(client: TestClient, settings: Settings, version: str, document: str = "v1.2.md", *,
           user: str = WANG, effective_date: str = "2026-11-01", data: bytes | None = None,
           name: str | None = None):
    content = data if data is not None else (settings.policies_dir / document).read_bytes()
    return client.post("/api/policies", headers=h(user),
                       data={"version": version, "effective_date": effective_date},
                       files={"file": (name or document, content, "application/octet-stream")})


def extract(client: TestClient, version: str, body: dict | None = None, user: str = WANG):
    return client.post(f"/api/policies/{version}/extract", json=body or {}, headers=h(user))


def patch(client: TestClient, version: str, rule_id: str, body: dict[str, Any], user: str = WANG):
    return client.patch(f"/api/policies/{version}/rules/{rule_id}", json=body, headers=h(user))


def publish(client: TestClient, version: str, key: str | None = "pub-1", user: str = WANG):
    return client.post(f"/api/policies/{version}/publish", headers=h(user, key))


def policy_events(settings: Settings, version: str, event_type: str | None = None) -> list[dict[str, Any]]:
    with read_session(settings.db_path) as conn:
        rows = conn.execute("SELECT type, actor, payload_json, refs_json FROM audit_events"
                            " WHERE trip_id IS NULL ORDER BY seq").fetchall()
    events = [{"type": r["type"], "actor": r["actor"], "payload": json.loads(r["payload_json"]),
               "refs": json.loads(r["refs_json"])} for r in rows]
    return [e for e in events if e["payload"].get("version") == version
            and (event_type is None or e["type"] == event_type)]


def versions(settings: Settings) -> dict[str, str]:
    with read_session(settings.db_path) as conn:
        return {r["version"]: r["status"] for r in conn.execute("SELECT version, status FROM policy_versions")}


def review_all(client: TestClient, version: str) -> None:
    """Confirm the 8 closed-type candidates, reject the invalid quote; the unsupported one is already marked."""
    for rule_id in CLOSED_IDS:
        assert patch(client, version, rule_id, {"status": "confirmed"}).status_code == 200, rule_id
    assert patch(client, version, INVALID_ID, {"status": "rejected"}).status_code == 200


# ------------------------------------------------------------------ AC-005


def test_ac005_upload_markdown_and_text_pdf_create_drafts_scanned_pdf_is_415(client: TestClient,
                                                                             settings: Settings):
    md = upload(client, settings, "v9.1-test")
    assert md.status_code == 201, md.text
    body = md.json()
    assert (body["version"], body["status"], body["effective_date"]) == ("v9.1-test", "draft", "2026-11-01")
    assert body["document"]["kind"] == "markdown" and body["rules"] == []
    pdf = upload(client, settings, "v9.2-test", "v1.2-text.pdf")
    assert pdf.status_code == 201 and pdf.json()["document"]["kind"] == "pdf"

    scanned = upload(client, settings, "v9.3-test", "scanned.pdf")
    assert scanned.status_code == 415 and error_code(scanned) == "SCANNED_PDF"
    text_file = upload(client, settings, "v9.3-test", data="出租车费用实报实销".encode(), name="policy.txt")
    assert text_file.status_code == 415 and error_code(text_file) == "UNSUPPORTED_MEDIA_TYPE"
    broken = upload(client, settings, "v9.3-test", data=b"%PDF-1.7\nnot really a pdf", name="x.pdf")
    assert broken.status_code == 415 and error_code(broken) == "UNSUPPORTED_MEDIA_TYPE"
    too_big = upload(client, settings, "v9.3-test", data=b"%PDF-" + b"\0" * settings.max_upload_bytes,
                     name="big.pdf")
    assert too_big.status_code == 413 and error_code(too_big) == "PAYLOAD_TOO_LARGE"

    assert upload(client, settings, "v9.1-test").status_code == 409
    assert error_code(upload(client, settings, "v1.2")) == "VERSION_EXISTS"
    for version, day in (("1.3", "2026-11-01"), ("v9.4-test", "2026-13-01"), ("v9.4-test", "20261101")):
        bad = upload(client, settings, version, effective_date=day)
        assert bad.status_code == 422 and error_code(bad) == "VALIDATION_ERROR", (version, day)
    employee = upload(client, settings, "v9.4-test", user=ZHANG)
    assert employee.status_code == 403
    assert client.post("/api/policies", data={"version": "v9.4-test"}).status_code == 401

    assert versions(settings) == {"v1.0": "archived", "v1.2": "published", "v9.1-test": "draft",
                                  "v9.2-test": "draft"}
    (event,) = policy_events(settings, "v9.2-test", "policy_uploaded")
    assert event["actor"] == WANG and event["payload"]["kind"] == "pdf" and event["payload"]["pages"] == 2
    stored = list((settings.upload_dir / "policies").iterdir())
    assert len(stored) == 2  # rejected uploads leave no file behind


def test_ac005_extraction_marks_invalid_quotes_and_unsupported_types(client: TestClient, settings: Settings):
    upload(client, settings, "v9.1-test")
    upload(client, settings, "v9.2-test", "v1.2-text.pdf")
    first = extract(client, "v9.1-test")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["reused"] is False and body["generator"] == "recorded:hand_written"
    candidates = {c["id"]: c for c in body["candidates"]}
    assert list(candidates) == CLOSED_IDS + [INVALID_ID, UNSUPPORTED_ID]
    for rule_id in CLOSED_IDS:
        rule = candidates[rule_id]
        assert (rule["status"], rule["quote_status"], rule["problems"]) == ("candidate", "valid", []), rule_id
        assert rule["locator"].startswith("line:") and rule["supported"] is True
    hotel = candidates["RULE-HOTEL-001"]
    assert hotel["value"] == {"caps_fen": {"A": 50000, "B": 40000, "C": 30000}} and hotel["locator"] == "line:13"
    invalid = candidates[INVALID_ID]
    assert (invalid["status"], invalid["quote_status"], invalid["locator"]) == ("candidate", "invalid_quote", None)
    assert [p["code"] for p in invalid["problems"]] == ["INVALID_QUOTE"]
    unsupported = candidates[UNSUPPORTED_ID]
    assert (unsupported["type"], unsupported["status"], unsupported["supported"]) == (
        "taxi_expense", "unsupported", False)
    assert unsupported["quote_status"] == "valid" and unsupported["clause"] == "POL-8"

    pdf = extract(client, "v9.2-test").json()["candidates"]
    assert {c["locator"] for c in pdf if c["quote_status"] == "valid"} == {"page:1", "page:2"}

    # Idempotent without overwrite; overwrite=true replaces the candidates and is audited.
    patch(client, "v9.1-test", "RULE-CITY-001", {"status": "confirmed"})
    again = extract(client, "v9.1-test").json()
    assert again["reused"] is True and again["candidates"][0]["status"] == "confirmed"
    assert len(policy_events(settings, "v9.1-test", "policy_extracted")) == 1
    replaced = extract(client, "v9.1-test", {"overwrite": True}).json()
    assert replaced["reused"] is False and replaced["candidates"][0]["status"] == "candidate"
    assert {"id": "RULE-CITY-001", "type": "city_tier", "status": "confirmed",
            "quote_status": "valid"} in replaced["replaced"]
    events = policy_events(settings, "v9.1-test", "policy_extracted")
    assert [e["payload"]["overwrite"] for e in events] == [False, True]
    assert events[1]["payload"]["counts"] == {"candidate": 9, "confirmed": 0, "rejected": 0, "unsupported": 1,
                                              "invalid_quote": 1}

    assert extract(client, "v1.2").status_code == 409 and error_code(extract(client, "v1.2")) == "POLICY_NOT_DRAFT"
    assert extract(client, "v9.9-test").status_code == 404
    assert extract(client, "v9.1-test", user=ZHANG).status_code == 404  # drafts are invisible to non-admins
    assert extract(client, "v9.1-test", {"overwrite": "yes"}).status_code == 422


def test_extraction_of_an_unrecorded_document_is_unavailable_not_guessed(client: TestClient,
                                                                         settings: Settings):
    other = "# 另一份制度\n\n出差住宿每晚不超过 800 元。\n".encode()
    assert upload(client, settings, "v9.5-test", data=other, name="other.md").status_code == 201
    response = extract(client, "v9.5-test")
    assert response.status_code == 503 and error_code(response) == "MODEL_UNAVAILABLE"
    assert response.json()["error"]["details"]["retryable"] is False
    (event,) = policy_events(settings, "v9.5-test", "error")
    assert event["payload"]["stage"] == "policy_extraction"
    assert client.get("/api/policies/v9.5-test", headers=h(WANG)).json()["rules"] == []


def test_ac005_review_rules_invalid_quote_unsupported_and_modification_reason(client: TestClient,
                                                                              settings: Settings):
    upload(client, settings, "v9.1-test")
    extract(client, "v9.1-test")
    url_ok = patch(client, "v9.1-test", "RULE-CITY-001", {"status": "confirmed"})
    assert url_ok.status_code == 200 and url_ok.json()["confirmed_by"] == WANG

    invalid = patch(client, "v9.1-test", INVALID_ID, {"status": "confirmed"})
    assert invalid.status_code == 422 and error_code(invalid) == "QUOTE_INVALID"
    unsupported = patch(client, "v9.1-test", UNSUPPORTED_ID, {"status": "confirmed"})
    assert unsupported.status_code == 422 and error_code(unsupported) == "UNSUPPORTED_TYPE"

    lower = {"caps_fen": {"A": 48000, "B": 40000, "C": 30000}}
    no_reason = patch(client, "v9.1-test", "RULE-HOTEL-001", {"status": "confirmed", "value": lower})
    assert no_reason.status_code == 422 and error_code(no_reason) == "MODIFICATION_REASON_REQUIRED"
    blank = patch(client, "v9.1-test", "RULE-HOTEL-001",
                  {"status": "confirmed", "value": lower, "modification_reason": "  "})
    assert error_code(blank) == "MODIFICATION_REASON_REQUIRED"
    bad_shape = patch(client, "v9.1-test", "RULE-HOTEL-001",
                      {"status": "confirmed", "value": {"caps_fen": {"A": "480 元"}}, "modification_reason": "x"})
    assert bad_shape.status_code == 422 and error_code(bad_shape) == "INVALID_RULE_SHAPE"
    modified = patch(client, "v9.1-test", "RULE-HOTEL-001",
                     {"status": "confirmed", "value": lower, "modification_reason": "试点城市临时下调"})
    assert modified.status_code == 200, modified.text
    assert modified.json()["value"] == lower and modified.json()["modification_reason"] == "试点城市临时下调"
    # Same value again is not a modification: no reason needed.
    same = patch(client, "v9.1-test", "RULE-HOTEL-001", {"status": "confirmed", "value": lower})
    assert same.status_code == 200

    updates = policy_events(settings, "v9.1-test", "policy_rule_updated")
    hotel = [e for e in updates if e["payload"]["rule_id"] == "RULE-HOTEL-001"]
    assert hotel[0]["payload"]["before"]["value"] == {"caps_fen": {"A": 50000, "B": 40000, "C": 30000}}
    assert hotel[0]["payload"]["after"]["value"] == lower
    assert hotel[0]["payload"]["modified_fields"] == ["value"]
    assert hotel[0]["payload"]["modification_reason"] == "试点城市临时下调"
    assert hotel[1]["payload"]["modified_fields"] == []
    assert hotel[0]["refs"] == [{"kind": "rule", "id": "RULE-HOTEL-001", "version": "v9.1-test"}]

    # Rejected and unsupported are allowed for an invalid quote; status values are a closed set.
    assert patch(client, "v9.1-test", INVALID_ID, {"status": "unsupported"}).status_code == 200
    assert error_code(patch(client, "v9.1-test", INVALID_ID, {"status": "candidate"})) == "INVALID_RULE_STATUS"
    assert patch(client, "v9.1-test", INVALID_ID, {"status": "maybe"}).status_code == 400
    extra = patch(client, "v9.1-test", INVALID_ID, {"status": "confirmed", "quote": "出差不超过 15 天"})
    assert extra.status_code == 422 and error_code(extra) == "VALIDATION_ERROR"
    assert patch(client, "v9.1-test", INVALID_ID, {}).status_code == 422
    assert patch(client, "v9.1-test", "RULE-NOPE-001", {"status": "rejected"}).status_code == 404
    assert patch(client, "v9.1-test", INVALID_ID, {"status": "rejected"}, user=ZHANG).status_code == 404
    assert patch(client, "v1.2", "RULE-CITY-001", {"status": "rejected"}, user=LISI).status_code == 403
    seeded = patch(client, "v1.2", "RULE-CITY-001", {"status": "rejected"})
    assert seeded.status_code == 409 and error_code(seeded) == "POLICY_NOT_DRAFT"


def test_ac005_publish_preconditions_then_atomic_publish(client: TestClient, settings: Settings):
    upload(client, settings, "v9.1-test")
    assert error_code(publish(client, "v9.1-test")) == "MISSING_RULE_TYPES"  # nothing extracted yet
    extract(client, "v9.1-test")

    assert publish(client, "v9.1-test", key=None).status_code == 400
    early = publish(client, "v9.1-test")
    assert early.status_code == 409 and error_code(early) == "UNPROCESSED_CANDIDATES"
    assert early.json()["error"]["details"]["rule_ids"] == CLOSED_IDS + [INVALID_ID]

    for rule_id in CLOSED_IDS:
        patch(client, "v9.1-test", rule_id, {"status": "rejected" if rule_id == "RULE-MEAL-001" else "confirmed"})
    patch(client, "v9.1-test", INVALID_ID, {"status": "rejected"})
    missing = publish(client, "v9.1-test")
    assert missing.status_code == 422 and error_code(missing) == "MISSING_RULE_TYPES"
    assert missing.json()["error"]["details"]["missing_types"] == ["meal_allowance"]
    patch(client, "v9.1-test", "RULE-HOTEL-002", {"status": "rejected"})
    patch(client, "v9.1-test", "RULE-MEAL-001", {"status": "confirmed"})
    level = publish(client, "v9.1-test")
    assert error_code(level) == "MISSING_RULE_TYPES" and level.json()["error"]["details"]["missing_types"] == [
        "hotel_cap"]  # the manager level is missing
    patch(client, "v9.1-test", "RULE-HOTEL-002", {"status": "confirmed"})
    assert versions(settings)["v1.2"] == "published"  # failed attempts changed nothing

    assert publish(client, "v9.1-test", user=ZHANG).status_code == 404
    assert publish(client, "v1.2", user=LISI).status_code == 403
    done = publish(client, "v9.1-test")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["policy"]["status"] == "published" and body["archived_version"] == "v1.2"
    assert body["confirmed_rule_ids"] == CLOSED_IDS
    assert versions(settings) == {"v1.0": "archived", "v1.2": "archived", "v9.1-test": "published"}

    replay = publish(client, "v9.1-test")
    assert replay.status_code == 200 and replay.json() == body
    again = publish(client, "v9.1-test", key="pub-2")
    assert again.status_code == 409 and error_code(again) == "POLICY_NOT_DRAFT"
    assert error_code(patch(client, "v9.1-test", "RULE-CITY-001", {"status": "rejected"})) == "POLICY_NOT_DRAFT"
    (event,) = policy_events(settings, "v9.1-test", "policy_published")
    assert event["payload"]["archived"] == "v1.2" and event["actor"] == WANG
    assert {"kind": "policy", "id": "v1.2"} in event["refs"]

    # Runtime uses the newly published confirmed rules (REQ-008).
    trip_id = trip_with(client, S01)
    stream(client, f"/api/trips/{trip_id}/submit", {"expected_revision": 1}, user=ZHANG, key="s-1")
    trip = get_trip(client, trip_id)
    assert trip["policy_version"] == "v9.1-test" and trip["status"] == "PENDING_APPROVAL"
    hotel_cap = [v for v in trip["violations"] if v["code"] == "HOTEL_OVER_CAP"]
    assert hotel_cap == []  # 48000 ≤ 50000 under the published copy of v1.2


def test_policy_reads_hide_drafts_and_candidates_from_non_admins(client: TestClient, settings: Settings):
    upload(client, settings, "v9.1-test")
    extract(client, "v9.1-test")
    admin_list = client.get("/api/policies", headers=h(WANG)).json()["policies"]
    assert [p["version"] for p in admin_list] == ["v1.0", "v1.2", "v9.1-test"]
    assert admin_list[2]["rule_counts"]["candidate"] == 9
    employee_list = client.get("/api/policies", headers=h(ZHANG)).json()["policies"]
    assert [(p["version"], p["status"]) for p in employee_list] == [("v1.0", "archived"), ("v1.2", "published")]
    assert "rule_counts" not in employee_list[0]
    assert client.get("/api/policies/v9.1-test", headers=h(ZHANG)).status_code == 404
    detail = client.get("/api/policies/v1.2", headers=h(ZHANG)).json()
    assert len(detail["rules"]) == 8 and {r["status"] for r in detail["rules"]} == {"confirmed"}
    assert {r["type"] for r in detail["rules"]} == set(CLOSED_RULE_TYPES)
    assert client.get("/api/policies", headers=h()).status_code == 401


def test_reset_removes_uploaded_policies_and_their_files(client: TestClient, settings: Settings):
    upload(client, settings, "v9.1-test")
    assert len(list((settings.upload_dir / "policies").iterdir())) == 1
    reset = client.post("/api/demo/reset", json={"confirmation": "RESET_DEMO_DATA"}, headers=h(WANG))
    assert reset.status_code == 200
    assert versions(settings) == {"v1.0": "archived", "v1.2": "published"}
    assert list((settings.upload_dir / "policies").iterdir()) == []


# ------------------------------------------------------------------ S-12 / AC-024 through #20–#23


def publish_v13_via_api(client: TestClient, settings: Settings) -> dict[str, Any]:
    assert upload(client, settings, "v1.3", "v1.3.md").status_code == 201
    candidates = {c["id"]: c for c in extract(client, "v1.3").json()["candidates"]}
    assert candidates["RULE-HOTEL-001"]["value"]["caps_fen"]["A"] == 45000
    assert candidates["RULE-HOTEL-001"]["quote"] == V13_HOTEL_QUOTE
    assert candidates[INVALID_ID]["quote_status"] == "invalid_quote"
    assert candidates[UNSUPPORTED_ID]["status"] == "unsupported"
    review_all(client, "v1.3")
    response = publish(client, "v1.3", key="pub-v13")
    assert response.status_code == 200, response.text
    return response.json()


def test_ac024_s12_publish_v13_through_the_api_then_revalidate(client: TestClient, settings: Settings):
    trip_id = pending_s01(client)
    before = get_trip(client, trip_id)
    assert before["policy_version"] == "v1.2"
    assert not [v for v in before["violations"] if v["code"] == "HOTEL_OVER_CAP"]

    published = publish_v13_via_api(client, settings)
    assert published["archived_version"] == "v1.2"
    assert versions(settings) == {"v1.0": "archived", "v1.2": "archived", "v1.3": "published"}
    assert get_trip(client, trip_id)["policy_change_notice"] == {"bound_version": "v1.2",
                                                                  "published_version": "v1.3"}

    response = revalidate(client, trip_id)
    assert response.status_code == 200, response.text
    trip = response.json()["trip"]
    assert (trip["status"], trip["revision"], trip["policy_version"]) == ("PENDING_APPROVAL", 2, "v1.3")
    (hotel,) = [v for v in trip["violations"] if v["code"] == "HOTEL_OVER_CAP"]
    assert (hotel["actual_value"], hotel["limit_value"], hotel["rule_id"]) == (48000, 45000, "RULE-HOTEL-001")
    assert hotel["quote"] == V13_HOTEL_QUOTE and hotel["clause"] == "POL-2" and hotel["source"] == "policy"
    assert approvals(settings, trip_id) == [{"revision": 1, "status": "superseded", "decision": None},
                                            {"revision": 2, "status": "pending", "decision": None}]
    (event,) = audit_events(client, trip_id, "revalidated")
    assert (event["payload"]["before"]["policy_version"], event["payload"]["after"]["policy_version"]) == (
        "v1.2", "v1.3")
    assert event["payload"]["providers_called"] is False
