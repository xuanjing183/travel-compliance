"""Golden-path timing harness (AC-020, SHOULD; real model mode).

Boots the shipped service as a real subprocess (``python -m travel_desk``, the same
entry point and machine as AC-019) in ``llm_mode=real`` and drives the S-01 golden
path three times over HTTP/SSE. Per run it times the contract's boundary (D-13):

    from  employee explicit submit (all required slots already filled)
    to    the approval-card event (``approval_requested``; card retrievable)

The submit SSE stream returns when the LangGraph run suspends at the approval
interrupt, immediately after ``request_approval`` emits ``approval_requested``; the
plan node runs the real itinerary-summary model call first, so the window contains
genuine real-model latency (Fake/Recorded timing is not admissible, D-08).

Reports the median of 3 (threshold <= 90 s), plus max/min, machine info, model name
and the timing boundary. Real calls are metered under AUTH-01 into the persistent
``artifacts/llm-usage.jsonl``; the subprocess inherits the parent environment (the
API key is forwarded to the child but NEVER read, printed, or committed here —
only the variable NAME is checked, F-03/HC-07).

Usage::

    uv run --offline python scripts/eval_timing.py <out_dir> [--runs 3] [--keep]
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from e2e_lib import (S01, Desk, Server, cleanup, now, read_jsonl, temp_work,  # noqa: E402
                     write_json)

THRESHOLD_S = 90.0
USAGE_LOG = REPO_ROOT / "artifacts" / "llm-usage.jsonl"


def _usage_delta(before: int) -> dict[str, Any]:
    lines = read_jsonl(USAGE_LOG)
    calls = lines[before:]
    return {"n": len(calls), "total_tokens": sum(c.get("total_tokens") or 0 for c in calls),
            "est_cost_cny": round(sum(c.get("est_cost_cny") or 0 for c in calls), 6),
            "by_purpose": sorted({c.get("purpose") for c in calls}),
            "models": sorted({c.get("model") for c in calls})}


def _pending_card(d: Desk, trip_id: str, revision: int) -> dict[str, Any]:
    cards = [a for a in d.pending_approvals() if a["trip_id"] == trip_id and a["revision"] == revision]
    return cards[0]["card"] if cards else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        sys.exit("DASHSCOPE_API_KEY is not set (only the name is checked, never the value)")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    work = temp_work("ac020-")
    server = Server(work, out, llm_mode="real")
    # REAL mode under AUTH-01: forward the inherited environment (the child needs the key to
    # call DashScope) and log usage to the PERSISTENT ledger so the run is reconciled. The key
    # value is never read/printed here — {**os.environ} only forwards it to the subprocess.
    server.env = {**os.environ,
                  "TRAVEL_DESK_DATA_DIR": str(server.data_dir),
                  "TRAVEL_DESK_LLM_MODE": "real",
                  "TRAVEL_DESK_PORT": str(server.port),
                  "TRAVEL_DESK_HOST": "127.0.0.1",
                  "TRAVEL_DESK_USAGE_LOG": str(USAGE_LOG),
                  "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}

    usage_before = len(read_jsonl(USAGE_LOG))
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "llm_mode": "real", "threshold_s": THRESHOLD_S,
        "start_command": "python -m travel_desk (= uv run travel-desk), real-mode subprocess",
        "timing_boundary": "员工显式提交（必填槽位已齐，D-13）→ 审批卡事件 approval_requested（审批卡可获取）",
        "machine": {"platform": platform.platform(), "machine": platform.machine(),
                    "processor": platform.processor(), "python": sys.version.split()[0],
                    "cpu_count": os.cpu_count()},
        "scenario": "S-01（北京→上海，2026-10-28~30，供应商大会，预算 400000 分；总额 476000 需审批）",
        "runs": [], "passed": False,
    }

    try:
        boot = server.start()
        report["boot"] = boot
        with httpx.Client(base_url=server.base, timeout=180) as client:
            d = Desk(client, server)
            for i in range(1, args.runs + 1):
                trip_id = d.create_trip()["trip"]["id"]
                d.fill(trip_id, S01)  # all required slots filled BEFORE the timed window (D-13)
                t0 = time.perf_counter()
                events = d.submit(trip_id)  # blocks until the run suspends at the approval interrupt
                elapsed = time.perf_counter() - t0
                trip = d.trip(trip_id)
                card = _pending_card(d, trip_id, 1)
                approval_events = d.events_of(trip_id, "approval_requested")
                ok = (bool(approval_events) and trip["status"] == "PENDING_APPROVAL"
                      and bool(card) and card.get("cost_breakdown", {}).get("total_fen") == 476000)
                report["runs"].append({
                    "run": i, "trip_id": trip_id, "elapsed_s": round(elapsed, 3),
                    "status": trip["status"], "approval_requested_events": len(approval_events),
                    "card_total_fen": card.get("cost_breakdown", {}).get("total_fen"),
                    "card_policy_version": card.get("policy_version"),
                    "sse_event_types": [e["type"] for e in events], "valid": ok,
                })
                print(f"  run {i}: {elapsed:.3f}s  status={trip['status']}  card_ok={bool(card)}  valid={ok}",
                      flush=True)
        report["provider_health"] = None
        with httpx.Client(base_url=server.base, timeout=30) as tail:
            resp = tail.get("/api/health/providers", headers=Desk.headers("u-wangwu"))
            report["provider_health"] = resp.json() if resp.status_code == 200 else resp.text
            llm = (report["provider_health"] or {}).get("llm", {}) if isinstance(report["provider_health"], dict) else {}
            report["text_model"] = llm.get("text_model")
            report["vision_model"] = llm.get("vision_model")
            # api_key_configured is a presence flag (bool), never the key value (HC-07).
            report["api_key_configured"] = llm.get("api_key_configured")
        report["boot_log_tail"] = server.boot_log(1)[-3000:]
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {report['error']}", file=sys.stderr, flush=True)
    finally:
        server.stop()
        if server.egress_log.is_file():
            shutil.copyfile(server.egress_log, out / "network-guard.jsonl")
        report["usage"] = _usage_delta(usage_before)
        elapsed_list = [r["elapsed_s"] for r in report["runs"] if r["valid"]]
        all_valid = len(elapsed_list) == args.runs
        if elapsed_list:
            report["median_s"] = round(statistics.median(elapsed_list), 3)
            report["max_s"] = round(max(elapsed_list), 3)
            report["min_s"] = round(min(elapsed_list), 3)
        else:
            report["median_s"] = report["max_s"] = report["min_s"] = None
        report["all_runs_valid"] = all_valid
        report["passed"] = bool(all_valid and report["median_s"] is not None
                                and report["median_s"] <= THRESHOLD_S)
        write_json(out / "timing-report.json", report)
        if not args.keep:
            cleanup(work)

    print(f"runs={len(report['runs'])} valid={sum(1 for r in report['runs'] if r['valid'])} "
          f"median={report['median_s']}s max={report['max_s']}s min={report['min_s']}s "
          f"(<= {THRESHOLD_S}s) passed={report['passed']}")
    if "usage" in report:
        u = report["usage"]
        print(f"  usage: {u['n']} calls, {u['total_tokens']} tokens, {u['est_cost_cny']} CNY "
              f"purposes={u['by_purpose']} models={u['models']}")
    print(f"wrote {out / 'timing-report.json'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
