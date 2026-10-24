# Travel Compliance Desk — UI 标准

> 从六版迭代中沉淀的可复用设计系统。适用于任何**中后台 / 合规 / 审批 / 工单类**产品：白底、亮蓝主色、方角标签、折叠收纳、白话审计。

---

## 1. 设计语言

**一句话**：让制度可被确认，让例外可被追溯。

- **白底亮蓝**：`#3b5bdb` 主色，所有可操作元素（链接、按钮、选中态、tag）统一用这个色。不用渐变、不用毛玻璃、不用暗色模式。
- **方角标签 + 圆角卡片**：标签 4px、卡片 10px、按钮 6px。方角传达"这是数据/状态"，圆角传达"这是容器/操作区"。
- **折叠优先**：详情页用原生 `<details>` 折叠段，默认收起，按需展开。信息密度高但首屏干净。
- **白话审计**：操作记录用人类可读的句子（"查询去程交通 · 找到 15 个候选"），技术字段下沉到"技术详情"折叠块。
- **降级不造假**：缺失数据用 `待补录` 标签明示，不编造默认值。

---

## 2. Token 系统

### 2.1 颜色

| 语义 | 变量 | 值 | 用途 |
|---|---|---|---|
| 主色 | `--blue` | `#3b5bdb` | 链接、按钮、选中、tag-blue |
| 主色浅 3/5/7/8/9 | `--blue-3` ~ `--blue-9` | `#768ce6` → `#ebeefb` | hover、边框、背景、选中背景 |
| 主色深 | `--blue-dark` | `#2f49af` | 按钮 hover |
| 正文 | `--ink` | `#1f2328` | 正文、标题 |
| 次要 | `--muted` | `#8b949e` | 标签、元信息、空状态 |
| 分割线 | `--hair` | `#eaeef2` | 卡片边框、列表分割 |
| 背景 | `--canvas` | `#fafbfc` | 页面背景、次级背景 |
| 错误 | `--red` / `--red-bg` / `--red-line` | `#cf222e` / `#fef0f0` / `#fcdcdc` | 阻断、违规、待审批 |
| 警告 | `--orange` / `--orange-bg` / `--orange-line` / `--orange-dot` | `#b58126` / `#fdf6ec` / `#f5e3c8` / `#e6a23c` | 待补录、例外、进行中 |
| 成功 | `--green` / `--green-bg` / `--green-line` | `#2ba471` / `#e8f6ef` / `#b9e2cd` | 已交付、已通过 |

**规则**：
- 语义色只用三件套：文字色 + 背景色 + 边框色。不要发明第四种。
- `--red` 只用于**需要人处理**的状态（阻断/违规/待审批）。已解决的错误用 `--muted`。
- 状态 tag 的红色必须 `font-weight: 700`，其他颜色 400。

### 2.2 字体

| 角色 | 字体栈 | 字号 | 字重 |
|---|---|---|---|
| 正文 | `"Segoe UI", "Microsoft YaHei", system-ui, sans-serif` | 14px | 400 |
| 标题 | 同上 | 19–24px | 600–700 |
| 标签/元信息 | 同上 | 11.5–12.5px | 400 |
| 数字/金额/代码 | `"Cascadia Mono", Consolas, "Courier New", monospace` | 12–20px | 400–700 |

**规则**：
- 金额、日期、ID、版本号、JSON 一律用等宽字体 + `font-variant-numeric: tabular-nums`。
- 英文标签（REQUEST / PLAN / COST / COMPLIANCE）用 `letter-spacing: .12em; text-transform: uppercase`，11px，`--muted` 色。

### 2.3 间距

| 用途 | 值 |
|---|---|
| 页边距 | `28px 32px 96px`（上 左右 下） |
| 卡片内边距 | `18px 20px` |
| 折叠段内边距 | `2px 4px 22px` |
| 票根卡内边距 | `13px 16px` |
| 列表行内边距 | `13px 16px` |
| 元素间距 | 4 / 6 / 8 / 10 / 12 / 14 / 16 / 24（只用这些档位） |

### 2.4 圆角

| 元素 | 值 |
|---|---|
| 标签 / 徽标 | 4px |
| 按钮 / 输入框 | 6px |
| 日期 chip / 消息气泡 | 8–10px |
| 卡片 / 票根卡 / 折叠段 | 10px |
| 头像 / 状态点 | 50% |
| 胶囊 tab | 999px |

---

## 3. 组件目录

### 3.1 标签 `tag(label, tone, {size, effect})`

```
tag("待补录", "danger")        → 红底白字加粗，表示需要处理
tag("已交付", "success")       → 绿底
tag("规划中", "primary")       → 蓝底
tag("员工", "warning")         → 橙底
tag("demo_fixture", "gray")   → 灰底，用于来源/角色等中性信息
```

- **tone 映射**：`danger` = 红（需处理）、`warning` = 橙（注意）、`success` = 绿（完成）、`primary` = 蓝（进行中/中性）、`gray` = 灰（元信息）。
- **size**：`small`（默认 11.5px）/ `large`（13px，用于页头状态）。
- **effect**：`light`（默认，带背景色）/ `plain`（白底 + 有色边框，用于次要场景）。
- **禁止**：不要用文字图标（✈️🚂🏠），用内联 SVG。

### 3.2 状态标签 `statusTag(status)`

由 `ui.js` 的 `STATUS_TONE` 表驱动，自动映射：

| 状态 | tone |
|---|---|
| COLLECTING / VALIDATING / PLANNING | primary（蓝） |
| POLICY_BLOCKED / PENDING_APPROVAL / REJECTED | danger（红） |
| DELIVERY_PENDING | warning（橙） |
| AUTO_APPROVED / APPROVED / DELIVERED | success（绿） |

### 3.3 票根卡 `legCard({ico, title, tags, sub, price, unit, missing, warn})`

```
┌──────────────────────────────────────────────┐
│ [✈]  ZH9108  Shenzhen Airlines · 经济舱  例外   ¥1,070 │
│      10-22 19:00 北京 → 22:35 深圳          去程交通 · 飞机 │
└──────────────────────────────────────────────┘
```

- **图标**：`icon("train"|"house"|"plane")`，38×38 圆角方块，蓝底蓝字。缺失时红底红字 + 虚线边框。
- **主行**：车次/航班号（粗）+ 承运人 + 舱位（灰）+ 例外 tag（可选）。
- **副行**：时间 + 路线，12.5px 灰色。
- **右侧**：金额（等宽粗体）+ 单位（"去程交通 · 飞机"，11.5px 灰色）。
- **缺失态**：`missing: true` → 虚线红边框 + 红底图标 + 右侧显示 `待补录`。

### 3.4 折叠段 `disclosure(title, en, meta, body, {open, tone})`

```
申请信息  REQUEST                              已齐全  ›
─────────────────────────────────────────────────────
  （展开后的内容）
```

- **标题**：中文粗体 14.5px + 英文小写大写 11px 灰色。
- **右侧 meta**：摘要信息（"住宿 6 晚 · 天气 7 天"），12.5px 灰色；异常时红色/橙色加粗。
- **箭头**：`›` 字符，展开时旋转 90°。
- **hover**：整行背景变 `--canvas`，标题变蓝。
- **嵌套**：折叠内可用 `subBlock("技术详情", ...)` 做二级折叠。

### 3.5 规则告警 `alert(kind, title, ...children)`

```
┌──────────────────────────────────────────────┐
│ (!) 提前预订不足，待审批                      │
│ 去程 kiwi-0ddc... 提前 2 天预订，少于制度要求的 3 天 │
│ 实际 2 days · 上限 3 days                     │
│ ┌────────────────────────────────────────── │
│ │ 制度原文 · POL-5 · line:29               │ │
│ │ 机票出发日距申请日不少于 3 个自然日。      │ │
│ └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

- **kind**：`error`（红，阻断/违规）/ `warning`（橙，例外/待补录）。
- **图标**：圆形 18px，白字 `!`，背景色 = 告警色。
- **引用块**：`.ep-quote`，白底 + 灰边框，顶部小字标注来源（"制度原文 · POL-5 · line:29"）。

### 3.6 操作记录时间线 `auditTimeline(events)`

```
● 创建申请 · 新建一份出差申请
  #556 · 10/21 11:49:07 · 张三
  ▸ 技术详情

● 查询去程交通 · 找到 15 个候选
  #564 · 10/21 11:49:18 · 系统
  ▸ 技术详情
```

- **圆点颜色**：`primary`（蓝）= 常规操作、`success`（绿）= 成功完成、`warning`（橙）= 方案生成/审批发起、`danger`（红）= 合规判断/阻断。
- **主行**：白话句子（动词开头，如"查询去程交通"、"选定方案"、"发起审批"）。
- **副行**：`#seq · 时间 · 操作人`，12px 灰色。
- **技术详情**：折叠块，内含 kv（seq/revision/trace/refs）+ jsonBlock（完整 payload）。
- **规则**：主行**不出现** ID、版本号、trace 等技术字段；这些只出现在"技术详情"里。

### 3.7 来源徽标 `sourceBadge(source)`

| source | 颜色 | 背景 |
|---|---|---|
| `demo_fixture` | `#285a8a` | `#f1f6fb` |
| `backup_fixture` | `#7a5413` | `#fdf6e9` |
| `live` | `#6b2c7a` | `#faf2fc` |
| `policy` | `#2f6b47` | `#f0f8f3` |
| `model` | `#8a2f4b` | `#fcf1f5` |
| `user` | `--muted` | `#fff` |

- 等宽字体 10.5px，4px 圆角，1px 边框。
- 用于审批卡、操作记录等需要标注数据来源的场景。

### 3.8 金额格式

| 函数 | 输出 | 场景 |
|---|---|---|
| `yuan(fen)` | `¥1,050.00` | 成本明细表、预算、申报金额 |
| `yuanShort(fen)` | `¥1,050` | 页头、票根卡、列表行、告警数值 |

- 一律等宽字体 + `tabular-nums`。
- 列表行金额右对齐，下方可跟灰色单位（"起"、"10-22 ~ 10-28"）。

---

## 4. 布局

### 4.1 页面结构

```
┌─────────────────────────────────────────────┐
│ topbar（sticky, 56px）                      │
│  [TC] 差旅合规工作台    我的申请 制度问答 偏好  ● 演示日期  张三 员工 · 销售部  切换身份 │
├─────────────────────────────────────────────┤
│ .shell（max-width 960px, 居中）              │
│                                             │
│  页头（back + 状态 tag + 金额 + 路线 + 元信息） │
│                                             │
│  .secs（折叠段列表）                          │
│   ├─ 申请信息 REQUEST                        │
│   ├─ 出行方案 PLAN                           │
│   ├─ 逐日日程 ITINERARY                      │
│   ├─ 成本明细 COST                           │
│   ├─ 规则依据 COMPLIANCE                     │
│   ├─ 审批记录 APPROVAL                       │
│   └─ 操作记录 AUDIT                          │
│                                             │
─────────────────────────────────────────────┘
```

### 4.2 网格

- **双栏**：`.grid-2`，`minmax(0, 1fr) minmax(0, 1fr)`，gap 16px。≤900px 退化为单栏。
- **表单**：`.form`，两列，gap `10px 14px`。
- **天气卡**：flex wrap，`min-width: 130px; max-width: 180px`。
- **日程**：最大宽度 640px，日期 chip 固定 92px。

### 4.3 列表页

```
┌─────────────────────────────────────────────┐
│ 我的出差申请  35 份               新建申请  │
│ [待审批] [已交付] [收集信息]                  │
├─────────────────────────────────────────────┤
│ 待审批   北京 → 深圳              ¥2,624 起  │
│          张三 · 更新 10/21 11:49  10-22~10-28│
├─────────────────────────────────────────────┤
│ 已交付   北京 → 上海              ¥2,557    │
│          张三 · 更新 10/21 11:37  10-31~11-02│
└─────────────────────────────────────────────┘
```

- **行结构**：状态 tag（固定 84px）+ 路线（粗）+ 申请人·更新时间（灰）+ 金额（右对齐等宽粗体）+ 日期（灰）。
- **hover**：背景变 `--canvas`。
- **tab 过滤**：胶囊形，active 时蓝底蓝字。

---

## 5. 交互模式

### 5.1 导航

- **hash router**：`#/trips`、`#/trips/:id`、`#/approvals`、`#/ask`、`#/prefs`、`#/admin/policies`、`#/admin/ops`。
- **角色路由守卫**：`ANY` / `["employee"]` / `["manager"]` / `["admin"]`。无权时显示"无权访问"而非 404。
- **首页跳转**：employee/admin → `#/trips`，manager → `#/approvals`。

### 5.2 身份切换

- 登录页展示所有演示用户卡片（头像首字 + 姓名 + 部门/常驻/直属上级 + 角色 tag）。
- 点击即切换，`localStorage` 存 `tcd.user`，刷新保持。
- topbar 右侧"切换身份"链接回 `#/login`。

### 5.3 折叠段默认态

- **申请信息**：默认展开（非编辑态）。
- **审批记录**：审批人为本人时默认展开（保证操作可达）。
- **其他**：默认收起。
- **打印**：`.sec-body` / `.sub` / `.json` 隐藏，只打印折叠段标题。

### 5.4 加载与错误

- **加载态**：按钮 `busy(btn, fn)` 期间禁用 + `cursor: progress`。
- **错误态**：`errorBox(error)` 红色卡片，显示 `code: message`。
- **空态**：`.empty`，灰色居中文字，40px 上下间距。

---

## 6. 无障碍与动效

### 6.1 无障碍

- **图标**：`aria-hidden="true" focusable="false"`，不干扰屏幕阅读器。
- **折叠段**：原生 `<details>` + `<summary>`，键盘可操作。
- **颜色对比**：正文 `#1f2328` on `#fafbfc` = 15.4:1（AAA）；tag 文字 on 背景 ≥ 4.5:1。
- **焦点**：输入框 `outline: 2px solid var(--blue-8)`。
- **打印**：`@media print` 隐藏 topbar、技术详情、JSON。

### 6.2 动效

- **过渡**：所有 hover/active 用 `transition: .15s` 或 `.2s`。
- **折叠箭头**：`transform: rotate(90deg)`，`.2s`。
- **尊重偏好**：`@media (prefers-reduced-motion: reduce)` 全局禁用过渡。
- **禁止**：不用入场动画、不用滚动触发、不用视差。中后台产品不需要这些。

---

## 7. 命名约定

### 7.1 CSS 类名前缀

| 前缀 | 含义 | 示例 |
|---|---|---|
| `.mh-*` | 页头（meta header） | `.mh-route` `.mh-amount` |
| `.sec-*` | 折叠段 | `.sec-head` `.sec-title` `.sec-meta` `.sec-arrow` `.sec-body` |
| `.sub` | 二级折叠 | `sub > summary` |
| `.leg-*` | 票根卡 | `.leg-card` `.leg-ico` `.leg-main` `.leg-price` |
| `.w-card` | 天气卡 | `.w-card .d .t .r` |
| `.day-*` | 日程行 | `.day-row` `.day-chip` |
| `.cost` | 成本表 | `table.cost` |
| `.ep-alert` / `.ep-quote` | 规则告警/引用 | |
| `.tl-*` | 时间线 | `.tl-item` `.tl-text` `.tl-meta` |
| `.trip-row` / `.user-row` | 列表行 | |
| `.tag-*` | 标签变体 | `.tag-blue` `.tag-red` `.tag-large` `.tag-plain` |
| `.src-*` | 来源徽标 | `.src-demo_fixture` `.src-model` |
| `.ps-*` | 发布状态 | `.ps-published` `.ps-draft` `.ps-archived` |

### 7.2 JS 组件函数

| 函数 | 返回 |
|---|---|
| `el(tag, attrs, ...children)` | DOM 节点 |
| `clear(node, ...children)` | 清空并追加 |
| `tag(label, tone, opts)` | `<span class="tag tag-{tone}">` |
| `statusTag(status)` | 自动映射 tone 的 tag |
| `icon(name)` | cloneNode 的 SVG（train/house/plane） |
| `disclosure(title, en, meta, body, opts)` | `<details class="sec">` |
| `subBlock(summary, ...children)` | `<details class="sub">` |
| `notice(kind, ...children)` | `.alert.alert-{kind}` |
| `section(title, ...children)` | `.card` |
| `kv(rows)` | `<dl class="kv">` |
| `jsonBlock(value)` | `<pre class="json">` |
| `button(label, onclick, opts)` | `.btn.btn-{kind}` |
| `busy(btn, fn)` | 按钮加载态包装 |
| `errorBox(error)` | 错误卡片 |

---

## 8. 反模式（禁止项）

1. **不用 innerHTML / outerHTML / insertAdjacentHTML / document.write / eval / new Function**。所有 DOM 用 `el()` 生成。
2. **不引用外部 URL**。字体、脚本、样式、图标全部本地。HC-06 测试会扫描所有 JS + HTML + CSS。
3. **不用文字图标**（✈️🚂🏠）。用内联 SVG sprite + `cloneNode`。
4. **不在主界面暴露系统字段**。revision、trace、generator、source 徽标只出现在审批卡、操作记录、技术详情折叠块里。
5. **不编造缺失数据**。缺失用 `待补录` tag 明示，金额显示 `–` 或 `待补录后结算`。
6. **不在主行写技术细节**。操作记录主行用白话句子，ID/版本号/trace 下沉到"技术详情"。
7. **不用渐变/毛玻璃/暗色模式**。保持白底亮蓝的干净观感。
8. **不削弱测试**。HC-06 / HC-07 / 路由守卫 / 权限矩阵测试必须保持全绿。

---

## 9. 扩展指南

### 9.1 新增页面

1. 在 `app.js` 的 `ROUTES` 数组加一行 `[regex, renderFn, roles]`。
2. 在 `app.js` 的 `NAV` 数组加导航项（按角色）。
3. 在 `app.js` 的 `HOME` 对象设默认首页（按角色）。
4. 渲染函数签名：`async function renderXxx(root, ctx, params)`。
5. 用 `disclosure()` 组织内容段，用 `tag()` / `statusTag()` 标状态。

### 9.2 新增组件

1. 在 `ui.js` 加导出函数，返回 `el()` 生成的节点。
2. 在 `app.css` 加对应类名，遵循前缀约定。
3. 在 `parts.js` 或页面模块里引用。
4. 确保不引入外部依赖、不用 innerHTML。

### 9.3 新增状态

1. 在 `ui.js` 的 `STATUS` 常量加映射（英文 → 中文）。
2. 在 `STATUS_TONE` 加 tone 映射。
3. 在 `CODES` 加规则代码映射（如适用）。
4. 更新 `test_workbench.py` 的 `STATUS` 测试数据（如有）。

---

## 10. 文件清单

```
src/travel_desk/static/
├── index.html          # 入口 + 隐藏 SVG sprite（ico-train/house/plane）
├── css/
│   └── app.css         # 全部样式（~460 行）
└── js/
    ├── app.js          # 路由、topbar、登录页
    ├── api.js          # HTTP/SSE 客户端（X-Demo-User 头）
    ├── ui.js           # 基础组件（el/tag/icon/disclosure/...）
    ├── parts.js        # 共享渲染器（planView/costTable/auditTimeline/...）
    ├── trips.js        # 申请列表/详情/创建/行程单
    ├── approvals.js    # 经理审批页
    ├── ask.js          # 制度问答
    ├── admin.js        # 管理员（制度管理/运维）
    └── prefs.js        # 个人偏好
```

---

**版本**：2026-10-21（v6 落地版）
**基线**：379 项回归全绿，14 条路由零运行时错误
**约束**：HC-06（零外部 URL / 零 innerHTML）、HC-07（密钥不回显）、降级不造假
