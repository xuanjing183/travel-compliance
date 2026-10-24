"""Full-chain real-model run of the S-01 golden path (TASK-013 / AC-027).

AC-020 timed the contract's ``submit -> approval card`` window, but it filled the
slots programmatically first, so real *slot extraction* was never exercised. This
script closes that gap by driving the whole chain from the employee's own words::

    natural-language utterance -> real slot extraction (conversation turns)
                               -> real itinerary summary (submit)
                               -> approval card (PENDING_APPROVAL)

and then asserts the outcome is the one the deterministic core produces for the
same scenario: status ``PENDING_APPROVAL``, total ``476000`` fen, and *identical*
selected transport/hotel candidates. The baseline is not hard-coded — it is
measured from a fake-mode run of S-01 in Phase A of the same invocation, so the
comparison really is "real chain vs deterministic mode".

Two servers run in sequence:

* **Phase A** — ``llm_mode=fake``, ``DASHSCOPE_API_KEY`` removed from the child
  environment: the deterministic baseline. Zero cost, zero external network.
* **Phase B** — ``llm_mode=real``: the chain under test. The inherited
  environment is forwarded so the child can reach the AUTH-01 model endpoint;
  the key is never read, printed, or written here — only the variable NAME is
  checked and only the boolean ``api_key_configured`` flag is recorded
  (F-03 / HC-07). Real calls are metered into the persistent
  ``artifacts/llm-usage.jsonl``.

Usage::

    uv run --offline python scripts/eval_fullchain.py <out_dir> [--runs 1] [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from e2e_lib import (S01, Check, Desk, Server, cleanup, now, read_jsonl,  # noqa: E402
                     temp_work, write_json)

USAGE_LOG = REPO_ROOT / "artifacts" / "llm-usage.jsonl"

# The employee's own words, exactly as the 3-minute demo script types them.
UTTERANCE_OPEN = "下周去上海参加供应商大会，预算四千"
UTTERANCE_FOLLOWUP = "从北京出发，10月28日到10月30日"
# Only used if the model still reports missing slots after the two scripted turns;
# the report flags it as `topup_used` so the evidence cannot overstate the flow.
UTTERANCE_TOPUP = "出发地北京，出发日期2026-10-28，返回日期2026-10-30，事由供应商大会，预算4000元"

EXPECTED_TOTAL_FEN = 476000
EXPECTED_STATUS = "PENDING_APPROVAL"
SLOT_FIELDS = ("origin", "destination", "start_date", "end_date", "purpose", "declared_budget_fen")


def _usage_delta(before: int) -> dict[str, Any]:
    calls = read_jsonl(USAGE_LOG)[before:]
    return {"n": len(calls), "total_tokens": sum(c.get("total_tokens") or 0 for c in calls),
            "est_cost_cny": round(sum(c.get("est_cost_cny") or 0 for c in calls), 6),
            "by_purpose": sorted({c.get("purpose") for c in calls}),
            "models": sorted({c.get("model") for c in calls}),
            "calls": [{"purpose": c.get("purpose"), "model": c.get("model"),
                       "total_tokens": c.get("total_tokens"), "est_cost_cny": c.get("est_cost_cny"),
                       "elapsed_ms": c.get("elapsed_ms"), "ts": c.get("ts")} for c in calls]}


def _selected_ids(trip: dict[str, Any]) -> dict[str, Any]:
    # Before submit there is no plan yet; report empty ids rather than crashing.
    plan = trip.get("plan") or {}
    transport, hotel = plan.get("transport") or {}, plan.get("hotel") or {}
    return {"outbound": (transport.get("outbound") or {}).get("id"),
            "return": (transport.get("return") or {}).get("id"),
            "hotel": (hotel.get("candidate") or {}).get("id")}


def _snapshot(d: Desk, trip_id: str) -> dict[str, Any]:
    trip = d.trip(trip_id)
    cost = trip.get("cost_breakdown") or {}
    summary = (trip.get("plan") or {}).get("summary") or {}
    return {"status": trip["status"], "slots": {k: trip["slots"].get(k) for k in SLOT_FIELDS},
            "missing_fields": trip["missing_fields"], "invalid_fields": trip["invalid_fields"],
            "selected_ids": _selected_ids(trip),
            "cost": {k: cost.get(k) for k in ("nights", "days", "transport_fen", "hotel_fen",
                                              "meal_fen", "total_fen", "budget_delta_fen")},
            "violations": [v["code"] for v in trip["violations"]],
            "approval_reasons": [r["code"] for r in trip["approval_reasons"]],
            "summary": {"status": summary.get("status"), "generator": summary.get("generator"),
                        "chars": len(summary.get("text") or ""), "text": summary.get("text")}}


def _pending_card(d: Desk, trip_id: str, revision: int) -> dict[str, Any]:
    cards = [a for a in d.pending_approvals() if a["trip_id"] == trip_id and a["revision"] == revision]
    return cards[0]["card"] if cards else {}


def _real_env(server: Server) -> None:
    """Forward the inherited environment so the child can call the AUTH-01 endpoint.

    ``{**os.environ}`` only passes the key to the subprocess; this script never
    reads, prints, or persists its value.
    """
    server.env = {**os.environ,
                  "TRAVEL_DESK_DATA_DIR": str(server.data_dir),
                  "TRAVEL_DESK_LLM_MODE": "real",
                  "TRAVEL_DESK_PORT": str(server.port),
                  "TRAVEL_DESK_HOST": "127.0.0.1",
                  "TRAVEL_DESK_USAGE_LOG": str(USAGE_LOG),
                  "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}


def phase_a_baseline(out: Path, chain: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic (fake-mode) S-01: the reference the real chain must match."""
    work = temp_work("ac027-a-")
    logs = out / "baseline"
    logs.mkdir(parents=True, exist_ok=True)
    server = Server(work, logs, llm_mode="fake")
    baseline: dict[str, Any] = {"llm_mode": "fake"}
    try:
        baseline["boot"] = server.start()
        with httpx.Client(base_url=server.base, timeout=120) as client:
            d = Desk(client, server)
            trip_id = d.create_trip()["trip"]["id"]
            d.fill(trip_id, S01)          # slots supplied directly: no model in this phase
            d.submit(trip_id)
            baseline.update(_snapshot(d, trip_id))
            baseline["trip_id"] = trip_id
            baseline["card_total_fen"] = _pending_card(d, trip_id, 1).get("cost_breakdown", {}).get("total_fen")
        chain.append({"phase": "A-baseline", "llm_mode": "fake", "input": "PATCH /slots with S01 (no model)",
                      "result": baseline["slots"], "status": baseline["status"],
                      "total_fen": baseline["cost"]["total_fen"], "at": now()})
    finally:
        server.stop()
        cleanup(work)
    return baseline


def phase_b_real(out: Path, runs: int, baseline: dict[str, Any],
                 chain: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The chain under test: natural language in, approval card out, real models throughout."""
    work = temp_work("ac027-b-")
    logs = out / "real"
    logs.mkdir(parents=True, exist_ok=True)
    server = Server(work, logs, llm_mode="real")
    _real_env(server)
    meta: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    try:
        meta["boot"] = server.start()
        with httpx.Client(base_url=server.base, timeout=300) as client:
            d = Desk(client, server)
            for i in range(1, runs + 1):
                checks = Check(echo=True)
                print(f"\n=== Phase B run {i} (llm_mode=real) ===", flush=True)
                record: dict[str, Any] = {"run": i, "turns": []}

                created = d.create_trip(text=UTTERANCE_OPEN)
                trip_id = created["trip"]["id"]
                record["trip_id"] = trip_id
                after_open = _snapshot(d, trip_id)
                assistant = created.get("assistant") or {}
                record["turns"].append({
                    "turn": 1, "via": "POST /api/trips {text}", "utterance": UTTERANCE_OPEN,
                    "assistant_text": assistant.get("text") if isinstance(assistant, dict) else assistant,
                    "slots": after_open["slots"], "missing_fields": after_open["missing_fields"]})
                chain.append({"phase": "B-real", "run": i, "turn": 1, "utterance": UTTERANCE_OPEN,
                              "extracted": after_open["slots"],
                              "missing_fields": after_open["missing_fields"], "at": now()})
                print(f"  turn1 {UTTERANCE_OPEN!r} -> slots={after_open['slots']} "
                      f"missing={after_open['missing_fields']}", flush=True)

                checks("原话抽取出 destination=上海", after_open["slots"]["destination"] == "上海",
                       after_open["slots"]["destination"])
                checks("抽取出 purpose=供应商大会", after_open["slots"]["purpose"] == "供应商大会",
                       after_open["slots"]["purpose"])
                checks("抽取出 declared_budget_fen=400000（四千→整数分）",
                       after_open["slots"]["declared_budget_fen"] == 400000,
                       after_open["slots"]["declared_budget_fen"])
                checks("不把「下周」猜成具体日期",
                       after_open["slots"]["start_date"] is None and after_open["slots"]["end_date"] is None,
                       (after_open["slots"]["start_date"], after_open["slots"]["end_date"]))

                topup_used = False
                for turn, utterance in ((2, UTTERANCE_FOLLOWUP), (3, UTTERANCE_TOPUP)):
                    if not after_open["missing_fields"] and not after_open["invalid_fields"]:
                        break
                    if turn == 3:
                        topup_used = True
                    d.message(trip_id, utterance)
                    after_open = _snapshot(d, trip_id)
                    record["turns"].append({"turn": turn, "via": "POST /api/trips/{id}/messages",
                                            "utterance": utterance, "slots": after_open["slots"],
                                            "missing_fields": after_open["missing_fields"],
                                            "invalid_fields": after_open["invalid_fields"]})
                    chain.append({"phase": "B-real", "run": i, "turn": turn, "utterance": utterance,
                                  "extracted": after_open["slots"],
                                  "missing_fields": after_open["missing_fields"], "at": now()})
                    print(f"  turn{turn} {utterance!r} -> slots={after_open['slots']} "
                          f"missing={after_open['missing_fields']}", flush=True)
                record["topup_used"] = topup_used

                checks("补齐后无缺失/非法槽位",
                       after_open["missing_fields"] == [] and after_open["invalid_fields"] == [],
                       (after_open["missing_fields"], after_open["invalid_fields"]))
                checks("最终槽位与 S-01 逐项一致", after_open["slots"] == {k: S01[k] for k in SLOT_FIELDS},
                       after_open["slots"])

                t0 = time.perf_counter()
                events = d.submit(trip_id)
                record["submit_elapsed_s"] = round(time.perf_counter() - t0, 3)
                final = _snapshot(d, trip_id)
                card = _pending_card(d, trip_id, 1)
                record.update({"final": final, "sse_event_types": [e["type"] for e in events],
                               "card_total_fen": card.get("cost_breakdown", {}).get("total_fen"),
                               "card_policy_version": card.get("policy_version"),
                               "card_sources": sorted({s.get("kind") for s in (card.get("sources") or [])
                                                       if isinstance(s, dict)})})
                chain.append({"phase": "B-real", "run": i, "turn": "submit",
                              "utterance": "POST /api/trips/{id}/submit",
                              "status": final["status"], "total_fen": final["cost"]["total_fen"],
                              "selected_ids": final["selected_ids"],
                              "summary_generator": final["summary"]["generator"],
                              "elapsed_s": record["submit_elapsed_s"], "at": now()})

                # ---- the AC-027 assertions
                checks(f"状态 {EXPECTED_STATUS}", final["status"] == EXPECTED_STATUS, final["status"])
                checks(f"总额 {EXPECTED_TOTAL_FEN} 分",
                       final["cost"]["total_fen"] == EXPECTED_TOTAL_FEN, final["cost"]["total_fen"])
                checks("选中候选与确定性基线一致",
                       final["selected_ids"] == baseline["selected_ids"],
                       {"real": final["selected_ids"], "baseline": baseline["selected_ids"]})
                checks("成本拆解与基线一致", final["cost"] == baseline["cost"],
                       {"real": final["cost"], "baseline": baseline["cost"]})
                checks("违规与审批理由与基线一致",
                       (final["violations"], final["approval_reasons"])
                       == (baseline["violations"], baseline["approval_reasons"]),
                       {"real": [final["violations"], final["approval_reasons"]],
                        "baseline": [baseline["violations"], baseline["approval_reasons"]]})
                checks("审批卡可获取且总额一致",
                       bool(card) and record["card_total_fen"] == EXPECTED_TOTAL_FEN,
                       record["card_total_fen"])
                checks("行程摘要 status=ok", final["summary"]["status"] == "ok", final["summary"])
                checks("行程摘要来自真实模型（generator 非 fake）",
                       bool(final["summary"]["generator"])
                       and not str(final["summary"]["generator"]).startswith(("fake", "recorded")),
                       final["summary"]["generator"])
                # The SSE surface and the audit trail name this differently and both must be there:
                # the stream frame is `approval_required`, the audit event `approval_requested`.
                checks("SSE 流含 approval_required 帧",
                       "approval_required" in record["sse_event_types"], record["sse_event_types"])
                audit_requested = d.events_of(trip_id, "approval_requested")
                record["audit_approval_requested_events"] = len(audit_requested)
                checks("审计含恰好 1 条 approval_requested", len(audit_requested) == 1,
                       len(audit_requested))

                # `take()` clears the collector, so the verdict must be read first.
                record["valid"] = checks.passed
                record["checks"] = checks.take()
                results.append(record)
                print(f"  run {i}: status={final['status']} total={final['cost']['total_fen']} "
                      f"selected={final['selected_ids']} valid={record['valid']}", flush=True)

        with httpx.Client(base_url=server.base, timeout=30) as tail:
            resp = tail.get("/api/health/providers", headers=Desk.headers("u-wangwu"))
            health = resp.json() if resp.status_code == 200 else {"error": resp.text}
            meta["provider_health"] = health
            llm = health.get("llm", {}) if isinstance(health, dict) else {}
            meta["text_model"] = llm.get("text_model")
            meta["vision_model"] = llm.get("vision_model")
            meta["embed_model"] = llm.get("embed_model")
            # A presence flag only — never the key value (HC-07).
            meta["api_key_configured"] = llm.get("api_key_configured")
            meta["network"] = health.get("network")
        meta["boot_log_tail"] = server.boot_log(1)[-3000:]
    finally:
        server.stop()
        if server.egress_log.is_file():
            shutil.copyfile(server.egress_log, out / "real" / "network-guard.jsonl")
        cleanup(work)
    return results, meta


_BYTES_HOST = re.compile(r"^b'(.*)'$")


def _egress_summary(network: Any, log_path: Path | None = None) -> dict[str, Any]:
    """What tried to leave the machine in Phase B, and whether it was only the allowlist.

    IMP-97 caveat, stated rather than hidden: until the host-form fix lands,
    `_local()` classifies a *bytes* host as `local`, so the guard's own counters
    undercount external attempts (a real DashScope call shows up as
    ``external_attempts: 0``). The log is therefore read as well — a ``b'...'``
    host recorded as ``local`` is decoded and judged against the allowlist on its
    merits, and reported separately as an IMP-97 observation.
    """
    if not isinstance(network, dict):
        return {"error": f"provider health returned no network section: {network!r}",
                "only_allowlisted_hosts": False}
    allowed_hosts = set(network.get("allowed_hosts") or [])
    external = network.get("external_hosts") or {}
    seen = {str(key).rsplit(":", 1)[0] for key in external}

    imp97: list[dict[str, Any]] = []
    if log_path is not None and Path(log_path).is_file():
        for line in Path(log_path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            match = _BYTES_HOST.match(str(entry.get("host") or ""))
            if match and entry.get("decision") == "local":
                decoded = match.group(1)
                on_list = decoded.lower() in allowed_hosts
                imp97.append({"logged_as": entry["host"], "decoded_host": decoded,
                              "via": entry.get("via"), "decision_recorded": entry["decision"],
                              "on_allowlist": on_list,
                              "decision_after_imp97": "allowed" if on_list else "blocked"})
                seen.add(decoded)
    return {"allowed_hosts": sorted(allowed_hosts),
            "attempts": network.get("attempts"),
            "external_attempts": network.get("external_attempts"),
            "blocked": network.get("blocked"), "allowed": network.get("allowed"),
            "local": network.get("local"), "external_hosts": external,
            "external_host_names": sorted(seen),
            "imp97_bytes_hosts_misclassified_as_local": imp97,
            "note": ("external_attempts/local 计数受 IMP-97 影响：bytes 主机名被误判为 local。"
                     "本字段以解码后的主机名对允许清单重新判定，故 only_allowlisted_hosts 反映"
                     "实质结论；IMP-97 修复落地后同一调用应记为 decision=allowed、"
                     "external_attempts=1。") if imp97 else None,
            # Nothing was refused, and every external name actually seen is one configuration allows.
            "only_allowlisted_hosts": network.get("blocked") == 0 and seen <= allowed_hosts}


def _secret_scan(out: Path) -> dict[str, Any]:
    """Evidence must be desensitised. Scans for credential *shapes*, never reads the key."""
    hits: list[str] = []
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in ("sk-", "DASHSCOPE_API_KEY=", "Bearer ", "api_key\":", "apiKey"):
            if needle in text:
                hits.append(f"{path.relative_to(out)}:{needle}")
    return {"scanned_files": len([p for p in out.rglob("*") if p.is_file()]), "hits": hits,
            "clean": not hits}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        sys.exit("DASHSCOPE_API_KEY is not set (only the NAME is checked, never the value)")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    chain: list[dict[str, Any]] = []
    usage_before = len(read_jsonl(USAGE_LOG))
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": "TASK-013", "acceptance": "AC-027",
        "llm_mode": "real", "authorization": "AUTH-01",
        "start_command": "python -m travel_desk (= uv run travel-desk)",
        "purpose": ("整链真实模型运行：自然语言原话 → 真实槽位抽取 → 真实行程摘要 → 审批卡；"
                    "并与同一场景的确定性（fake）运行逐项比对。AC-020 只计时了「提交→审批卡」"
                    "且槽位为预填，未覆盖真实槽位抽取，本运行补上该缺口。"),
        "scenario": "S-01（北京→上海，2026-10-28~30，供应商大会，申报预算 400000 分）",
        "natural_language_inputs": [UTTERANCE_OPEN, UTTERANCE_FOLLOWUP, UTTERANCE_TOPUP],
        "expected": {"status": EXPECTED_STATUS, "total_fen": EXPECTED_TOTAL_FEN,
                     "slots": S01, "baseline": "measured in Phase A (fake mode), not hard-coded"},
        "machine": {"platform": platform.platform(), "machine": platform.machine(),
                    "python": sys.version.split()[0], "cpu_count": os.cpu_count()},
        "runs_requested": args.runs, "passed": False,
    }

    try:
        print("=== Phase A: deterministic baseline (fake, zero cost) ===", flush=True)
        baseline = phase_a_baseline(out, chain)
        report["baseline"] = baseline
        print(f"  baseline: status={baseline['status']} total={baseline['cost']['total_fen']} "
              f"selected={baseline['selected_ids']}", flush=True)

        print("\n=== Phase B: full chain with real models ===", flush=True)
        results, meta = phase_b_real(out, args.runs, baseline, chain)
        report["real"] = meta
        report["runs"] = results
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {report['error']}", file=sys.stderr, flush=True)
    finally:
        report["usage"] = _usage_delta(usage_before)
        runs = report.get("runs") or []
        report["runs_valid"] = sum(1 for r in runs if r.get("valid"))
        report["all_runs_valid"] = bool(runs) and len(runs) == args.runs and report["runs_valid"] == args.runs
        # That real models actually ran is proven by the metered call log (AUTH-01 ledger),
        # not by a label in the plan JSON: purposes and model names come from the calls made.
        usage = report["usage"]
        generators = [str((r.get("final") or {}).get("summary", {}).get("generator") or "") for r in runs]
        corroboration = {
            "slot_extraction_calls": sum(1 for c in usage["calls"] if c["purpose"] == "slot_extraction"),
            "itinerary_summary_calls": sum(1 for c in usage["calls"] if c["purpose"] == "itinerary_summary"),
            "purposes_used": usage["by_purpose"], "models_used": usage["models"],
            "summary_generators": generators,
            "no_fake_or_recorded_output": all(g and not g.startswith(("fake", "recorded")) for g in generators),
        }
        corroboration["passed"] = bool(
            corroboration["slot_extraction_calls"] >= args.runs
            and corroboration["itinerary_summary_calls"] >= args.runs
            and corroboration["models_used"] and corroboration["no_fake_or_recorded_output"])
        report["real_model_corroboration"] = corroboration
        network = (report.get("real") or {}).get("network")
        report["egress"] = _egress_summary(network, out / "real" / "network-guard.jsonl")
        report["desensitised"] = _secret_scan(out)
        report["passed"] = bool(report["all_runs_valid"] and corroboration["passed"]
                               and report["egress"]["only_allowlisted_hosts"]
                               and report["desensitised"]["clean"] and "error" not in report)
        write_json(out / "fullchain-report.json", report)
        write_json(out / "scenario.json", {"scenario": report["scenario"], "slots": S01,
                                           "utterances": report["natural_language_inputs"],
                                           "expected_total_fen": EXPECTED_TOTAL_FEN,
                                           "expected_status": EXPECTED_STATUS,
                                           "demo_today": os.environ.get("DEMO_TODAY", "2026-10-20")})
        with (out / "chain-log.jsonl").open("w", encoding="utf-8") as handle:
            for entry in chain:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        report["desensitised"] = _secret_scan(out)
        write_json(out / "fullchain-report.json", report)

    print(f"\nruns={len(report.get('runs') or [])} valid={report['runs_valid']} "
          f"passed={report['passed']}")
    u = report["usage"]
    print(f"  usage: {u['n']} calls, {u['total_tokens']} tokens, {u['est_cost_cny']} CNY "
          f"purposes={u['by_purpose']} models={u['models']}")
    print(f"  egress: {report['egress']}")
    print(f"  real-model corroboration: {report['real_model_corroboration']}")
    print(f"  desensitised: {report['desensitised']}")
    print(f"wrote {out / 'fullchain-report.json'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
