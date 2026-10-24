"""LLM layer without network: fake parser, recorded lookups, metering and the AUTH-01 guard."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from travel_desk.llm import dashscope
from travel_desk.llm.dashscope import BudgetPaused, ModelUnavailable, UsageMeter, structured_call
from travel_desk.llm.slots import (
    FakeSlotExtractor,
    RecordedSlotExtractor,
    SlotsOut,
    chinese_number,
    from_model_output,
    parse_message,
    recorded_key,
)
from travel_desk.llm.vision import RecordedInvitationExtractor, build_invitation_extractor

TODAY = date(2026, 10, 20)
INVITATION_SHA = "e88ef3b9c44b45849869dfab5ac570e1a0c5aaf44565992aff6615250705bf84"


def _ledger(path: Path, total: str = "0.4119") -> Path:
    path.write_text(f"# 台账\n\n<!-- manual_total_cny: {total} -->\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- fake parser


def test_parser_full_sentence():
    fields = parse_message("我要从北京去上海，10月28日到10月30日，参加供应商大会，预算4000元，"
                           "会议地址：上海市浦东新区滨江大道2727号", TODAY)
    assert fields == {
        "origin": "北京", "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30",
        "purpose": "供应商大会", "declared_budget_fen": 400000,
        "meeting_address": "上海市浦东新区滨江大道2727号",
    }


def test_parser_does_not_invent_vague_fields():
    assert parse_message("下周去杭州", TODAY) == {"destination": "杭州"}


@pytest.mark.parametrize(("text", "fen"), [("预算改成5000元", 500000), ("预算两千五", 250000),
                                          ("预算1.2万", 1200000), ("预算四千块", 400000)])
def test_parser_budget(text, fen):
    assert parse_message(text, TODAY)["declared_budget_fen"] == fen


@pytest.mark.parametrize(("text", "value"), [("四千", 4000), ("两千五", 2500), ("一万二", 12000),
                                            ("三千五百", 3500), ("一千零五", 1005), ("十二", 12),
                                            ("二十", 20)])
def test_chinese_number(text, value):
    assert chinese_number(text) == value


def test_parser_preferences_and_same_day():
    fields = parse_message("北京到天津 10月25日当天往返，回避早班", TODAY)
    assert fields["start_date"] == fields["end_date"] == "2026-10-25"
    assert fields["preferences"] == {"avoid_early_departure": True}


async def test_fake_extractor_is_labelled_fake():
    result = await FakeSlotExtractor().extract("去上海", TODAY)
    assert result.generator == "fake"
    assert result.fields == {"destination": "上海"}


def test_model_output_conversion():
    out = SlotsOut(origin=" 北京 ", destination="上海", start_date=None, declared_budget_yuan=4000.5,
                   avoid_early_departure=True, purpose="")
    assert from_model_output(out) == {"origin": "北京", "destination": "上海",
                                      "declared_budget_fen": 400050,
                                      "preferences": {"avoid_early_departure": True}}


# ---------------------------------------------------------------- recorded


async def test_recorded_slots_lookup_and_missing(tmp_path: Path):
    extractor = RecordedSlotExtractor(tmp_path)
    with pytest.raises(ModelUnavailable) as missing_file:
        await extractor.extract("去上海", TODAY)
    assert missing_file.value.retryable is False
    key = recorded_key("去  上海", TODAY)  # whitespace-normalised
    (tmp_path / "slot_extractions.json").write_text(json.dumps(
        {"entries": {key: {"source": "hand_written", "fields": {"destination": "上海"}}}}), encoding="utf-8")
    result = await extractor.extract("去 上海", TODAY)
    assert result.generator == "recorded:hand_written"
    assert result.fields == {"destination": "上海"}
    with pytest.raises(ModelUnavailable):
        await extractor.extract("去北京", TODAY)


async def test_recorded_invitation(settings):
    extractor = build_invitation_extractor(settings)  # fake mode -> recorded outputs
    assert isinstance(extractor, RecordedInvitationExtractor)
    result = await extractor.extract(b"", "image/png", INVITATION_SHA)
    assert result.generator == "recorded:model"
    assert result.fields["destination"] == "上海"
    assert result.fields["start_date"] == "2026-10-28"
    assert set(result.fields) == {"destination", "start_date", "end_date", "purpose", "meeting_address"}
    with pytest.raises(ModelUnavailable) as unknown:
        await extractor.extract(b"", "image/png", "0" * 64)
    assert unknown.value.retryable is False


# ---------------------------------------------------------------- meter and guard


def test_meter_sums_manual_and_runtime(tmp_path: Path):
    meter = UsageMeter(tmp_path / "usage.jsonl", _ledger(tmp_path / "ledger.md"))
    assert meter.spent_cny() == pytest.approx(0.4119)
    entry = meter.record(purpose="slot_extraction", model="m",
                         usage={"input_tokens": 600, "output_tokens": 100, "total_tokens": 700}, latency_ms=5)
    assert entry.est_cost_cny == pytest.approx(700 * 36 / 1_000_000)
    line = json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8"))
    assert set(line) == {"ts", "purpose", "model", "input_tokens", "output_tokens", "total_tokens",
                         "est_cost_cny", "latency_ms"}
    assert meter.spent_cny() == pytest.approx(0.4119 + 0.0252)


def test_guard_pauses_at_line(tmp_path: Path):
    meter = UsageMeter(tmp_path / "usage.jsonl", _ledger(tmp_path / "ledger.md", "39.99"))
    meter.check()
    meter.record(purpose="p", model="m", usage={"total_tokens": 1000}, latency_ms=1)  # +0.036
    with pytest.raises(BudgetPaused) as paused:
        meter.check()
    assert paused.value.retryable is False


def test_guard_fails_closed_without_ledger(tmp_path: Path):
    meter = UsageMeter(tmp_path / "usage.jsonl", tmp_path / "missing.md")
    with pytest.raises(BudgetPaused):
        meter.check()
    (tmp_path / "bad.md").write_text("no marker", encoding="utf-8")
    with pytest.raises(BudgetPaused):
        UsageMeter(tmp_path / "usage.jsonl", tmp_path / "bad.md").check()


def test_repo_ledger_marker_is_readable():
    from travel_desk.config import REPO_ROOT

    assert UsageMeter(Path("unused"), REPO_ROOT / "artifacts" / "cost-ledger.md").manual_total_cny() > 0


class _FakeRaw:
    usage_metadata = {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}


class _FakeStructured:
    def __init__(self, outcome):
        self.outcome = outcome

    async def ainvoke(self, messages):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeChat:
    def __init__(self, outcome):
        self.outcome = outcome

    def with_structured_output(self, schema, **kwargs):
        assert kwargs == {"method": "function_calling", "include_raw": True}
        return _FakeStructured(self.outcome)


async def test_structured_call_records_usage(settings, tmp_path, monkeypatch):
    settings = replace(settings, usage_log_path=tmp_path / "u.jsonl", cost_ledger_path=_ledger(tmp_path / "l.md"))
    meter = UsageMeter.from_settings(settings)
    parsed = SlotsOut(destination="上海")
    monkeypatch.setattr(dashscope, "chat_model",
                        lambda *a, **k: _FakeChat({"raw": _FakeRaw(), "parsed": parsed, "parsing_error": None}))
    out = await structured_call(settings, meter, purpose="slot_extraction", model="m", schema=SlotsOut,
                                messages=[])
    assert out is parsed
    assert json.loads((tmp_path / "u.jsonl").read_text(encoding="utf-8"))["total_tokens"] == 60


async def test_structured_call_hides_exception_text(settings, tmp_path, monkeypatch):
    settings = replace(settings, usage_log_path=tmp_path / "u.jsonl", cost_ledger_path=_ledger(tmp_path / "l.md"))
    secret_text = "Incorrect API key provided: sk-abc***xyz"
    monkeypatch.setattr(dashscope, "chat_model", lambda *a, **k: _FakeChat(PermissionError(secret_text)))
    with pytest.raises(ModelUnavailable) as failure:
        await structured_call(settings, UsageMeter.from_settings(settings), purpose="p", model="m",
                              schema=SlotsOut, messages=[])
    assert failure.value.error_type == "PermissionError"
    assert "sk-" not in str(failure.value) and "sk-" not in failure.value.reason
    assert failure.value.__cause__ is None and failure.value.__suppress_context__


async def test_guard_blocks_before_client_is_built(settings, tmp_path, monkeypatch):
    settings = replace(settings, usage_log_path=tmp_path / "u.jsonl",
                       cost_ledger_path=_ledger(tmp_path / "l.md", "40"))

    def boom(*args, **kwargs):
        raise AssertionError("client must not be built once paused")

    monkeypatch.setattr(dashscope, "chat_model", boom)
    with pytest.raises(BudgetPaused):
        await structured_call(settings, UsageMeter.from_settings(settings), purpose="p", model="m",
                              schema=SlotsOut, messages=[])


def test_missing_key_is_not_retryable(settings, monkeypatch):
    monkeypatch.delenv(dashscope.API_KEY_ENV, raising=False)
    assert dashscope.api_key_present() is False
    with pytest.raises(ModelUnavailable) as failure:
        dashscope.chat_model(settings, "m")
    assert failure.value.retryable is False
