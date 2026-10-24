"""TASK-010 / AC-021: the demo egress boundary (HC-06, REQ-021/022).

The scenario runner proves the *negative* — a full fake-mode run makes zero
external attempts. These tests are the *positive control*: they show the guard
really does intercept and refuse an outbound call, log it, and let through only
what configuration allows. Without them "external attempts: 0" could just mean
nothing ever tried.

The final section is the IMP-97 regression suite: a host name that arrives as
`bytes` (which `getaddrinfo` accepts, and which httpcore actually passes) must
take exactly the same decision as its `str` form, on every intercepted route,
and a host that cannot be normalised must fail closed.
"""
from __future__ import annotations

import json
import socket
import urllib.request
from typing import Any

import pytest
from travel_desk.network_guard import (
    NetworkBoundaryError,
    NetworkGuard,
    allowed_hosts_for,
)

EXTERNAL = "api.example-internet.com"


# --------------------------------------------------------------------- allowlist


@pytest.mark.parametrize("mode", ["fake", "recorded", "off", ""])
def test_non_real_modes_allow_nothing_external(mode: str) -> None:
    """HC-06: only AUTH-01 real model calls may leave the machine."""
    assert allowed_hosts_for(mode, "https://dashscope.aliyuncs.com/compatible-mode/v1") == frozenset()


def test_real_mode_allows_only_the_model_host() -> None:
    hosts = allowed_hosts_for("real", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert hosts == frozenset({"dashscope.aliyuncs.com"})


def test_real_mode_without_a_host_allows_nothing() -> None:
    assert allowed_hosts_for("real", "not-a-url") == frozenset()


# ------------------------------------------------------------------ positive control


def test_getaddrinfo_to_external_host_is_refused_and_logged(tmp_path) -> None:
    log = tmp_path / "network-guard.jsonl"
    guard = NetworkGuard(allowed_hosts=frozenset(), log_path=log)
    guard.install()
    try:
        with pytest.raises(NetworkBoundaryError):
            socket.getaddrinfo(EXTERNAL, 443)
    finally:
        guard.uninstall()

    summary = guard.summary()
    assert summary["blocked"] == 1
    assert summary["external_attempts"] == 1
    assert summary["allowed"] == 0
    # The attempt is on disk as evidence, with the caller that triggered it.
    assert log.exists()
    line = log.read_text(encoding="utf-8").strip()
    assert EXTERNAL in line and '"decision": "blocked"' in line


def test_create_connection_to_external_host_is_refused(tmp_path) -> None:
    guard = NetworkGuard(allowed_hosts=frozenset(), log_path=tmp_path / "g.jsonl")
    guard.install()
    try:
        with pytest.raises(NetworkBoundaryError):
            socket.create_connection((EXTERNAL, 443))
    finally:
        guard.uninstall()
    assert guard.summary()["blocked"] == 1


def test_socket_connect_to_external_host_is_refused(tmp_path) -> None:
    guard = NetworkGuard(allowed_hosts=frozenset(), log_path=tmp_path / "g.jsonl")
    guard.install()
    try:
        with pytest.raises(NetworkBoundaryError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((EXTERNAL, 443))
    finally:
        guard.uninstall()
    assert guard.summary()["blocked"] == 1


def test_urlopen_to_external_host_is_refused(tmp_path) -> None:
    guard = NetworkGuard(allowed_hosts=frozenset(), log_path=tmp_path / "g.jsonl")
    guard.install()
    try:
        with pytest.raises(NetworkBoundaryError):
            urllib.request.urlopen(f"https://{EXTERNAL}/x")
    finally:
        guard.uninstall()
    assert guard.summary()["blocked"] == 1


def test_allowlisted_host_passes_through(tmp_path, monkeypatch) -> None:
    """In real mode the model host is allowed and reaches the original resolver."""
    reached = {}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        reached["host"] = host
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.4", port or 0))]

    monkeypatch.setattr("travel_desk.network_guard.socket.getaddrinfo", fake_getaddrinfo, raising=False)
    guard = NetworkGuard(allowed_hosts=frozenset({EXTERNAL}), log_path=tmp_path / "g.jsonl")
    # install() captures the (already monkeypatched) original, so pass-through is observable.
    guard.install()
    try:
        socket.getaddrinfo(EXTERNAL, 443)
    finally:
        guard.uninstall()

    assert reached["host"] == EXTERNAL
    summary = guard.summary()
    assert summary["allowed"] == 1 and summary["blocked"] == 0
    assert summary["external_hosts"][f"{EXTERNAL}:443"] == {"allowed": 1, "blocked": 0}


def test_loopback_is_local_not_external(tmp_path) -> None:
    guard = NetworkGuard(allowed_hosts=frozenset(), log_path=tmp_path / "g.jsonl")
    assert guard.record("127.0.0.1", 8000, via="test") == "local"
    assert guard.record("localhost", 8000, via="test") == "local"
    summary = guard.summary()
    assert summary["local"] == 2
    assert summary["external_attempts"] == 0


def test_blocked_by_caller_names_the_offending_frame(tmp_path) -> None:
    guard = NetworkGuard(allowed_hosts=frozenset(), log_path=tmp_path / "g.jsonl")
    guard.install()
    try:
        with pytest.raises(NetworkBoundaryError):
            socket.getaddrinfo(EXTERNAL, 443)
    finally:
        guard.uninstall()
    callers = guard.summary()["blocked_by_caller"]
    assert callers and any("test_network_guard" in c for c in callers)


def test_uninstall_restores_the_original_resolver() -> None:
    original = socket.getaddrinfo
    guard = NetworkGuard(allowed_hosts=frozenset())
    guard.install()
    assert socket.getaddrinfo is not original
    guard.uninstall()
    assert socket.getaddrinfo is original


# ------------------------------------------- IMP-97: host-form normalisation (fail closed)

BYTES_EXTERNAL = EXTERNAL.encode("ascii")
UNDECODABLE = b"\xff\xfe.not-ascii"
MODEL_HOST = "dashscope.aliyuncs.com"


class _BytesHostURL:
    """Stand-in for a parsed URL whose `.host` is bytes.

    `urlopen` reads the host with `getattr(url, "host", None)`, so this is enough
    to exercise the fourth intercepted route with a non-`str` host without
    depending on urllib accepting a bytes URL.
    """

    def __init__(self, host: Any, port: Any = None):
        self.host = host
        self.port = port


def _via_getaddrinfo() -> None:
    socket.getaddrinfo(BYTES_EXTERNAL, 443)


def _via_create_connection() -> None:
    socket.create_connection((BYTES_EXTERNAL, 443))


def _via_socket_connect() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((BYTES_EXTERNAL, 443))
    finally:
        sock.close()


def _via_urlopen() -> None:
    urllib.request.urlopen(_BytesHostURL(BYTES_EXTERNAL))


def _stub_originals(monkeypatch) -> dict[str, Any]:
    """Stand in for the four real entry points, so "allowed" reaches no network.

    `NetworkGuard.install()` captures whatever `socket` and `urllib.request` hold at
    that moment as its originals, so patching first and installing second puts these
    stubs *behind* the hooks. What is then under test is the guard's own decision on
    each real route, and nothing leaves the machine while it is being made.
    """
    reached: dict[str, Any] = {}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        reached["getaddrinfo"] = host
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.4", port or 0))]

    def fake_create_connection(address, *args, **kwargs):
        reached["create_connection"] = address
        return "stub-socket"

    def fake_connect(self, address):
        reached["socket.connect"] = address
        return None

    def fake_urlopen(url, *args, **kwargs):
        reached["urlopen"] = url
        return None

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(socket.socket, "connect", fake_connect)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return reached


def test_bytes_external_host_is_refused_and_logged(tmp_path) -> None:
    """The reported defect: httpcore hands `getaddrinfo` bytes, `_local()` waved it through."""
    log = tmp_path / "network-guard.jsonl"
    guard = NetworkGuard(allowed_hosts=frozenset(), log_path=log)
    guard.install()
    try:
        with pytest.raises(NetworkBoundaryError):
            socket.getaddrinfo(BYTES_EXTERNAL, 443)
    finally:
        guard.uninstall()

    summary = guard.summary()
    assert summary["blocked"] == 1 and summary["allowed"] == 0
    assert summary["local"] == 0, "a bytes host name must never be classified as local"
    assert summary["external_attempts"] == 1
    line = log.read_text(encoding="utf-8").strip()
    assert EXTERNAL in line, "the log carries the normalised name, not an opaque repr"
    assert '"decision": "blocked"' in line and '"host_form": "bytes"' in line


@pytest.mark.parametrize("route", [_via_getaddrinfo, _via_create_connection, _via_socket_connect, _via_urlopen],
                         ids=["getaddrinfo", "create_connection", "socket.connect", "urlopen"])
def test_bytes_external_host_is_refused_on_every_route(route, tmp_path) -> None:
    """One shared normalisation point, so all four routes behave alike — not just getaddrinfo.

    Each `_via_*` calls the *patched real hook* (`socket.getaddrinfo`,
    `socket.create_connection`, `socket.socket.connect`, `urllib.request.urlopen`), not
    `NetworkGuard.record()`. That matters: it is the hook that decides what to hand
    `record()` as the host, so only a real-hook test can show that all four of them
    pass a host *element* rather than the whole address tuple.
    """
    guard = NetworkGuard(allowed_hosts=frozenset(), log_path=tmp_path / "g.jsonl")
    guard.install()
    try:
        with pytest.raises(NetworkBoundaryError):
            route()
    finally:
        guard.uninstall()
    summary = guard.summary()
    assert summary["blocked"] == 1 and summary["local"] == 0
    assert summary["external_attempts"] == 1, "exactly one attempt per route, no double recording"


@pytest.mark.parametrize("call,route", [
    (lambda: socket.getaddrinfo(MODEL_HOST.encode("ascii"), 443), "getaddrinfo"),
    (lambda: socket.create_connection((MODEL_HOST.encode("ascii"), 443)), "create_connection"),
    (lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        .connect((MODEL_HOST.encode("ascii"), 443)), "socket.connect"),
    (lambda: urllib.request.urlopen(_BytesHostURL(MODEL_HOST.encode("ascii"), 443)), "urlopen"),
], ids=["getaddrinfo", "create_connection", "socket.connect", "urlopen"])
def test_allowlisted_bytes_host_passes_through_on_every_route(call, route, tmp_path, monkeypatch) -> None:
    """The positive half: fail-closed must not become fail-everything.

    Refusing is only half of a boundary. If any hook handed `record()` something other
    than a host element, the fix would classify it as unnormalisable and refuse *all*
    connects — breaking AUTH-01 and the demo itself. So for every route, the allowlisted
    model host arriving as bytes must be let through and must reach the stubbed original.
    """
    reached = _stub_originals(monkeypatch)
    guard = NetworkGuard(allowed_hosts=frozenset({MODEL_HOST}), log_path=tmp_path / "g.jsonl")
    guard.install()
    try:
        call()  # the assertion is that this does not raise
    finally:
        guard.uninstall()

    summary = guard.summary()
    assert summary["allowed"] == 1, (route, summary)
    assert summary["blocked"] == 0, f"{route} refused an allowlisted host"
    assert summary["local"] == 0, (route, summary)
    assert reached.get(route) is not None, f"{route} never reached the stubbed original"
    assert summary["external_hosts"][f"{MODEL_HOST}:443"] == {"allowed": 1, "blocked": 0}


def test_bytes_allowlisted_host_passes_through(tmp_path, monkeypatch) -> None:
    """The fix must not break AUTH-01: the model host still passes when it arrives as bytes."""
    reached = {}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        reached["host"] = host
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.4", port or 0))]

    monkeypatch.setattr("travel_desk.network_guard.socket.getaddrinfo", fake_getaddrinfo, raising=False)
    guard = NetworkGuard(allowed_hosts=frozenset({MODEL_HOST}), log_path=tmp_path / "g.jsonl")
    guard.install()
    try:
        socket.getaddrinfo(MODEL_HOST.encode("ascii"), 443)
    finally:
        guard.uninstall()

    assert reached["host"] == MODEL_HOST.encode("ascii"), "forwarded untouched; only the decision normalises"
    summary = guard.summary()
    assert summary["allowed"] == 1 and summary["blocked"] == 0 and summary["local"] == 0
    assert summary["external_hosts"][f"{MODEL_HOST}:443"] == {"allowed": 1, "blocked": 0}


def test_undecodable_bytes_host_is_refused_even_with_a_populated_allowlist(tmp_path) -> None:
    """Cannot be normalised => external => refused, and still evidenced (fail closed)."""
    log = tmp_path / "g.jsonl"
    guard = NetworkGuard(allowed_hosts=frozenset({MODEL_HOST, EXTERNAL}), log_path=log)
    assert guard.record(UNDECODABLE, 443, via="test") == "blocked"
    summary = guard.summary()
    assert summary["blocked"] == 1 and summary["local"] == 0 and summary["allowed"] == 0
    line = log.read_text(encoding="utf-8")
    assert '"decision": "blocked"' in line and '"host_form": "bytes"' in line


@pytest.mark.parametrize("host", [12345, object(), ("nested", 1), b"\x00\xff"])
def test_non_normalisable_host_fails_closed(host, tmp_path) -> None:
    guard = NetworkGuard(allowed_hosts=frozenset({MODEL_HOST, EXTERNAL}), log_path=tmp_path / "g.jsonl")
    assert guard.record(host, 443, via="test") == "blocked"
    assert guard.summary()["local"] == 0


def test_socket_connect_peer_fallback_fails_closed(tmp_path) -> None:
    """The one hook branch that can hand `record()` something other than a host element.

    `connect()` falls back to `host = address` when a bare internet-socket connect cannot
    report its peer. That object is not a name, so it cannot be normalised — and the
    boundary must refuse it rather than wave it through as an "unusual address object",
    which is what it did before IMP-97 and precisely the risk this suite has to rule out.
    """

    class _NoPeer(socket.socket):
        """An internet socket whose `getpeername()` fails, forcing the fallback branch."""

        def getpeername(self):  # noqa: D102 - test double
            raise OSError("socket is not connected")

    guard = NetworkGuard(allowed_hosts=frozenset({MODEL_HOST, EXTERNAL}), log_path=tmp_path / "g.jsonl")
    sock = _NoPeer(socket.AF_INET, socket.SOCK_STREAM)
    guard.install()
    try:
        with pytest.raises(NetworkBoundaryError):
            sock.connect(12345)  # not a tuple, so the elif runs and getpeername() fails
    finally:
        guard.uninstall()
        sock.close()

    summary = guard.summary()
    assert summary["blocked"] == 1 and summary["local"] == 0
    assert summary["external_attempts"] == 1


class _ExplodingRepr:
    """A host object whose `repr()` raises, to prove evidence cannot break the request path."""

    def __repr__(self) -> str:
        raise RuntimeError("no repr for you")


def test_evidence_survives_a_host_whose_repr_raises(tmp_path) -> None:
    """Writing the log must never turn a boundary decision into a crash (HC-07)."""
    log = tmp_path / "g.jsonl"
    guard = NetworkGuard(allowed_hosts=frozenset({MODEL_HOST, EXTERNAL}), log_path=log)
    assert guard.record(_ExplodingRepr(), 443, via="test") == "blocked"

    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["decision"] == "blocked", "still refused, and still evidenced"
    assert entry["host_form"] == "_ExplodingRepr"
    assert entry["host"] == "<unrepresentable _ExplodingRepr>"


def test_wildcard_none_host_stays_local(tmp_path) -> None:
    """通配绑定，无远端主机: `getaddrinfo(None, port, AI_PASSIVE)` resolves the wildcard for a
    listening socket, so blocking it would break binding rather than protect anything.

    The only exception to fail-closed, and it deliberately gets no decision value of its
    own: the evidence tells a wildcard bind apart from a real peer by `host_form`.
    """
    log = tmp_path / "g.jsonl"
    guard = NetworkGuard(allowed_hosts=frozenset(), log_path=log)
    assert guard.record(None, 8000, via="test") == "local"
    assert guard.summary()["external_attempts"] == 0

    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["decision"] == "local" and entry["host_form"] == "NoneType"


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", "local"), ("localhost", "local"), ("::1", "local"), ("[::1]", "local"),
    ("0.0.0.0", "local"), ("", "local"), ("fe80::1", "local"), ("printer.local", "local"),
    ("svc.internal", "local"), (EXTERNAL, "blocked"), (MODEL_HOST, "allowed"),
])
def test_str_classification_is_unchanged(host: str, expected: str) -> None:
    """str regression: normalising other forms must not move any existing str decision."""
    guard = NetworkGuard(allowed_hosts=frozenset({MODEL_HOST}))
    assert guard.record(host, 443, via="test") == expected


@pytest.mark.parametrize("name", [EXTERNAL, MODEL_HOST, "DashScope.Aliyuncs.com", "127.0.0.1",
                                  "localhost", "fe80::1", "printer.local"])
def test_bytes_host_classifies_exactly_like_its_str_form(name: str) -> None:
    """Form independence: the same name decides the same way however it is encoded."""
    guard = NetworkGuard(allowed_hosts=frozenset({MODEL_HOST}))
    as_str = guard.record(name, 443, via="test-str")
    as_bytes = guard.record(name.encode("ascii"), 443, via="test-bytes")
    as_bytearray = guard.record(bytearray(name.encode("ascii")), 443, via="test-bytearray")
    assert as_bytes == as_str and as_bytearray == as_str
