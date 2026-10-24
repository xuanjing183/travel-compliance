"""TASK-007: the three-role workbench is served by the app itself, with no build step.

The browser loads native ES modules, so these checks stand in for a bundler: every module is
served as JavaScript (IMP-86), every named import resolves to an export of the imported module
and is actually used, and the front end never builds markup from strings or loads anything from
outside the app (HC-06). Behaviour of the pages is covered by the browser rehearsal recorded in
artifacts/acceptance/TASK-007; the server-side rules they rely on are covered by test_api.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from travel_desk.config import REPO_ROOT

STATIC = REPO_ROOT / "src" / "travel_desk" / "static"
JS_DIR = STATIC / "js"
MODULES = sorted(JS_DIR.glob("*.js"))

IMPORT_RE = re.compile(r'^import\s*\{([^}]*)\}\s*from\s*"(\./[\w-]+\.js)";', re.M | re.S)
EXPORT_DECL_RE = re.compile(r"^export\s+(?:async\s+)?(?:function|const|let|class)\s+(\w+)", re.M)
EXPORT_LIST_RE = re.compile(r"^export\s*\{([^}]*)\}\s*;", re.M)


def _names(group: str) -> list[str]:
    return [n.strip() for n in group.split(",") if n.strip()]


def _exports(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    names = set(EXPORT_DECL_RE.findall(text))
    for group in EXPORT_LIST_RE.findall(text):
        names.update(_names(group))
    return names


def test_module_set_is_what_the_shell_expects():
    assert {p.name for p in MODULES} == {
        "admin.js", "api.js", "app.js", "approvals.js", "ask.js", "parts.js",
        "reports.js", "trips.js", "ui.js"}


def test_index_and_assets_are_served_with_browser_usable_types(client):
    index = client.get("/")
    assert index.status_code == 200 and index.headers["content-type"].startswith("text/html")
    assert '<div id="app"' in index.text
    assert 'src="/static/js/app.js' in index.text
    assert 'href="/static/css/app.css' in index.text

    for module in MODULES:
        res = client.get(f"/static/js/{module.name}")
        assert res.status_code == 200, module.name
        # Browsers refuse to execute a module served as text/plain (IMP-86).
        assert res.headers["content-type"].split(";")[0] == "text/javascript", module.name
        assert res.content == module.read_bytes()

    css = client.get("/static/css/app.css")
    assert css.status_code == 200 and css.headers["content-type"].split(";")[0] == "text/css"


def test_shell_and_assets_are_revalidated_so_updates_take_effect(client):
    # No versioned file names: without no-cache a browser may keep running an old module (IMP-94).
    assert client.get("/").headers["cache-control"] == "no-cache"  # 14-line shell, always re-sent
    for path in ("/static/js/app.js", "/static/js/parts.js", "/static/css/app.css"):
        res = client.get(path)
        assert res.headers["cache-control"] == "no-cache", path
        again = client.get(path, headers={"If-None-Match": res.headers["etag"]})
        assert again.status_code == 304, path  # unchanged files cost a round trip, not a download
    assert "cache-control" not in client.get("/api/health").headers


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_named_imports_resolve_and_are_used(module: Path):
    text = module.read_text(encoding="utf-8")
    body = IMPORT_RE.sub("", text)
    for group, target in IMPORT_RE.findall(text):
        target_path = JS_DIR / target[2:]
        assert target_path.is_file(), f"{module.name} imports missing {target}"
        exported = _exports(target_path)
        for name in _names(group):
            assert name in exported, f"{module.name}: {name} is not exported by {target}"
            assert re.search(rf"\b{re.escape(name)}\b", body), f"{module.name}: {name} imported but unused"


def test_every_module_is_reachable_from_the_entry_point():
    seen, todo = set(), ["app.js"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(t[2:] for _, t in IMPORT_RE.findall((JS_DIR / name).read_text(encoding="utf-8")))
    assert seen == {p.name for p in MODULES}


def test_front_end_never_parses_markup_or_loads_external_resources():
    files = [*MODULES, STATIC / "index.html", STATIC / "css" / "app.css"]
    # SVG/XML namespace URIs are standard constants, not external resource loads.
    namespace_exceptions = re.compile(r"https?://www\.w3\.org/")
    for path in files:
        code = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                         if not line.lstrip().startswith("//"))
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert banned not in code, f"{path.name} uses {banned}"
        # HC-06: no CDN fonts, scripts or styles; everything comes from this app.
        for match in re.finditer(r"(?:https?:)?//[\w.-]+\.\w{2,}/", code):
            assert namespace_exceptions.match(match.group()), f"{path.name} references an external URL: {match.group()}"


def test_identity_header_is_the_only_credential_the_client_sends():
    api_js = (JS_DIR / "api.js").read_text(encoding="utf-8")
    assert "X-Demo-User" in api_js
    for banned in ("Authorization", "DASHSCOPE", "api_key", "apiKey"):
        assert banned not in api_js


ROUTE_RE = re.compile(r"^[ \t]*\[/\^(?P<path>.+?)\$/,.*,[ \t]*(?P<roles>ANY|\[[^\]\n]*\])\],[ \t]*$", re.M)
ROLE_OF = {"u-zhangsan": "employee", "u-lisi": "manager", "u-wangwu": "admin"}

# One representative server call per guarded page: the page's role list (AC-014) must admit
# exactly the roles the server admits, so the UI neither hides a usable page nor shows a dead one.
PROBES = {
    r"\/trips\/new": ("post", "/api/trips", {}),
    r"\/approvals": ("get", "/api/approvals?status=pending", None),
    r"\/prefs": ("get", "/api/users/{user}/preferences", None),
    r"\/admin\/policies": ("post", "/api/policies/v1.2/extract", None),
    r"\/admin\/policies\/([^/]+)": ("post", "/api/policies/v1.2/extract", None),
    r"\/admin\/ops": ("get", "/api/outbox", None),
}


def _route_roles() -> dict[str, list[str] | None]:
    text = (JS_DIR / "app.js").read_text(encoding="utf-8")
    table = {}
    for m in ROUTE_RE.finditer(text):
        roles = m["roles"]
        table[m["path"]] = None if roles == "ANY" else re.findall(r'"(\w+)"', roles)
    return table


def test_page_role_guards_match_the_server(client):
    table = _route_roles()
    assert len(table) == 11, table
    guarded = {path for path, roles in table.items() if roles is not None}
    assert guarded == set(PROBES)
    for path, (method, url, body) in PROBES.items():
        for user, role in ROLE_OF.items():
            kwargs = {"headers": {"X-Demo-User": user}}
            if body is not None:
                kwargs["json"] = body
            res = getattr(client, method)(url.format(user=user), **kwargs)
            allowed_by_server = res.status_code != 403
            assert allowed_by_server == (role in table[path]), (path, user, res.status_code, res.text)
