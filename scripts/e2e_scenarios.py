"""Deterministic end-to-end scenario runner: S-01–S-12 (contract §7.2).

Used for AC-021 (the whole run happens inside a real server process whose egress
guard records every outbound attempt) and reusable for AC-019 (each scenario runs
`--runs` times and the report keeps the state trajectory of every run).

The service is started with the documented command (`python -m travel_desk`) on a
throwaway data directory in Fake mode with a fixed `DEMO_TODAY`, and driven over
HTTP/SSE. `POST /api/demo/reset` separates the runs. No model cost, no external
network (HC-06): `DASHSCOPE_API_KEY` is removed from the child environment.

    uv run --offline python scripts/e2e_scenarios.py <out_dir> [--runs 3] [--mode fake]
                                                   [--only S-01,S-09] [--keep]

Exit code 0 only when every check of every run passed; the report is written
either way.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_lib import (  # noqa: E402
    REPO_ROOT,
    S01,
    S02,
    S03,
    S04,
    S06,
    S06_FIXED,
    WANG,
    ZHANG,
    Check,
    Desk,
    Server,
    cleanup,
    codes,
    now,
    read_jsonl,
    temp_work,
    write_json,
)

CLOSED_IDS = ["RULE-CITY-001", "RULE-HOTEL-001", "RULE-HOTEL-002", "RULE-MEAL-001", "RULE-CLASS-001",
              "RULE-ADVANCE-001", "RULE-AUTO-001", "RULE-DURATION-001"]
INVALID_ID, UNSUPPORTED_ID = "RULE-DURATION-002", "RULE-OTHER-001"
V13_HOTEL_QUOTE = "员工住宿上限：A 类城市每晚上限 450 元；B 类城市每晚上限 400 元；C 类城市每晚上限 300 元。"
# S-11 判定口径（§7.2）：事由含“供应商大会”，地址去空白后同时含这两个片段。
ADDRESS_PARTS = ("滨江大道2727号", "星衡国际会议中心")


def _plan(trip: dict[str, Any]) -> dict[str, Any]:
    return trip["plan"]


def _selected_ids(trip: dict[str, Any]) -> dict[str, Any]:
    plan = _plan(trip)
    transport, hotel = plan["transport"], plan["hotel"]
    # Plan JSON is serialized by alias, so the return leg's key is "return" (§6.3).
    return {"outbound": (transport["outbound"] or {}).get("id"),
            "return": (transport["return"] or {}).get("id"),
            "hotel": (hotel["candidate"] or {}).get("id")}


def _pending_card(d: Desk, trip_id: str, revision: int) -> dict[str, Any]:
    cards = [a for a in d.pending_approvals() if a["trip_id"] == trip_id and a["revision"] == revision]
    return cards[0]["card"] if cards else {}


# ---------------------------------------------------------------------- scenarios


def s01(d: Desk, c: Check) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S01)
    events = d.submit(trip_id)
    trip = d.trip(trip_id)
    c("状态 PENDING_APPROVAL", trip["status"] == "PENDING_APPROVAL", trip["status"])
    c("选中交通与酒店（02 满房被过滤）", _selected_ids(trip) == {
        "outbound": "T-BJS-SHA-1028-A", "return": "T-SHA-BJS-1030-A", "hotel": "H-SHA-CONTRACT-01"},
        _selected_ids(trip))
    cost = trip["cost_breakdown"]
    c("晚数2、天数3", (cost["nights"], cost["days"]) == (2, 3), cost)
    c("交通350000、酒店96000、补贴30000、总额476000",
      (cost["transport_fen"], cost["hotel_fen"], cost["meal_fen"], cost["total_fen"])
      == (350000, 96000, 30000, 476000), cost)
    c("无制度违规", trip["violations"] == [], trip["violations"])
    c("审批理由含 BUDGET_EXCEEDED 与 OVER_AUTO_APPROVAL_LIMIT",
      codes(trip["approval_reasons"]) == ["BUDGET_EXCEEDED", "OVER_AUTO_APPROVAL_LIMIT"],
      codes(trip["approval_reasons"]))
    c("超预算 76000 分", cost["budget_delta_fen"] == 76000, cost["budget_delta_fen"])
    card = _pending_card(d, trip_id, 1)
    c("审批卡在经理待办中且字段齐全", bool(card) and card["revision"] == 1
      and card["cost_breakdown"]["total_fen"] == 476000 and card["budget_delta_fen"] == 76000
      and card["policy_version"] == "v1.2" and card["sources"], list(card)[:6])
    evaluated = d.events_of(trip_id, "rules_evaluated")
    c("审计中的规则结果带条款引用与来源", len(evaluated) == 1
      and {r["clause"] for r in evaluated[0]["refs"]} >= {"POL-6"}
      and evaluated[0]["payload"]["source"] == "policy", evaluated[0]["refs"] if evaluated else None)
    approved = d.decide(trip_id, 1, "approve")
    c("李四批准后 DELIVERED", approved.status_code == 200
      and approved.json()["trip"]["status"] == "DELIVERED", approved.status_code)
    c("恰好 1 封 trip_delivery", [e["kind"] for e in d.outbox(trip_id)] == ["trip_delivery"],
      [e["kind"] for e in d.outbox(trip_id)])
    itinerary = d.get(f"/api/trips/{trip_id}/itinerary", user=ZHANG)
    c("行程单已生成", itinerary.status_code == 200
      and bool(itinerary.json()["itinerary"]["plan"]["itinerary"]), itinerary.status_code)
    return {"trip_id": trip_id, "sse": [e["type"] for e in events]}


def s02(d: Desk, c: Check) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S02)
    d.submit(trip_id)
    trip = d.trip(trip_id)
    c("选中 B 组交通与 H-SHA-CONTRACT-02", _selected_ids(trip) == {
        "outbound": "T-BJS-SHA-1025-B", "return": "T-SHA-BJS-1026-B", "hotel": "H-SHA-CONTRACT-02"},
        _selected_ids(trip))
    c("晚数1、天数2、总额190000", (trip["cost_breakdown"]["nights"], trip["cost_breakdown"]["days"],
                                trip["cost_breakdown"]["total_fen"]) == (1, 2, 190000),
      trip["cost_breakdown"])
    c("无违规、无审批理由", trip["violations"] == [] and trip["approval_reasons"] == [], trip["violations"])
    c("AUTO_APPROVED→DELIVERED", trip["status"] == "DELIVERED", trip["status"])
    c("没有审批卡", trip["current_approval"] is None and d.pending_approvals() == [],
      d.pending_approvals())
    c("恰好 1 封 trip_delivery", [e["kind"] for e in d.outbox(trip_id)] == ["trip_delivery"],
      [e["kind"] for e in d.outbox(trip_id)])
    return {"trip_id": trip_id}


def s03(d: Desk, c: Check) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S03)
    d.submit(trip_id)
    trip = d.trip(trip_id)
    c("成都无协议候选，使用备用酒店 H-CTU-BACKUP-01", _selected_ids(trip)["hotel"] == "H-CTU-BACKUP-01",
      _selected_ids(trip))
    c("备用酒店来源 backup_fixture", _plan(trip)["hotel"]["candidate"]["source"] == "backup_fixture",
      _plan(trip)["hotel"]["candidate"]["source"])
    (violation,) = trip["violations"]
    c("HOTEL_OVER_CAP 45000>40000（RULE-HOTEL-001 / POL-2 / policy）",
      (violation["code"], violation["actual_value"], violation["limit_value"], violation["rule_id"],
       violation["clause"], violation["source"]) == ("HOTEL_OVER_CAP", 45000, 40000, "RULE-HOTEL-001",
                                                     "POL-2", "policy"), violation)
    c("总额274000", trip["cost_breakdown"]["total_fen"] == 274000, trip["cost_breakdown"])
    c("PENDING_APPROVAL", trip["status"] == "PENDING_APPROVAL", trip["status"])
    approved = d.decide(trip_id, 1, "approve")
    c("批准后 DELIVERED", approved.status_code == 200
      and approved.json()["trip"]["status"] == "DELIVERED", approved.status_code)
    return {"trip_id": trip_id}


def s04(d: Desk, c: Check) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S04)
    d.submit(trip_id)
    trip = d.trip(trip_id)
    (violation,) = trip["violations"]
    c("ADVANCE_BOOKING_VIOLATION（提前2天<3天）",
      (violation["code"], violation["actual_value"], violation["limit_value"])
      == ("ADVANCE_BOOKING_VIOLATION", 2, 3), violation)
    c("总额180000", trip["cost_breakdown"]["total_fen"] == 180000, trip["cost_breakdown"])
    c("进入审批而非硬阻断", trip["status"] == "PENDING_APPROVAL" and trip["blocks"] == [], trip["status"])
    return {"trip_id": trip_id}


def s06(d: Desk, c: Check) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S06)
    events = d.submit(trip_id)
    trip = d.trip(trip_id)
    c("POLICY_BLOCKED", trip["status"] == "POLICY_BLOCKED", trip["status"])
    c("审计记录阻断原因", bool(trip["blocks"]) and bool(d.events_of(trip_id, "validation_blocked")),
      trip["blocks"])
    calls = d.events_of(trip_id, "provider_call")
    c("未调用交通/酒店/天气 Provider", calls == [], [x["payload"]["provider"] for x in calls])
    fixed = d.fill(trip_id, {"start_date": S06_FIXED["start_date"], "end_date": S06_FIXED["end_date"]})
    c("员工修改后回到 COLLECTING 且 revision 不变",
      (fixed["status"], fixed["revision"]) == ("COLLECTING", 1), (fixed["status"], fixed["revision"]))
    d.submit(trip_id)
    after = d.trip(trip_id)
    c("修改后的日期可以走完规划（与 S-02 同额）",
      after["status"] == "DELIVERED" and after["cost_breakdown"]["total_fen"] == 190000, after["status"])
    return {"trip_id": trip_id, "sse": [e["type"] for e in events]}


def s07(d: Desk, c: Check) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S01)
    d.submit(trip_id)
    rejected = d.decide(trip_id, 1, "reject", comment="预算过高，请改为线上参会")
    c("驳回成功", rejected.status_code == 200, rejected.status_code)
    trip = d.trip(trip_id)
    c("REJECTED（终态）", trip["status"] == "REJECTED", trip["status"])
    emails = d.outbox(trip_id)
    c("无 trip_delivery，恰好 1 封 workflow_notification",
      [e["kind"] for e in emails] == ["workflow_notification"], [e["kind"] for e in emails])
    again = d.decide(trip_id, 1, "approve")
    c("再次审批返回 409", again.status_code == 409, again.status_code)
    return {"trip_id": trip_id, "second_decision_code": again.json()["error"]["code"]}


def s08(d: Desk, c: Check) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S01)
    d.submit(trip_id)
    first = d.decide(trip_id, 1, "request_changes", comment="请补充会议议程后再提交")
    c("要求修改成功", first.status_code == 200, first.status_code)
    trip = d.trip(trip_id)
    c("revision=2 且回到 COLLECTING", (trip["revision"], trip["status"]) == (2, "COLLECTING"),
      (trip["revision"], trip["status"]))
    c("旧审批失效", trip["current_approval"] is None
      and bool(d.events_of(trip_id, "approval_superseded")),
      [(e["revision"], e["payload"].get("status")) for e in d.events_of(trip_id, "approval_superseded")])
    d.fill(trip_id, {"declared_budget_fen": 500000}, revision=2)
    d.submit(trip_id, revision=2)
    trip = d.trip(trip_id)
    c("重新提交后再次 PENDING_APPROVAL", trip["status"] == "PENDING_APPROVAL", trip["status"])
    c("总额仍 476000 且超过自动阈值", trip["cost_breakdown"]["total_fen"] == 476000
      and "OVER_AUTO_APPROVAL_LIMIT" in codes(trip["approval_reasons"]), trip["approval_reasons"])
    c("新预算已生效（不再超预算）", trip["cost_breakdown"]["declared_budget_fen"] == 500000
      and "BUDGET_EXCEEDED" not in codes(trip["approval_reasons"]), trip["cost_breakdown"])
    approved = d.decide(trip_id, 2, "approve")
    c("第二次批准后交付", approved.status_code == 200
      and approved.json()["trip"]["status"] == "DELIVERED", approved.status_code)
    decisions = d.events_of(trip_id, "decision_recorded")
    c("两次决定均可审计", [(e["revision"], e["payload"]["decision"]) for e in decisions]
      == [(1, "request_changes"), (2, "approve")], [(e["revision"], e["payload"]) for e in decisions])
    superseded = [e["payload"] for e in d.events_of(trip_id, "approval_superseded")]
    plan_revisions = [e["revision"] for e in d.events_of(trip_id, "plan_created")]
    card_revisions = [e["revision"] for e in d.events_of(trip_id, "approval_requested")]
    c("旧方案与旧审批卡保留在审计中", bool(superseded) and plan_revisions == [1, 2]
      and card_revisions == [1, 2],
      {"superseded": superseded, "plan_revisions": plan_revisions, "card_revisions": card_revisions})
    kinds = [e["kind"] for e in d.outbox(trip_id)]
    c("1 封流程通知 + 1 封交付邮件", sorted(kinds) == ["trip_delivery", "workflow_notification"], kinds)
    return {"trip_id": trip_id}


def s09(d: Desk, c: Check) -> dict[str, Any]:
    fault = d.fault("transport_timeout", True)
    c("管理员可注入交通超时", fault.status_code == 200
      and fault.json()["faults"]["transport_timeout"] is True, fault.json()["faults"])
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S02)
    d.submit(trip_id)
    trip = d.trip(trip_id)
    plan, cost = _plan(trip), trip["cost_breakdown"]
    c("不崩溃，交通待人工补录", plan["incomplete"] is True and plan["missing"] == ["transport"]
      and plan["transport"]["status"] == "manual_required", plan["transport"])
    c("total_fen=null，known_subtotal_fen=60000（酒店40000+补贴20000）",
      cost["total_fen"] is None and cost["known_subtotal_fen"] == 60000
      and cost["transport_fen"] is None and cost["missing_components"] == ["transport"], cost)
    c("审批理由含 PLAN_INCOMPLETE", "PLAN_INCOMPLETE" in codes(trip["approval_reasons"]),
      codes(trip["approval_reasons"]))
    c("进入 PENDING_APPROVAL（S-02 同输入本应自动通过）", trip["status"] == "PENDING_APPROVAL",
      trip["status"])
    calls = {e["payload"]["provider"]: e["payload"]["status"] for e in d.events_of(trip_id, "provider_call")}
    c("交通超时、酒店与天气照常", calls.get("transport") == "timeout"
      and calls.get("hotel_contract") == "ok" and calls.get("weather") == "ok", calls)
    c("天气与酒店未降级", plan["weather"]["status"] == "ok" and plan["hotel"]["status"] == "selected",
      (plan["weather"]["status"], plan["hotel"]["status"]))
    selected = _selected_ids(trip)
    used = {selected["outbound"], selected["return"]} - {None}
    fixture_ids = {candidate["id"] for candidate in json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "providers" / "transport.json")
        .read_text(encoding="utf-8"))["candidates"]}
    c("没有伪造交通 ID/价格", plan["transport"]["outbound"] is None
      and plan["transport"]["return"] is None and not (used - fixture_ids), sorted(used))
    d.fault("transport_timeout", False)
    return {"trip_id": trip_id, "provider_calls": calls}


def s10(d: Desk, c: Check, server: Server, client_factory: Callable[[], httpx.Client]) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S01)
    d.submit(trip_id)
    before = d.trip(trip_id)
    c("重启前 PENDING_APPROVAL", before["status"] == "PENDING_APPROVAL", before["status"])
    thread_id = before["thread_id"]
    kill = server.hard_kill()
    c("进程被真实终止", kill["port_closed_after_kill"], kill)
    boot = server.start()
    c("重启日志显示启动恢复", "startup recovery" in server.boot_log(boot["boot"]), boot)
    with client_factory() as restarted:
        d2 = Desk(restarted, server)
        trip = d2.trip(trip_id)
        c("Trip/revision 未丢失", (trip["status"], trip["revision"], trip["thread_id"])
          == ("PENDING_APPROVAL", 1, thread_id), (trip["status"], trip["thread_id"]))
        c("审批待办未丢失", [a["trip_id"] for a in d2.pending_approvals()] == [trip_id],
          d2.pending_approvals())
        approved = d2.decide(trip_id, 1, "approve")
        c("重启后批准成功", approved.status_code == 200, approved.status_code)
        c("从审批后节点恢复（via=graph）",
          [e["payload"].get("via") for e in d2.events_of(trip_id, "decision_recorded")] == ["graph"],
          d2.events_of(trip_id, "decision_recorded"))
        c("只交付一次", [e["kind"] for e in d2.outbox(trip_id)] == ["trip_delivery"],
          [e["kind"] for e in d2.outbox(trip_id)])
        c("最终 DELIVERED", d2.trip(trip_id)["status"] == "DELIVERED", d2.trip(trip_id)["status"])
    return {"trip_id": trip_id, "kill": kill, "boot": boot}


def s11(d: Desk, c: Check) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    uploaded = d.upload_image()
    extracted = d.extract_image(trip_id, uploaded["id"])
    c("抽取成功且标记为模型来源", extracted.status_code == 201
      and extracted.json()["source"] == "model", extracted.status_code)
    proposal = extracted.json()
    fields = proposal["fields"]
    address = re.sub(r"\s+", "", fields.get("meeting_address") or "")
    c("抽取上海、2026-10-28～10-30、供应商大会与会议地址",
      (fields.get("destination"), fields.get("start_date"), fields.get("end_date")) ==
      ("上海", "2026-10-28", "2026-10-30") and "供应商大会" in (fields.get("purpose") or "")
      and all(part in address for part in ADDRESS_PARTS), fields)
    untouched = d.trip(trip_id)
    c("确认前不修改 Trip", untouched["slots"]["destination"] is None
      and d.events_of(trip_id, "slots_changed") == [], untouched["slots"])
    confirmed = d.confirm_image(trip_id, proposal["id"], fields)
    c("确认后写入槽位", confirmed.status_code == 200
      and confirmed.json()["slots"]["destination"] == "上海", confirmed.status_code)
    change = d.events_of(trip_id, "slots_changed")
    c("写入已审计且来源为 image", len(change) == 1 and change[0]["payload"]["origin"] == "image"
      and bool(d.events_of(trip_id, "image_confirmed")), change[0]["payload"] if change else None)
    again = d.confirm_image(trip_id, proposal["id"], fields)
    c("重复确认返回 409", again.status_code == 409, again.status_code)
    return {"trip_id": trip_id, "upload_sha256": uploaded["sha256"], "fields": fields}


def s12(d: Desk, c: Check) -> dict[str, Any]:
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S01)
    d.submit(trip_id)
    before = d.trip(trip_id)
    c("待审批且绑定 v1.2", (before["status"], before["policy_version"]) == ("PENDING_APPROVAL", "v1.2"),
      (before["status"], before["policy_version"]))
    c("尚无酒店违规", codes(before["violations"]) == [], codes(before["violations"]))

    uploaded = d.upload_policy("v1.3", "v1.3.md")
    c("上传 v1.3", uploaded.status_code == 201, uploaded.status_code)
    candidates = {item["id"]: item for item in d.extract_policy("v1.3").json()["candidates"]}
    c("抽取出的住宿上限为 45000", candidates["RULE-HOTEL-001"]["value"]["caps_fen"]["A"] == 45000,
      candidates["RULE-HOTEL-001"]["value"])
    c("无效引用与不支持类型已被标记", candidates[INVALID_ID]["quote_status"] == "invalid_quote"
      and candidates[UNSUPPORTED_ID]["status"] == "unsupported",
      (candidates[INVALID_ID]["quote_status"], candidates[UNSUPPORTED_ID]["status"]))
    review = d.review_policy("v1.3", CLOSED_IDS, [INVALID_ID])
    c("候选全部处理完成", all(code == 200 for code in review), review)
    published = d.publish_policy("v1.3")
    c("发布 v1.3 并归档 v1.2", published.status_code == 200
      and published.json()["archived_version"] == "v1.2", published.status_code)

    notice = d.trip(trip_id)["policy_change_notice"]
    c("提示制度已更新", notice == {"bound_version": "v1.2", "published_version": "v1.3"}, notice)
    calls_before = d.events_of(trip_id, "provider_call")
    revalidated = d.revalidate(trip_id, 1)
    c("重新校验成功", revalidated.status_code == 200, revalidated.status_code)
    trip = revalidated.json()["trip"]
    c("revision+1 且绑定 v1.3", (trip["revision"], trip["policy_version"], trip["status"])
      == (2, "v1.3", "PENDING_APPROVAL"), (trip["revision"], trip["policy_version"], trip["status"]))
    (hotel,) = [v for v in trip["violations"] if v["code"] == "HOTEL_OVER_CAP"]
    c("新增 HOTEL_OVER_CAP 48000>45000", (hotel["actual_value"], hotel["limit_value"], hotel["quote"],
                                       hotel["clause"]) == (48000, 45000, V13_HOTEL_QUOTE, "POL-2"), hotel)
    c("旧审批失效", trip["current_approval"]["revision"] == 2
      and bool(d.events_of(trip_id, "approval_superseded")), trip["current_approval"]["revision"])
    evaluated = d.events_of(trip_id, "rules_evaluated")
    judged = [e["payload"]["policy_version"] for e in evaluated]
    c("审计保留 v1.2 与 v1.3 两次结果", judged == ["v1.2", "v1.3"], judged)
    calls = d.events_of(trip_id, "provider_call")
    c("重新校验复用候选，未再次调用 Provider（D-12）", len(calls) == len(calls_before),
      (len(calls_before), len(calls)))
    return {"trip_id": trip_id}


SCENARIOS: dict[str, Callable[..., dict[str, Any]]] = {
    "S-01": s01, "S-02": s02, "S-03": s03, "S-04": s04, "S-06": s06, "S-07": s07, "S-08": s08,
    "S-09": s09, "S-11": s11, "S-12": s12,
}
NEEDS_SERVER = {"S-10": s10}
ORDER = ["S-01", "S-02", "S-03", "S-04", "S-05", "S-06", "S-07", "S-08", "S-09", "S-10", "S-11", "S-12"]


def s05_conversation(d: Desk, c: Check) -> dict[str, Any]:
    """S-05 lives here rather than in the table: it is driven by conversation, not by slots."""
    created = d.create_trip(text="下周去杭州")
    trip_id = created["trip"]["id"]
    first = created["assistant"]
    trip = d.trip(trip_id)
    c("识别 destination=杭州", trip["slots"]["destination"] == "杭州", trip["slots"])
    c("不把“下周”猜成具体日期", trip["slots"]["start_date"] is None and trip["slots"]["end_date"] is None,
      trip["slots"])
    c("只追问缺失的 5 项", trip["missing_fields"] == ["origin", "start_date", "end_date", "purpose",
                                                  "declared_budget_fen"], trip["missing_fields"])
    ask = first.get("text", "") if isinstance(first, dict) else ""
    c("追问覆盖 5 个缺失项", all(label in ask for label in
                            ("出发城市", "出发日期", "返回日期", "出差事由", "申报预算")), ask)
    tail = ask.split("还需要")[-1]
    c("一次回复不重复询问杭州", "杭州" not in tail and "目的城市" not in tail, tail)
    events = d.message(trip_id, "从北京出发，10月28日到10月30日，参加供应商大会，预算四千元")
    after = d.trip(trip_id)
    c("补齐后不再追问", after["missing_fields"] == [] and after["invalid_fields"] == [],
      (after["missing_fields"], after["invalid_fields"]))
    asked = [e["payload"]["missing_fields"] for e in d.events_of(trip_id, "message_processed")]
    c("两轮追问已审计", asked == [["origin", "start_date", "end_date", "purpose", "declared_budget_fen"],
                              []], asked)
    return {"trip_id": trip_id, "sse": [e["type"] for e in events], "ask": ask}


# ---------------------------------------------------------------------- runner


def run(out: Path, runs: int, mode: str, only: list[str] | None, keep: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = temp_work("e2e-")
    server = Server(work, out, llm_mode=mode)
    wanted = [s for s in ORDER if not only or s in only]
    report: dict[str, Any] = {
        "generated_at": now(), "mode": mode, "runs_requested": runs, "scenarios": wanted,
        "start_command": "python -m travel_desk (= uv run travel-desk)",
        "data_dir": "<tempdir>/var", "demo_today": "2026-10-20（固定时钟）",
        "results": [], "passed": False,
    }
    check = Check()

    def connect() -> httpx.Client:
        return httpx.Client(base_url=server.base, timeout=120)

    try:
        boot = server.start()
        report["boot"] = boot
        with connect() as client:
            if Desk(client, server).reset().status_code != 200:
                raise RuntimeError("initial demo reset failed")
        for iteration in range(1, runs + 1):
            for name in wanted:
                egress_before = len(read_jsonl(server.egress_log))
                started = time.perf_counter()
                scenario = Check(echo=False)
                detail: dict[str, Any] = {}
                error = None
                # One client per scenario: S-10 kills and restarts the service, which would
                # leave a pooled connection pointing at a dead process.
                with connect() as client:
                    d = Desk(client, server)
                    try:
                        if d.reset().status_code != 200:
                            raise RuntimeError("demo reset between runs failed")
                        if name == "S-05":
                            detail = s05_conversation(d, scenario)
                        elif name == "S-10":
                            detail = s10(d, scenario, server, connect)
                        else:
                            detail = SCENARIOS[name](d, scenario)
                        if "trip_id" in detail:
                            detail["trajectory"] = d.trajectory(detail["trip_id"])
                    except Exception as exc:  # noqa: BLE001 - one scenario must not abort the run
                        error = f"{type(exc).__name__}: {exc}"
                        scenario("场景未抛出异常", False, error)
                egress = read_jsonl(server.egress_log)[egress_before:]
                passed = scenario.passed and error is None
                for item in scenario.items:
                    check(f"{name} #{iteration} {item['check']}", item["passed"], item["observed"])
                report["results"].append({
                    "scenario": name, "run": iteration, "passed": passed, "error": error,
                    "duration_s": round(time.perf_counter() - started, 2),
                    "trajectory": detail.get("trajectory", []), "detail": detail,
                    "checks": scenario.items,
                    "egress_attempts": len(egress),
                    "egress_external": [e for e in egress if e["decision"] != "local"],
                })
                print(f"{'PASS' if passed else 'FAIL'} {name} #{iteration} "
                      f"({len(scenario.items)} checks, {report['results'][-1]['duration_s']}s)",
                      flush=True)
        report["egress"] = {"log": str(server.egress_log), "attempts": read_jsonl(server.egress_log)}
        health = None
        with connect() as tail:
            response = tail.get("/api/health/providers", headers=Desk.headers(WANG))
            health = response.json() if response.status_code == 200 else response.text
        report["provider_health"] = health
        report["boot_logs"] = {f"boot-{i}": server.boot_log(i)[-4000:] for i in range(1, server.boots + 1)}
    except Exception as exc:  # noqa: BLE001
        check("运行未中断", False, f"{type(exc).__name__}: {exc}")
    finally:
        server.stop()
        if server.egress_log.is_file():
            shutil.copyfile(server.egress_log, out / "network-guard.jsonl")
        report["checks"] = check.items
        report["passed"] = check.passed
        write_json(out / "e2e-report.json", report)
        if not keep:
            cleanup(work)
    total = len(report["results"])
    good = len([r for r in report["results"] if r["passed"]])
    print(f"scenarios {good}/{total} passed; checks {len([i for i in check.items if i['passed']])}"
          f"/{len(check.items)}", flush=True)
    return 0 if check.passed and good == total else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--mode", choices=("fake", "recorded", "real"), default="fake")
    parser.add_argument("--only", default=None, help="comma separated scenario ids, e.g. S-01,S-09")
    parser.add_argument("--keep", action="store_true", help="keep the temporary data directory")
    args = parser.parse_args(argv)
    only = [item.strip() for item in args.only.split(",")] if args.only else None
    return run(args.out_dir, args.runs, args.mode, only, args.keep)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
