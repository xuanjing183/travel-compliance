"""AC-013 evidence: Provider degradation under fault injection (contract §6.6, §7.2).

Drives the three faults AC-013 names — transport timeout, no protocol hotel, and
weather unavailable — against a real `python -m travel_desk` subprocess in Fake
mode (no `DASHSCOPE_API_KEY`, no external network, HC-06), each over HTTP/SSE with
the S-02 input the contract prescribes, and exports the resulting API objects
(plan, cost breakdown, approval reasons, provider-call audit) so the degraded
behaviour can be read directly rather than inferred.

Per §6.6 the expected degradation is:
  * transport timeout      → transport manual_required, incomplete, total_fen=null,
                             known_subtotal_fen=60000, PLAN_INCOMPLETE, must approve,
                             hotel + weather unaffected, no fabricated transport id.
  * no protocol hotel      → hotel manual_required (Shanghai has no backup),
                             total_fen=null, known_subtotal_fen=150000 (transport
                             130000 + meal 20000), PLAN_INCOMPLETE, must approve,
                             transport + weather unaffected.
  * weather unavailable    → weather omitted and noted, planning NOT blocked, the
                             trip still auto-approves and delivers (total 190000).

The planning-layer behaviour is also covered by tests/test_planning.py; this script
is the API-result half of the AC-013 evidence (the UI half is the screenshots in
the same artifact directory).

Usage:
    uv run --offline python scripts/ac013_faults.py artifacts/acceptance/TASK-010/ac013-fault-injection

Exit code 0 only when every check passed; the evidence is written either way.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_lib import (  # noqa: E402
    REPO_ROOT,
    S02,
    Check,
    Desk,
    Server,
    cleanup,
    codes,
    temp_work,
    write_json,
)

TRANSPORT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "providers" / "transport.json"


def _plan(trip: dict[str, Any]) -> dict[str, Any]:
    return trip["plan"]


def _provider_calls(d: Desk, trip_id: str) -> dict[str, str]:
    """provider → last recorded status, from the append-only provider_call audit."""
    return {e["payload"]["provider"]: e["payload"]["status"]
            for e in d.events_of(trip_id, "provider_call")}


def _provider_call_log(d: Desk, trip_id: str) -> list[dict[str, str]]:
    return [{"provider": e["payload"]["provider"], "status": e["payload"]["status"]}
            for e in d.events_of(trip_id, "provider_call")]


def _transport_fixture_ids() -> set[str]:
    return {c["id"] for c in json.loads(TRANSPORT_FIXTURE.read_text(encoding="utf-8"))["candidates"]}


def _capture(d: Desk, trip_id: str) -> dict[str, Any]:
    """The API objects AC-013 asks to see, exactly as the service returned them."""
    trip = d.trip(trip_id)
    plan, cost = _plan(trip), trip["cost_breakdown"]
    return {
        "trip_id": trip_id,
        "status": trip["status"],
        "revision": trip["revision"],
        "plan_incomplete": plan["incomplete"],
        "plan_missing": plan["missing"],
        "transport": {"status": plan["transport"]["status"], "note": plan["transport"].get("note"),
                      "outbound_id": (plan["transport"]["outbound"] or {}).get("id"),
                      "return_id": (plan["transport"]["return"] or {}).get("id")},
        "hotel": {"status": plan["hotel"]["status"], "note": plan["hotel"].get("note"),
                  "candidate_id": (plan["hotel"]["candidate"] or {}).get("id"),
                  "candidate_source": (plan["hotel"]["candidate"] or {}).get("source")},
        "weather": {"status": plan["weather"]["status"], "note": plan["weather"].get("note"),
                    "days": plan["weather"].get("days")},
        "cost_breakdown": cost,
        "approval_reasons": codes(trip["approval_reasons"]),
        "violations": codes(trip["violations"]),
        "provider_calls": _provider_calls(d, trip_id),
        "provider_call_log": _provider_call_log(d, trip_id),
        "trajectory": d.trajectory(trip_id),
    }


def _run_fault(d: Desk, fault: str, out: Path, label: str) -> dict[str, Any]:
    """Reset, inject one fault, run the S-02 input over HTTP, capture the API result."""
    if d.reset().status_code != 200:
        raise RuntimeError(f"reset before {fault} failed")
    injected = d.fault(fault, True)
    assert injected.status_code == 200 and injected.json()["faults"][fault] is True, injected.text
    trip_id = d.create_trip()["trip"]["id"]
    d.fill(trip_id, S02)
    d.submit(trip_id)
    captured = _capture(d, trip_id)
    captured["fault"] = fault
    captured["fault_injected"] = injected.json()["faults"]
    (out / f"api-{label}.json").write_text(
        json.dumps(captured, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    d.fault(fault, False)  # leave the service clean for the next fault
    return captured


def run(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = temp_work("ac013-")
    server = Server(work, out, llm_mode="fake")
    check = Check()
    report: dict[str, Any] = {
        "scenario": "AC-013 / §6.6 Provider degradation under fault injection",
        "mode": "fake", "input": "S-02 (北京→上海 2026-10-25~26 客户拜访 budget 250000)",
        "start_command": "python -m travel_desk (= uv run travel-desk)",
        "data_dir": "<tempdir>/var", "faults": {},
    }
    try:
        server.start()
        with httpx.Client(base_url=server.base, timeout=120) as client:
            d = Desk(client, server)

            # ---- fault 1: transport timeout (S-09 behaviour) ----------------------
            t = _run_fault(d, "transport_timeout", out, "transport-timeout")
            report["faults"]["transport_timeout"] = t
            check("交通超时：交通待人工补录、方案不完整、缺 transport",
                  t["transport"]["status"] == "manual_required" and t["plan_incomplete"] is True
                  and t["plan_missing"] == ["transport"], t["transport"])
            check("交通超时：total_fen=null、known_subtotal_fen=60000（酒店40000+补贴20000）",
                  t["cost_breakdown"]["total_fen"] is None
                  and t["cost_breakdown"]["known_subtotal_fen"] == 60000
                  and t["cost_breakdown"]["transport_fen"] is None
                  and t["cost_breakdown"]["missing_components"] == ["transport"], t["cost_breakdown"])
            check("交通超时：审批理由含 PLAN_INCOMPLETE 且进入 PENDING_APPROVAL",
                  "PLAN_INCOMPLETE" in t["approval_reasons"] and t["status"] == "PENDING_APPROVAL",
                  (t["approval_reasons"], t["status"]))
            check("交通超时：酒店与天气照常（hotel_contract ok / weather ok）",
                  t["provider_calls"].get("transport") == "timeout"
                  and t["provider_calls"].get("hotel_contract") == "ok"
                  and t["provider_calls"].get("weather") == "ok"
                  and t["hotel"]["status"] == "selected" and t["weather"]["status"] == "ok",
                  t["provider_calls"])
            check("交通超时：未伪造交通 ID（outbound/return 均为空）",
                  t["transport"]["outbound_id"] is None and t["transport"]["return_id"] is None,
                  t["transport"])

            # ---- fault 2: no protocol hotel (S-02, Shanghai has no backup) --------
            h = _run_fault(d, "no_protocol_hotel", out, "no-protocol-hotel")
            report["faults"]["no_protocol_hotel"] = h
            check("无协议酒店：酒店待人工补录、候选为空、方案不完整、缺 hotel",
                  h["hotel"]["status"] == "manual_required" and h["hotel"]["candidate_id"] is None
                  and h["plan_incomplete"] is True and h["plan_missing"] == ["hotel"], h["hotel"])
            check("无协议酒店：note 说明协议酒店库不可用",
                  "协议酒店库不可用" in (h["hotel"]["note"] or ""), h["hotel"]["note"])
            check("无协议酒店：total_fen=null、known_subtotal_fen=150000（交通130000+补贴20000）",
                  h["cost_breakdown"]["total_fen"] is None
                  and h["cost_breakdown"]["known_subtotal_fen"] == 150000
                  and h["cost_breakdown"]["hotel_fen"] is None
                  and h["cost_breakdown"]["missing_components"] == ["hotel"], h["cost_breakdown"])
            check("无协议酒店：审批理由含 PLAN_INCOMPLETE 且进入 PENDING_APPROVAL",
                  "PLAN_INCOMPLETE" in h["approval_reasons"] and h["status"] == "PENDING_APPROVAL",
                  (h["approval_reasons"], h["status"]))
            check("无协议酒店：Provider 状态 transport ok / hotel_contract unavailable / "
                  "hotel_backup ok / weather ok",
                  h["provider_calls"] == {"transport": "ok", "hotel_contract": "unavailable",
                                          "hotel_backup": "ok", "weather": "ok"}, h["provider_calls"])
            check("无协议酒店：交通照常选中 B 组、天气照常",
                  h["transport"]["outbound_id"] == "T-BJS-SHA-1025-B"
                  and h["transport"]["return_id"] == "T-SHA-BJS-1026-B"
                  and h["weather"]["status"] == "ok", h["transport"])

            # ---- fault 3: weather unavailable (planning not blocked) --------------
            w = _run_fault(d, "weather_unavailable", out, "weather-unavailable")
            report["faults"]["weather_unavailable"] = w
            check("天气不可用：weather 状态 unavailable、days 为空、note 注明已省略",
                  w["weather"]["status"] == "unavailable" and w["weather"]["days"] == []
                  and "已省略" in (w["weather"]["note"] or ""), w["weather"])
            check("天气不可用：不阻断规划（方案完整、交通与酒店照常选中）",
                  w["plan_incomplete"] is False and w["plan_missing"] == []
                  and w["transport"]["status"] == "selected" and w["hotel"]["status"] == "selected",
                  (w["plan_incomplete"], w["plan_missing"]))
            check("天气不可用：S-02 同输入仍自动通过并交付（total_fen=190000、DELIVERED）",
                  w["cost_breakdown"]["total_fen"] == 190000 and w["status"] == "DELIVERED"
                  and w["approval_reasons"] == [] and w["violations"] == [],
                  (w["cost_breakdown"]["total_fen"], w["status"]))
            check("天气不可用：Provider 状态 weather unavailable，交通与酒店照常",
                  w["provider_calls"].get("weather") == "unavailable"
                  and w["provider_calls"].get("transport") == "ok"
                  and w["provider_calls"].get("hotel_contract") == "ok", w["provider_calls"])

            # ---- cross-cutting: no fabricated data anywhere -----------------------
            fixture_ids = _transport_fixture_ids()
            fabricated = [fid for f in (t, h, w) for fid in
                          (f["transport"]["outbound_id"], f["transport"]["return_id"])
                          if fid is not None and fid not in fixture_ids]
            check("三种降级均未伪造交通 ID（选中 ID 都存在于 fixture）", fabricated == [], fabricated)
            check("三种降级均未崩溃（全部产出可读的 API 结果）",
                  all(f["status"] for f in (t, h, w)), [f["status"] for f in (t, h, w)])
    except Exception as exc:  # noqa: BLE001 - record and fail, never pass silently
        check("script completed without exception", False, f"{type(exc).__name__}: {exc}")
    finally:
        server.stop()
        report["checks"] = check.items
        report["passed"] = check.passed
        write_json(out / "AC-013-faults.json", report)
        cleanup(work)
    print("AC-013", "PASSED" if check.passed else "FAILED", flush=True)
    return 0 if check.passed else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: ac013_faults.py <out_dir>")
    sys.exit(run(Path(sys.argv[1])))
