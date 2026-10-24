"""Export AC-007 evidence: provider calls, plan JSON and rule evaluation per scenario.

Runs entirely offline against the Demo fixtures and a throwaway database, so it
is safe to re-run. Usage:

    uv run python scripts/export_plan_evidence.py artifacts/acceptance/AC-007/plans
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from travel_desk.config import Settings
from travel_desk.db.database import initialize_database
from travel_desk.db.repositories import list_policy_rules
from travel_desk.domain.models import Preferences, TripSlots
from travel_desk.domain.policy import RuleSet
from travel_desk.domain.rules import PlanChoice, evaluate_trip
from travel_desk.planning.planner import build_plan
from travel_desk.planning.selector import ForgedCandidateError, verify_choice
from travel_desk.providers.demo import build_demo_providers

SCENARIOS = [
    ("S-01", dict(destination="上海", start_date="2026-10-28", end_date="2026-10-30",
                  purpose="供应商大会", declared_budget_fen=400000), frozenset(), None),
    ("S-02", dict(destination="上海", start_date="2026-10-25", end_date="2026-10-26",
                  purpose="客户拜访", declared_budget_fen=250000), frozenset(), None),
    ("S-03", dict(destination="成都", start_date="2026-10-27", end_date="2026-10-29",
                  purpose="培训", declared_budget_fen=400000), frozenset(), None),
    ("S-04", dict(destination="上海", start_date="2026-10-22", end_date="2026-10-23",
                  purpose="紧急会议", declared_budget_fen=300000), frozenset(), None),
    ("AC-023-neutral", dict(destination="上海", start_date="2026-11-05", end_date="2026-11-06",
                            purpose="客户拜访", declared_budget_fen=250000), frozenset(), None),
    ("AC-023-avoid-early", dict(destination="上海", start_date="2026-11-05", end_date="2026-11-06",
                                purpose="客户拜访", declared_budget_fen=250000), frozenset(),
     Preferences(avoid_early_departure=True)),
    ("S-09-transport-timeout", dict(destination="上海", start_date="2026-10-25", end_date="2026-10-26",
                                    purpose="客户拜访", declared_budget_fen=250000),
     frozenset({"transport_timeout"}), None),
    ("AC-013-no-protocol-hotel", dict(destination="上海", start_date="2026-10-25", end_date="2026-10-26",
                                      purpose="客户拜访", declared_budget_fen=250000),
     frozenset({"no_protocol_hotel"}), None),
    ("AC-013-weather-unavailable", dict(destination="上海", start_date="2026-10-28", end_date="2026-10-30",
                                        purpose="供应商大会", declared_budget_fen=400000),
     frozenset({"weather_unavailable"}), None),
]


async def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        base = Settings.from_env()
        data = Path(tmp)
        settings = replace(base, data_dir=data, db_path=data / "app.sqlite",
                           checkpoint_db_path=data / "checkpoints.sqlite", upload_dir=data / "uploads")
        initialize_database(settings)
        rules = RuleSet.from_rules("v1.2", list_policy_rules(settings.db_path, "v1.2"))
        today = settings.demo_today
        summary = []
        for name, fields, faults, prefs in SCENARIOS:
            slots = TripSlots(origin="北京", **fields)
            providers = build_demo_providers(settings.providers_dir, faults)
            result = await build_plan(slots=slots, rules=rules, role="employee", today=today, revision=1,
                                      preferences=prefs, providers=providers)
            evaluation = evaluate_trip(slots, rules, "employee", today, result.choice)
            returned = {cid for call in result.calls for cid in call.candidate_ids}
            chosen = (result.choice.outbound, result.choice.return_leg, result.choice.hotel)
            selected = [c.id for c in chosen if c]
            record = {
                "scenario": name, "demo_today": today.isoformat(), "policy_version": rules.version,
                "faults": sorted(faults), "preferences": prefs.model_dump() if prefs else None,
                "slots": slots.model_dump(mode="json"),
                "provider_calls": [c.model_dump(mode="json", exclude={"duration_ms"}) for c in result.calls],
                "plan": result.plan.to_json(),
                "evaluation": evaluation.model_dump(mode="json"),
                "selected_ids": selected,
                "all_selected_ids_in_responses": set(selected) <= returned,
            }
            (out_dir / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
            cost = evaluation.cost_breakdown
            summary.append({"scenario": name, "selected_ids": selected, "outcome": evaluation.outcome,
                            "total_fen": cost.total_fen, "known_subtotal_fen": cost.known_subtotal_fen,
                            "violations": [v.code for v in evaluation.violations],
                            "approval_reasons": [r.code for r in evaluation.approval_reasons],
                            "all_selected_ids_in_responses": record["all_selected_ids_in_responses"]})

        # Forged-id and altered-price attempts against the S-02 run.
        slots = TripSlots(origin="北京", **SCENARIOS[1][1])
        s02 = await build_plan(slots=slots, rules=rules, role="employee", today=today, revision=1,
                               preferences=None, providers=build_demo_providers(settings.providers_dir))
        attempts = {
            "forged_outbound_id": PlanChoice(
                s02.choice.outbound.model_copy(update={"id": "T-BJS-SHA-1025-FAKE"}),
                s02.choice.return_leg, s02.choice.hotel),
            "altered_hotel_price": PlanChoice(
                s02.choice.outbound, s02.choice.return_leg,
                s02.choice.hotel.model_copy(update={"price_fen": 1})),
        }
        forged = {}
        for label, choice in attempts.items():
            try:
                verify_choice(choice, s02.responses)
                forged[label] = {"rejected": False}
            except ForgedCandidateError as exc:
                forged[label] = {"rejected": True, "error": str(exc)}
        def dump(value) -> str:
            return json.dumps(value, ensure_ascii=False, indent=2) + "\n"

        (out_dir / "forged-candidates.json").write_text(dump(forged), encoding="utf-8")
        (out_dir / "summary.json").write_text(dump(summary), encoding="utf-8")
        print(json.dumps({"summary": summary, "forged": forged}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/acceptance/AC-007/plans")))
