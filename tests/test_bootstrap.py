from __future__ import annotations

import json

from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel

from travel_desk.api.deps import current_user, require_admin, require_manager
from travel_desk.config import Settings
from travel_desk.db.database import db_session, initialize_database, read_session, reset_database
from travel_desk.db.repositories import list_policy_rules
from travel_desk.main import create_app

EXPECTED_TYPES = {
    "city_tier",
    "hotel_cap",
    "meal_allowance",
    "cabin_class",
    "advance_booking_days",
    "auto_approve_limit",
    "trip_duration_limit",
}


def scalar(settings: Settings, sql: str, params: tuple = ()):
    with read_session(settings.db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_seed_is_idempotent_and_complete(settings: Settings) -> None:
    initialize_database(settings)
    initialize_database(settings)

    assert scalar(settings, "SELECT COUNT(*) FROM employees") == 4
    assert scalar(settings, "SELECT COUNT(*) FROM policy_versions") == 2
    assert scalar(settings, "SELECT COUNT(*) FROM policy_versions WHERE status = 'published'") == 1
    assert scalar(settings, "SELECT version FROM policy_versions WHERE status = 'published'") == "v1.2"
    assert scalar(settings, "SELECT COUNT(*) FROM policy_rules WHERE version = 'v1.2'") == 8
    assert scalar(settings, "SELECT COUNT(*) FROM policy_rules WHERE version = 'v1.0'") == 8
    assert scalar(settings, "SELECT COUNT(*) FROM faults WHERE enabled = 1") == 0

    with read_session(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT id, manager_id FROM employees WHERE id IN ('u-zhangsan','u-lisi')"
            " ORDER BY id"
        ).fetchall()
    assert dict(rows[0]) == {"id": "u-lisi", "manager_id": None}
    assert dict(rows[1]) == {"id": "u-zhangsan", "manager_id": "u-lisi"}


def test_seeded_rules_are_traceable_and_typed(settings: Settings) -> None:
    initialize_database(settings)
    for version, filename in (("v1.0", "v1.0.md"), ("v1.2", "v1.2.md")):
        text = (settings.policies_dir / filename).read_text(encoding="utf-8")
        rules = list_policy_rules(settings.db_path, version)
        assert {rule["type"] for rule in rules} == EXPECTED_TYPES
        assert all(rule["status"] == "confirmed" for rule in rules)
        assert all(rule["quote_status"] == "valid" for rule in rules)
        for rule in rules:
            assert rule["quote"] in text, rule["id"]
            line = int(rule["locator"].removeprefix("line:"))
            assert rule["quote"] in text.splitlines()[line - 1]


def test_policy_versions_differ_as_contract_specifies(settings: Settings) -> None:
    initialize_database(settings)
    v10 = {rule["id"]: rule for rule in list_policy_rules(settings.db_path, "v1.0")}
    v12 = {rule["id"]: rule for rule in list_policy_rules(settings.db_path, "v1.2")}
    assert v10["RULE-ADVANCE-001"]["value"]["days"] == 5
    assert v12["RULE-ADVANCE-001"]["value"]["days"] == 3
    assert v10["RULE-AUTO-001"]["value"]["value_fen"] == 200000
    assert v12["RULE-AUTO-001"]["value"]["value_fen"] == 300000
    assert v10["RULE-HOTEL-001"]["value"] == v12["RULE-HOTEL-001"]["value"]


def test_v13_fixture_has_lower_employee_cap(settings: Settings) -> None:
    text = (settings.policies_dir / "v1.3.md").read_text(encoding="utf-8")
    assert "员工住宿上限：A 类城市每晚上限 450 元" in text
    assert "员工住宿上限：A 类城市每晚上限 500 元" not in text


def test_reset_restores_seed_and_drops_dynamic_rows(settings: Settings) -> None:
    initialize_database(settings)
    with db_session(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO policy_versions (version, status, effective_date, document_path,"
            " document_hash, created_at) VALUES ('v9.9','draft','2027-01-01','x','x','now')"
        )
        conn.execute(
            "INSERT INTO trips (id, employee_id, status, revision, slots_json, thread_id,"
            " created_at, updated_at) VALUES ('t1','u-zhangsan','COLLECTING',1,'{}','t1',"
            "'2026-10-20T00:00:00+00:00','2026-10-20T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO audit_events (id, trip_id, revision, ts, actor, type, payload_json,"
            " trace_id) VALUES ('a1','t1',1,'2026-10-20T00:00:00+00:00','u-zhangsan',"
            "'test','{}','trace')"
        )

    reset_database(settings)

    assert scalar(settings, "SELECT COUNT(*) FROM trips") == 0
    assert scalar(settings, "SELECT COUNT(*) FROM audit_events") == 0
    assert scalar(settings, "SELECT COUNT(*) FROM policy_versions") == 2
    assert scalar(settings, "SELECT COUNT(*) FROM policy_versions WHERE version = 'v9.9'") == 0
    assert scalar(settings, "SELECT COUNT(*) FROM employees") == 4


def test_health_and_users_endpoints(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["demo_today"] == "2026-10-20"

        users = client.get("/api/users").json()["users"]
        assert {user["name"] for user in users} == {"张三", "李四", "赵六", "王五"}
        assert users[0]["role"] == "employee"
        assert "preferences" in users[0]


class ProbeBody(BaseModel):
    amount_fen: int


def _probe_app(settings: Settings):
    """Real app plus probe routes that exercise the shared identity and error machinery."""
    app = create_app(settings)

    @app.get("/api/_probe/me")
    def me(user: dict = Depends(current_user)) -> dict:
        return {"id": user["id"]}

    @app.get("/api/_probe/admin")
    def admin_only(user: dict = Depends(require_admin)) -> dict:
        return {"id": user["id"]}

    @app.get("/api/_probe/manager")
    def manager_only(user: dict = Depends(require_manager)) -> dict:
        return {"id": user["id"]}

    @app.post("/api/_probe/body")
    def body(payload: ProbeBody) -> dict:
        return payload.model_dump()

    @app.get("/api/_probe/boom")
    def boom() -> dict:
        raise RuntimeError("secret-provider-payload")

    return app


def assert_envelope(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert set(error) == {"code", "message", "details", "trace_id"}
    assert error["trace_id"]
    return error


def test_identity_and_error_envelope(settings: Settings) -> None:
    with TestClient(_probe_app(settings), raise_server_exceptions=False) as client:
        assert_envelope(client.get("/api/_probe/me"), 401, "UNAUTHENTICATED")
        unknown = assert_envelope(client.get("/api/_probe/me", headers={"X-Demo-User": "u-nobody"}),
                                  401, "UNKNOWN_DEMO_USER")
        assert unknown["details"] == {"user_id": "u-nobody"}
        assert client.get("/api/_probe/me", headers={"X-Demo-User": "u-zhangsan"}).json() == {"id": "u-zhangsan"}

        # §6.4: admin-only and manager-only capabilities; admin cannot approve.
        assert_envelope(client.get("/api/_probe/admin", headers={"X-Demo-User": "u-lisi"}), 403, "FORBIDDEN")
        assert client.get("/api/_probe/admin", headers={"X-Demo-User": "u-wangwu"}).status_code == 200
        assert_envelope(client.get("/api/_probe/manager", headers={"X-Demo-User": "u-wangwu"}), 403, "FORBIDDEN")
        assert_envelope(client.get("/api/_probe/manager", headers={"X-Demo-User": "u-zhangsan"}), 403, "FORBIDDEN")
        assert client.get("/api/_probe/manager", headers={"X-Demo-User": "u-lisi"}).status_code == 200

        # §6.2: unparseable JSON -> 400; well-formed but invalid fields -> 422.
        bad_json = client.post("/api/_probe/body", content=b"{not json",
                               headers={"Content-Type": "application/json"})
        assert_envelope(bad_json, 400, "INVALID_JSON")
        assert_envelope(client.post("/api/_probe/body", json={"amount_fen": "abc"}), 422, "VALIDATION_ERROR")

        assert_envelope(client.get("/api/does-not-exist"), 404, "NOT_FOUND")

        crash = assert_envelope(client.get("/api/_probe/boom"), 500, "INTERNAL_ERROR")
        assert "secret-provider-payload" not in json.dumps(crash)


def test_trace_id_is_echoed_only_when_safe(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        ok = client.get("/api/health", headers={"X-Trace-Id": "abc-123"})
        assert ok.headers["X-Trace-Id"] == "abc-123"
        unsafe = client.get("/api/health", headers={"X-Trace-Id": "x" * 65})
        assert unsafe.headers["X-Trace-Id"] != "x" * 65
        error = client.get("/api/does-not-exist", headers={"X-Trace-Id": "trace-1"})
        assert error.json()["error"]["trace_id"] == "trace-1"
        assert error.headers["X-Trace-Id"] == "trace-1"


def test_reset_deletes_only_tracked_upload_files(settings: Settings) -> None:
    initialize_database(settings)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    tracked = settings.upload_dir / "up-1.png"
    tracked.write_bytes(b"png")
    unrelated = settings.upload_dir / "user-notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    with db_session(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO uploads (id, filename, content_type, path, sha256, size_bytes, uploaded_by,"
            " created_at) VALUES ('up-1','a.png','image/png',?,'x',3,'u-zhangsan',"
            "'2026-10-20T00:00:00+00:00')",
            (str(tracked),),
        )

    reset_database(settings)

    assert not tracked.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert scalar(settings, "SELECT COUNT(*) FROM uploads") == 0


def test_invalid_configuration_fails_loudly(monkeypatch) -> None:
    import pytest

    from travel_desk.config import ConfigError

    monkeypatch.setenv("TRAVEL_DESK_LLM_MODE", "live")
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.setenv("TRAVEL_DESK_LLM_MODE", "fake")
    monkeypatch.setenv("DEMO_TODAY", "20261020")
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.setenv("DEMO_TODAY", "2026-10-20")
    assert Settings.from_env().demo_today.isoformat() == "2026-10-20"


def test_audit_and_outbox_are_append_only_outside_reset(settings: Settings) -> None:
    import sqlite3

    import pytest

    initialize_database(settings)
    with db_session(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO audit_events (id, trip_id, revision, ts, actor, type, payload_json, trace_id)"
            " VALUES ('a1', NULL, NULL, '2026-10-20T00:00:00+00:00', 'u-wangwu', 'test', '{}', 't')"
        )
        conn.execute(
            "INSERT INTO outbox_emails (id, trip_id, revision, kind, to_json, subject, body, created_at,"
            " unique_key) VALUES ('o1','t1',1,'trip_delivery','[]','s','b','2026-10-20T00:00:00+00:00',"
            "'t1:1:trip_delivery')"
        )
    for sql in (
        "UPDATE audit_events SET type = 'tampered'",
        "DELETE FROM audit_events",
        "UPDATE outbox_emails SET body = 'tampered'",
        "DELETE FROM outbox_emails",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            with db_session(settings.db_path) as conn:
                conn.execute(sql)
    assert scalar(settings, "SELECT type FROM audit_events WHERE id = 'a1'") == "test"

    reset_database(settings)
    assert scalar(settings, "SELECT COUNT(*) FROM audit_events") == 0
    assert scalar(settings, "SELECT COUNT(*) FROM outbox_emails") == 0
    assert scalar(settings, "SELECT COUNT(*) FROM meta WHERE key = 'reset_in_progress'") == 0


def test_incompatible_schema_version_is_refused(settings: Settings) -> None:
    import pytest

    from travel_desk.db.database import SchemaMismatchError, connect

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(SchemaMismatchError):
        initialize_database(settings)
