// Workbench entry: sidebar navigation, role-based routing.
// The navigation only hides what a role cannot do; the server enforces every permission.

import { api, currentUserId, setCurrentUserId } from "./api.js";
import { renderOps, renderPolicies, renderPolicy } from "./admin.js";
import { renderApprovals } from "./approvals.js";
import { renderAsk, renderPrefs } from "./ask.js";
import { forgetRules, setUsers } from "./parts.js";
import { renderReports } from "./reports.js";
import { renderItinerary, renderTripCreate, renderTripDetail, renderTripList } from "./trips.js?v=4";
import { ROLE, clear, el, errorBox } from "./ui.js";

// ---- SVG icon helper (creates SVG elements via createElementNS, not innerHTML) ----

const SVG_NS = "http://www.w3.org/2000/svg";

function makeSvg(viewBox, paths) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", viewBox);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.classList.add("nav-icon");
  for (const d of paths) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
  }
  return svg;
}

const ICONS = {
  clipboard: () => makeSvg("0 0 24 24", [
    "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2",
  ]),
  check: () => makeSvg("0 0 24 24", [
    "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z",
  ]),
  chart: () => makeSvg("0 0 24 24", [
    "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z",
  ]),
  settings: () => makeSvg("0 0 24 24", [
    "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z",
    "M15 12a3 3 0 11-6 0 3 3 0 016 0z",
  ]),
};

// ---- navigation definition ----
// Each entry: [href, label, iconKey, optional badge]

const NAV = {
  employee: [
    ["#/trips", "差旅列表", "clipboard"],
    ["#/ask", "制度问答", "check"],
    ["#/prefs", "偏好", "settings"],
  ],
  manager: [
    ["#/trips", "差旅列表", "clipboard"],
    ["#/approvals", "审批中心", "check", "pending"],
    ["#/reports", "报表分析", "chart"],
    ["#/ask", "制度问答", "check"],
    ["#/prefs", "偏好", "settings"],
  ],
  admin: [
    ["#/trips", "差旅列表", "clipboard"],
    ["#/approvals", "审批中心", "check"],
    ["#/reports", "报表分析", "chart"],
    ["#/admin/policies", "制度管理", "clipboard"],
    ["#/admin/ops", "运维", "settings"],
    ["#/ask", "制度问答", "check"],
  ],
};

const HOME = { employee: "#/trips", manager: "#/approvals", admin: "#/trips" };

// [pattern, render, roles]. `roles` mirrors the server's checks so a role never sees forms it
// cannot use (AC-014); it is a convenience only, every API call is still authorized server-side.
const ANY = null;
const ROUTES = [
  [/^\/trips$/, (root, ctx, m, q) => renderTripList(root, ctx, q), ANY],
  [/^\/trips\/new$/, (root, ctx) => renderTripCreate(root, ctx), ["employee", "manager"]],
  [/^\/trips\/([^/]+)\/itinerary$/, (root, ctx, m, q) => renderItinerary(root, ctx, m[1], q), ANY],
  [/^\/trips\/([^/]+)$/, (root, ctx, m) => renderTripDetail(root, ctx, m[1]), ANY],
  [/^\/approvals$/, (root, ctx) => renderApprovals(root, ctx), ["manager"]],
  [/^\/reports$/, (root, ctx) => renderReports(root, ctx), ANY],
  [/^\/ask$/, (root, ctx) => renderAsk(root, ctx), ANY],
  [/^\/prefs$/, (root, ctx) => renderPrefs(root, ctx), ["employee", "manager"]],
  [/^\/admin\/policies$/, (root, ctx) => renderPolicies(root, ctx), ["admin"]],
  [/^\/admin\/policies\/([^/]+)$/, (root, ctx, m) => renderPolicy(root, ctx, decodeURIComponent(m[1])), ["admin"]],
  [/^\/admin\/ops$/, (root, ctx, m, q) => renderOps(root, ctx, q), ["admin"]],
];

const app = document.getElementById("app");
const ctx = { user: null, users: [], health: null, flash: null, navigate };

function navigate(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  return { path, params: new URLSearchParams(query) };
}

// ---- sidebar layout ----

function sidebar(path) {
  const items = ctx.user ? NAV[ctx.user.role] : [];

  const side = el("aside", { class: "sidebar" });

  // Brand
  const brand = el("div", { class: "sidebar-brand" });
  brand.appendChild(el("h1", null, "Travel Desk"));
  side.appendChild(brand);

  // Nav links
  const nav = el("nav", { class: "sidebar-nav" });
  for (const [href, label, iconKey, badge] of items) {
    const a = el("a", { href });
    const cur = location.hash || "#/";
    const isActive = cur === href || (!href.includes("?") && cur.split("?")[0] === href.split("?")[0]);
    if (isActive) a.classList.add("active");

    // Icon
    const iconFn = ICONS[iconKey];
    if (iconFn) a.appendChild(iconFn());

    // Label text
    a.appendChild(document.createTextNode(label));

    // Optional badge. "pending" marks the live pending-approval count; the sidebar is
    // rebuilt on every route, so the number refreshes after each decision.
    if (badge === "pending") {
      api.get("/api/approvals?status=pending").then((d) => {
        a.appendChild(el("span", { class: "nav-badge" }, String((d.approvals || []).length)));
      }).catch(() => {});
    } else if (badge) {
      a.appendChild(el("span", { class: "nav-badge" }, String(badge)));
    }

    nav.appendChild(a);
  }
  side.appendChild(nav);

  // Footer with user info card
  if (ctx.user) {
    const footer = el("div", { class: "sidebar-footer" },
      el("div", { class: "who-card" },
        el("div", { class: "who-avatar" }, ctx.user.name.charAt(0)),
        el("div", { class: "who-text" },
          el("div", { class: "who-name-row" },
            el("span", { class: "who-name" }, ctx.user.name),
            el("span", { class: "who-role" }, ROLE[ctx.user.role])),
          el("div", { class: "who-dept" }, ctx.user.department))),
      el("a", { href: "#/login", class: "switch-link" }, "切换身份"));
    side.appendChild(footer);
  }

  return side;
}

function loginView(root) {
  clear(root,
    el("div", { class: "login-wrap" },
      el("div", { class: "user-grid" }, ctx.users.map((u) => el("button", {
        type: "button", class: `user-card ${ctx.user && ctx.user.id === u.id ? "current" : ""}`,
        onclick: () => {
          setCurrentUserId(u.id);
          ctx.user = u;
          forgetRules();
          navigate(HOME[u.role]);
        },
      },
      el("span", { class: "avatar" }, u.name.charAt(0)),
      el("span", { class: "uc-name-row" },
        el("strong", null, u.name),
        el("span", { class: `role role-${u.role}` }, ROLE[u.role])),
      el("span", { class: "uc-meta" }, `${u.department} · 常驻 ${u.home_city}`),
      u.manager_id ? el("span", { class: "uc-meta" }, `直属上级 ${ctx.users.find((x) => x.id === u.manager_id)?.name || u.manager_id}`) : null)))));
}

let routing = 0;

async function route() {
  const ticket = ++routing;
  const { path, params } = parseHash();

  // Clear app and build layout
  clear(app);

  if (!ctx.user || path === "/login" || path === "/") {
    if (ctx.user && path === "/") return navigate(HOME[ctx.user.role]);
    // Login page: no sidebar, just a topbar
    const loginTopbar = el("header", { class: "topbar" },
      el("a", { class: "brand", href: "#/login" },
        el("span", { class: "brand-mark" }, "TC"),
        el("div", null, el("h1", null, "差旅合规工作台"), el("p", null, "Travel Compliance Desk"))));
    const main = el("main", { class: "shell" });
    app.appendChild(loginTopbar);
    app.appendChild(main);
    loginView(main);
    return;
  }

  // Logged-in layout: sidebar + main
  const sb = sidebar(path);
  const mainWrapper = el("div", { class: "main-area" });
  const main = el("main", { class: "shell" });
  mainWrapper.appendChild(main);
  app.appendChild(sb);
  app.appendChild(mainWrapper);

  const hit = ROUTES.find(([re]) => re.test(path));
  if (!hit) {
    clear(main, el("p", { class: "empty" }, "页面不存在。"), el("a", { href: HOME[ctx.user.role] }, "返回首页"));
    return;
  }
  if (hit[2] && !hit[2].includes(ctx.user.role)) {
    clear(main, el("div", { class: "alert alert-error", role: "alert" },
      el("strong", null, "无权访问"), ` 当前身份（${ROLE[ctx.user.role]}）不能使用此页面。`),
    el("p", { class: "muted" }, `允许的角色：${hit[2].map((r) => ROLE[r]).join("、")}。界面限制只是便利，接口权限仍由服务端判定，越权调用返回 403。`),
    el("a", { href: HOME[ctx.user.role] }, "返回首页"));
    return;
  }
  main.append(el("p", { class: "muted" }, "加载中…"));
  try {
    await hit[1](main, ctx, path.match(hit[0]), params);
  } catch (e) {
    if (ticket !== routing) return;
    if (e.status === 401) {
      setCurrentUserId(null);
      ctx.user = null;
      navigate("#/login");
      return;
    }
    clear(main, errorBox(e),
      e.status === 403 ? el("p", { class: "muted" }, `当前身份（${ROLE[ctx.user.role]}）无权执行此操作；权限由服务端判定。`) : null,
    el("a", { href: HOME[ctx.user.role] }, "返回首页"));
  }
}

async function boot() {
  clear(app, el("main", { class: "shell" }, el("p", { class: "muted" }, "正在连接服务…")));
  try {
    ctx.health = await api.get("/api/health");
  } catch {
    ctx.health = null;
  }
  try {
    ctx.users = (await api.get("/api/users")).users;
  } catch (e) {
    clear(app, el("main", { class: "shell" }, errorBox(e)));
    return;
  }
  setUsers(ctx.users);
  ctx.user = ctx.users.find((u) => u.id === currentUserId()) || null;
  if (!ctx.user) setCurrentUserId(null);
  window.addEventListener("hashchange", route);
  route();
}

boot();
