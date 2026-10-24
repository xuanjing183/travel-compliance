"""SQLite schema. Amounts are integer fen; timestamps are UTC RFC 3339 strings."""
from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('employee', 'manager', 'admin')),
    department TEXT NOT NULL,
    manager_id TEXT REFERENCES employees(id),
    home_city TEXT NOT NULL,
    preferences_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_versions (
    version TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'archived')),
    effective_date TEXT NOT NULL,
    document_path TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    published_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS policy_versions_one_published
    ON policy_versions(status) WHERE status = 'published';

CREATE TABLE IF NOT EXISTS policy_rules (
    version TEXT NOT NULL REFERENCES policy_versions(version) ON DELETE CASCADE,
    id TEXT NOT NULL,
    type TEXT NOT NULL,
    condition_json TEXT NOT NULL,
    value_json TEXT NOT NULL,
    unit TEXT NOT NULL,
    clause TEXT NOT NULL,
    quote TEXT NOT NULL,
    locator TEXT NOT NULL,
    quote_status TEXT NOT NULL DEFAULT 'valid' CHECK (quote_status IN ('valid', 'invalid_quote')),
    status TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'rejected', 'unsupported')),
    confirmed_by TEXT,
    modification_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (version, id)
);

CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL REFERENCES employees(id),
    status TEXT NOT NULL CHECK (status IN (
        'COLLECTING', 'VALIDATING', 'POLICY_BLOCKED', 'PLANNING',
        'AUTO_APPROVED', 'PENDING_APPROVAL', 'APPROVED',
        'DELIVERY_PENDING', 'DELIVERED', 'REJECTED'
    )),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    slots_json TEXT NOT NULL DEFAULT '{}',
    preference_snapshot_json TEXT NOT NULL DEFAULT '{}',
    preference_source TEXT NOT NULL DEFAULT 'none',
    policy_version TEXT REFERENCES policy_versions(version),
    plan_json TEXT,
    cost_json TEXT,
    violations_json TEXT NOT NULL DEFAULT '[]',
    approval_reasons_json TEXT NOT NULL DEFAULT '[]',
    blocks_json TEXT NOT NULL DEFAULT '[]',
    thread_id TEXT NOT NULL,
    delivery_error TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trips_employee ON trips(employee_id, created_at);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    approver_id TEXT NOT NULL REFERENCES employees(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'decided', 'superseded')),
    decision TEXT CHECK (decision IN ('approve', 'reject', 'request_changes')),
    comment TEXT,
    idempotency_key TEXT,
    card_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS approvals_one_pending
    ON approvals(trip_id, revision) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS approvals_approver ON approvals(approver_id, status);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);

CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    trip_id TEXT,
    revision INTEGER,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    refs_json TEXT NOT NULL DEFAULT '[]',
    trace_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_trip ON audit_events(trip_id, seq);

-- REQ-020: audit events are append-only; only Demo reset (meta flag) may delete them.
CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
WHEN (SELECT value FROM meta WHERE key = 'reset_in_progress') IS NOT '1'
BEGIN
    SELECT RAISE(ABORT, 'audit_events can only be cleared by demo reset');
END;

CREATE TABLE IF NOT EXISTS outbox_emails (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('trip_delivery', 'workflow_notification')),
    to_json TEXT NOT NULL,
    cc_json TEXT NOT NULL DEFAULT '[]',
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    unique_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS outbox_emails_trip ON outbox_emails(trip_id, revision);

-- Written emails are immutable; reset is the only way to clear the outbox.
CREATE TRIGGER IF NOT EXISTS outbox_emails_no_update BEFORE UPDATE ON outbox_emails
BEGIN
    SELECT RAISE(ABORT, 'outbox_emails is append-only');
END;
CREATE TRIGGER IF NOT EXISTS outbox_emails_no_delete BEFORE DELETE ON outbox_emails
WHEN (SELECT value FROM meta WHERE key = 'reset_in_progress') IS NOT '1'
BEGIN
    SELECT RAISE(ABORT, 'outbox_emails can only be cleared by demo reset');
END;

-- Structured itinerary written together with the trip_delivery email (§4.7).
CREATE TABLE IF NOT EXISTS itineraries (
    trip_id TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (trip_id, revision)
);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    uploaded_by TEXT NOT NULL REFERENCES employees(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS image_extractions (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    upload_id TEXT NOT NULL REFERENCES uploads(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'discarded')),
    result_json TEXT NOT NULL,
    applied_json TEXT,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    confirmed_by TEXT
);

CREATE TABLE IF NOT EXISTS faults (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL
);
"""
