"""Demo egress boundary (HC-06, REQ-021/022) and the AC-021 evidence recorder.

The app is installed with a guard that intercepts the three ways Python reaches
the network — `socket.getaddrinfo` (name resolution), `socket.create_connection`
and `socket.socket.connect` — plus `urllib.request.urlopen`. Everything that is
not loopback, not a local filesystem socket, and not on the allowlist is refused
*before* a DNS query leaves the machine, and the attempt is written to
`<data_dir>/network-guard.jsonl` as evidence.

The allowlist is derived from configuration, never from an operator switch:
`fake`/`recorded` mode allows nothing external (zero external requests, HC-06),
`real` mode allows only the host of `dashscope_base_url` (the AUTH-01 model
endpoint). Loopback is allowed and logged separately, so the evidence can state
"external attempts: 0" while `uv run travel-desk` and the browser keep working.

Every host name reaches the decision through one normalisation step
(`_host_text`), because `getaddrinfo` also accepts `bytes` host names and
httpcore hands them over in that form. A host that cannot be normalised is
treated as external and refused — the boundary fails closed (IMP-97). The single
exception is `None`, the wildcard bind, which names no remote peer at all; it is
handled in `_local()` and evidenced as `host_form: "NoneType"` rather than by a
decision value of its own.

This is evidence and defence in depth, not a sandbox: it cannot stop a
subprocess or a C extension that opens its own sockets.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

LOG_NAME = "network-guard.jsonl"
# Local addresses that are not "external" by definition (HC-06 is about the internet).
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::]", ""})
LOCAL_SUFFIXES = (".local", ".internal")


class NetworkBoundaryError(OSError):
    """Raised instead of opening a connection that the demo boundary forbids.

    An `OSError` subclass so callers see an ordinary connection failure; the LLM
    layer reduces it to `ModelUnavailable` and the provider layer to a degraded
    plan part, exactly like a real network fault (§6.6).
    """


def _host_text(host: Any) -> str | None:
    """Normalise a peer host to text, or return `None` when that is impossible.

    `socket.getaddrinfo` accepts `bytes` host names (httpcore passes them in that
    form), so a bytes host must take exactly the same `_local()` + allowlist
    decision as `str` — otherwise it slips past a check written for strings.
    Decoding is strict ASCII on purpose: DNS names are ASCII, and an
    internationalised name must already be punycode-encoded before resolution.
    Bytes that are not ASCII, and any non-text address object, return `None` so
    the caller fails closed instead of guessing (IMP-97).
    """
    if isinstance(host, str):
        return host
    if isinstance(host, (bytes, bytearray, memoryview)):
        try:
            return bytes(host).decode("ascii")
        except UnicodeDecodeError:
            return None
    return None


def _host_repr(host: Any) -> str:
    """Evidence text for a host that could not be normalised to a name.

    Escaped and truncated, so an undecodable host is still evidenced without
    dumping raw bytes into the log (HC-07). Writing evidence must never itself
    break the request path: `repr()` of an arbitrary object can raise, so the
    fallback is a plain type name and the boundary decision still stands.
    """
    try:
        return repr(host)[:120]
    except Exception:  # noqa: BLE001 - repr() of an arbitrary object may do anything
        return f"<unrepresentable {type(host).__name__}>"


def _local(host: Any) -> bool:
    if host is None:
        # 通配绑定，无远端主机：`getaddrinfo(None, port, AI_PASSIVE)` resolves the
        # wildcard for a listening socket, so it names no remote peer and cannot
        # reach the internet. This is the only exception to fail-closed, it is
        # kept inside `_local()`, and it introduces no new decision value — the
        # evidence distinguishes it by `host_form: "NoneType"` with `decision:
        # "local"`. Behaviour here is unchanged from before IMP-97, which also
        # treated a non-str host as local; the fix only makes it explicit.
        return True
    text = _host_text(host)
    if text is None:
        return False  # cannot be normalised: treat as external (fail closed, IMP-97)
    lowered = text.lower().strip("[]")
    return lowered in LOCAL_HOSTS or lowered.endswith(LOCAL_SUFFIXES) or lowered.startswith("fe80")


def _caller() -> str:
    """The innermost frame outside this module and the standard library."""
    here = Path(__file__).resolve()
    stdlib = Path(socket.__file__).resolve().parent
    frame = sys._getframe()
    for _ in range(40):
        if frame is None:
            break
        frame = frame.f_back
        if frame is None:
            break
        path = Path(frame.f_code.co_filename).resolve()
        if path == here or path.parent == stdlib or "urllib" in path.parts[-2:]:
            continue
        try:
            return f"{path.relative_to(here.parents[2])}:{frame.f_lineno}"
        except ValueError:
            return f"{path.name}:{frame.f_lineno}"
    return "unknown"


class NetworkGuard:
    """Records and enforces the egress boundary for one app lifetime."""

    def __init__(self, *, allowed_hosts: frozenset[str] = frozenset(), log_path: Path | None = None):
        self.allowed_hosts = allowed_hosts
        self.log_path = log_path
        self.attempts: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._saved: dict[str, Any] = {}

    # ------------------------------------------------------------------ reporting

    def _classify(self, host: Any) -> str:
        if _local(host):
            return "local"
        text = _host_text(host)
        if text is not None and text.lower() in self.allowed_hosts:
            return "allowed"
        return "blocked"

    def record(self, host: Any, port: Any, *, via: str) -> str:
        decision = self._classify(host)
        text = _host_text(host)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "via": via,
                 # The normalised name when there is one, so the evidence is readable;
                 # otherwise an escaped repr. `host_form` records which it was, because
                 # a name that arrived as bytes is exactly what IMP-97 was about.
                 "host": text if text is not None else _host_repr(host),
                 "host_form": type(host).__name__,
                 "port": port, "decision": decision, "caller": _caller()}
        with self._lock:
            self.attempts.append(entry)
            if self.log_path is not None:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return decision

    def summary(self) -> dict[str, Any]:
        with self._lock:
            attempts = list(self.attempts)
        counts: dict[str, int] = {"local": 0, "allowed": 0, "blocked": 0}
        hosts: dict[str, list[int]] = {}
        for entry in attempts:
            counts[entry["decision"]] = counts.get(entry["decision"], 0) + 1
            if entry["decision"] != "local":
                key = f"{entry['host']}:{entry['port']}"
                hosts.setdefault(key, [0, 0])
                hosts[key][0 if entry["decision"] == "allowed" else 1] += 1
        return {"installed": True, "allowed_hosts": sorted(self.allowed_hosts), "attempts": len(attempts),
                "external_attempts": counts["allowed"] + counts["blocked"], "blocked": counts["blocked"],
                "allowed": counts["allowed"], "local": counts["local"],
                "external_hosts": {k: {"allowed": v[0], "blocked": v[1]} for k, v in sorted(hosts.items())},
                "blocked_by_caller": sorted({e["caller"] for e in attempts if e["decision"] == "blocked"})}

    def refuse(self, host: Any, port: Any, *, via: str) -> NetworkBoundaryError:
        return NetworkBoundaryError(
            f"Demo 网络边界拒绝访问 {host}:{port}（HC-06；出口记录见 network-guard.jsonl，via={via}）")

    # ------------------------------------------------------------------ patching

    def install(self) -> None:
        if self._saved:
            return
        guard = self
        original = {"getaddrinfo": socket.getaddrinfo, "create_connection": socket.create_connection,
                    "connect": socket.socket.connect, "urlopen": urllib.request.urlopen}

        def getaddrinfo(host, port, *args, **kwargs):
            if guard.record(host, port, via="getaddrinfo") == "blocked":
                raise guard.refuse(host, port, via="getaddrinfo")
            return original["getaddrinfo"](host, port, *args, **kwargs)

        def create_connection(address, *args, **kwargs):
            host, port = address[0], address[1] if len(address) > 1 else None
            if guard.record(host, port, via="create_connection") == "blocked":
                raise guard.refuse(host, port, via="create_connection")
            return original["create_connection"](address, *args, **kwargs)

        def connect(sock, address):
            host, port = None, None
            if isinstance(address, tuple) and address:
                host = address[0]
                port = address[1] if len(address) > 1 else None
            elif sock.family in (socket.AF_INET, socket.AF_INET6):
                # A bare connect on an internet socket: resolve the peer we already have.
                try:
                    host, port = sock.getpeername()[:2]
                except OSError:
                    host = address
            if host is not None and guard.record(host, port, via="socket.connect") == "blocked":
                raise guard.refuse(host, port, via="socket.connect")
            return original["connect"](sock, address)

        def urlopen(url, *args, **kwargs):
            host = getattr(url, "host", None)
            if host is None and isinstance(url, str):
                host = urllib.parse.urlparse(url).hostname
            if guard.record(host, getattr(url, "port", None), via="urlopen") == "blocked":
                raise guard.refuse(host, None, via="urlopen")
            return original["urlopen"](url, *args, **kwargs)

        socket.getaddrinfo = getaddrinfo
        socket.create_connection = create_connection
        socket.socket.connect = connect
        urllib.request.urlopen = urlopen
        self._saved = original

    def uninstall(self) -> None:
        if not self._saved:
            return
        socket.getaddrinfo = self._saved["getaddrinfo"]
        socket.create_connection = self._saved["create_connection"]
        socket.socket.connect = self._saved["connect"]
        urllib.request.urlopen = self._saved["urlopen"]
        self._saved = {}


def allowed_hosts_for(llm_mode: str, dashscope_base_url: str, provider_mode: str = "demo",
                      *, qweather_api_host: str | None = None) -> frozenset[str]:
    """AUTH-01 authorises the model endpoint; AUTH-02 authorises Live Provider hosts."""
    hosts = set()
    if llm_mode == "real":
        host = urllib.parse.urlparse(dashscope_base_url).hostname
        if host:
            hosts.add(host.lower())
    if provider_mode == "live":
        # Live Provider hosts (AUTH-02); added only when provider_mode is "live".
        hosts.update({"mcp.kiwi.com", "api.tavily.com"})
        if qweather_api_host:
            raw = qweather_api_host
            if "://" not in raw:
                raw = f"https://{raw}"
            qhost = urllib.parse.urlparse(raw).hostname or qweather_api_host
            hosts.add(qhost.lower().strip())
    return frozenset(hosts)
