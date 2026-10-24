"""Fallback city coordinates for QWeather when the credential denies GeoAPI.

New-console credentials can be restricted ("安全限制") so that
`/geo/v2/city/lookup` returns 403 even though the weather endpoints work.
QWeather's `/v7/weather/*` accepts `location=lon,lat` directly, so the weather
provider falls back to these factual, public city coordinates (WGS-84 city
centres) instead of failing the whole weather section.

This is identifier data, not business data: it never produces prices,
candidates, or availability — it only addresses the forecast request. When
GeoAPI is allowed again, the exact/rank-based lookup path resumes automatically.
"""
from __future__ import annotations

# city -> (longitude, latitude)
CITY_COORDINATES: dict[str, tuple[float, float]] = {
    "北京": (116.41, 39.90),
    "上海": (121.47, 31.23),
    "广州": (113.26, 23.13),
    "深圳": (114.06, 22.55),
    "天津": (117.20, 39.13),
    "重庆": (106.55, 29.57),
    "杭州": (120.16, 30.28),
    "南京": (118.80, 32.06),
    "武汉": (114.31, 30.59),
    "成都": (104.07, 30.57),
    "西安": (108.94, 34.34),
    "郑州": (113.63, 34.75),
    "洛阳": (112.45, 34.62),
    "开封": (114.35, 34.80),
    "拉萨": (91.11, 29.97),
}


def city_coordinates(city: str) -> tuple[float, float] | None:
    return CITY_COORDINATES.get(city.strip())
