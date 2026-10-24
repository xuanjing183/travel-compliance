"""Meeting-invitation image extraction (REQ-003).

The result is only a *proposal*: it is stored as a pending extraction and
touches the Trip only after the employee confirms it (possibly edited).

Implementations:
- `RecordedInvitationExtractor` — frozen model outputs keyed by the image's
  SHA-256 in `recorded/invitations.json` (§7.1.1). Used in both fake and
  recorded mode: there is no rule-based way to read an image, so fake mode does
  not pretend to have one. An unknown image is reported as unavailable.
- `RealInvitationExtractor` — DashScope multimodal model; the local file is sent
  as a base64 data URL (no OSS upload, RISK-002), metered under AUTH-01.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from travel_desk.config import Settings
from travel_desk.llm.dashscope import ModelUnavailable, UsageMeter, structured_call

RECORDED_FILE = "invitations.json"
INVITATION_FIELDS = ("destination", "start_date", "end_date", "purpose", "meeting_address")


@dataclass(frozen=True)
class InvitationExtraction:
    fields: dict[str, Any] = field(default_factory=dict)
    generator: str = "recorded:model"


class InvitationExtractor(Protocol):
    name: str

    async def extract(self, image: bytes, content_type: str, sha256: str) -> InvitationExtraction: ...


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    """Keep the five invitation fields; every one is present (null when not readable)."""
    cleaned = {}
    for name in INVITATION_FIELDS:
        value = fields.get(name)
        cleaned[name] = value.strip() if isinstance(value, str) and value.strip() else None
    return cleaned


class RecordedInvitationExtractor:
    name = "recorded"

    def __init__(self, recorded_dir: Path):
        self.path = recorded_dir / RECORDED_FILE

    async def extract(self, image: bytes, content_type: str, sha256: str) -> InvitationExtraction:
        if not self.path.is_file():
            raise ModelUnavailable(f"缺少录制输出文件 {RECORDED_FILE}", retryable=False)
        try:
            entry = json.loads(self.path.read_text(encoding="utf-8"))["entries"][sha256]
        except KeyError:
            raise ModelUnavailable("当前为确定性模式，录制输出中没有这张图片的识别结果；"
                                   "真实识别需切换到 real 模式", retryable=False) from None
        except (ValueError, TypeError):
            raise ModelUnavailable("录制输出文件格式无效", retryable=False) from None
        source = entry.get("source") if isinstance(entry, dict) else None
        if source not in ("model", "hand_written") or not isinstance(entry.get("fields"), dict):
            raise ModelUnavailable("录制输出缺少 source 或 fields", retryable=False)
        return InvitationExtraction(fields=_clean(entry["fields"]), generator=f"recorded:{source}")


class InvitationOut(BaseModel):
    """会议邀请函抽取结果。看不清或不存在的字段返回 null。"""

    city: str | None = Field(None, description="会议所在城市，规范城市名")
    start_date: str | None = Field(None, description="会议开始日期 YYYY-MM-DD；图片中没有明确日期则为 null")
    end_date: str | None = Field(None, description="会议结束日期 YYYY-MM-DD")
    purpose: str | None = Field(None, description="出差事由，简短名词短语，如会议名称")
    meeting_address: str | None = Field(None, description="会议详细地址，含场馆名")


class RealInvitationExtractor:
    name = "real"

    def __init__(self, settings: Settings, meter: UsageMeter):
        self.settings = settings
        self.meter = meter
        self.model = settings.vision_model

    async def extract(self, image: bytes, content_type: str, sha256: str) -> InvitationExtraction:
        data_url = f"data:{content_type};base64," + base64.b64encode(image).decode("ascii")
        out = await structured_call(
            self.settings, self.meter, purpose="image_extraction", model=self.model, schema=InvitationOut,
            timeout=90.0,
            messages=[("system", "从会议邀请函图片中抽取差旅信息。只抽取图片中明确出现的内容，不要猜测。"),
                      {"role": "user", "content": [
                          {"type": "image_url", "image_url": {"url": data_url}},
                          {"type": "text", "text": "请抽取会议城市、起止日期、事由和详细地址。"}]}])
        fields = {"destination": out.city, "start_date": out.start_date, "end_date": out.end_date,
                  "purpose": out.purpose, "meeting_address": out.meeting_address}
        return InvitationExtraction(fields=_clean(fields), generator=self.model)


def build_invitation_extractor(settings: Settings) -> InvitationExtractor:
    if settings.llm_mode == "real":
        return RealInvitationExtractor(settings, UsageMeter.from_settings(settings))
    return RecordedInvitationExtractor(settings.recorded_dir)
