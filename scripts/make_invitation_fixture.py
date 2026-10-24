"""Generate the S-11 meeting-invitation image fixture deterministically (no network).

Output: tests/fixtures/invitations/shanghai_supplier_conference.png
Expected extraction (contract S-11): city=上海, start=2026-10-28, end=2026-10-30,
purpose=供应商大会, meeting_address=上海市浦东新区滨江大道2727号 星衡国际会议中心 3号厅
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "invitations" / "shanghai_supplier_conference.png"
FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1200, 820), "#fbfaf6")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1200, 110], fill="#1f3a5f")
    d.text((60, 28), "星衡科技  ·  会议邀请函", font=ImageFont.truetype(FONT_BOLD, 44), fill="white")

    body = ImageFont.truetype(FONT, 32)
    label = ImageFont.truetype(FONT_BOLD, 32)
    lines = [
        ("致：", "张三（销售部）"),
        ("会议名称：", "2026 年度华东区供应商大会"),
        ("会议事由：", "供应商大会：年度合作评审与新品对接"),
        ("会议时间：", "2026 年 10 月 28 日（周三）至 10 月 30 日（周五）"),
        ("会议地点：", "上海市浦东新区滨江大道 2727 号"),
        ("", "星衡国际会议中心 3 号厅"),
        ("报到时间：", "10 月 28 日 09:00 – 10:00"),
        ("联系人：", "供应链管理部 刘女士"),
    ]
    y = 160
    for k, v in lines:
        if k:
            d.text((80, y), k, font=label, fill="#1f3a5f")
        d.text((280, y), v, font=body, fill="#222222")
        y += 66
    d.line([(80, y + 10), (1120, y + 10)], fill="#c8c2b4", width=2)
    d.text((80, y + 30), "请于 10 月 24 日前回复确认参会。本邀请函为演示 fixture，公司与地址均为虚构。",
           font=ImageFont.truetype(FONT, 24), fill="#666666")
    img.save(OUT, format="PNG", optimize=True)
    print(OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
