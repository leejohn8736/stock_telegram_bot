"""Weekly exchange calendar, based on published KRX/NYSE/Cboe rules.

No API key or third-party dependency is required. Exceptional closures can be
maintained in market_calendar_overrides.json. This is not a live news feed.
"""

from datetime import date, datetime, timedelta
from functools import lru_cache
from html import escape
import json
from pathlib import Path

import market_calendar as calendar

OVERRIDES_PATH = Path(__file__).resolve().parent / "market_calendar_overrides.json"
SOURCES = {
    "KRX": "https://global.krx.co.kr/contents/GLB/05/0501/0501110000/GLB0501110000.jsp",
    "NYSE": "https://www.nyse.com/markets/hours-calendars",
    "Cboe": "https://www.cboe.com/about/hours/us-options/",
}


def _nth(year, month, weekday, n):
    return calendar._nth_weekday(year, month, weekday, n).date()


@lru_cache(maxsize=16)
def _base_holidays(market, year):
    holidays = {}
    if market == "US":
        for month, day, name in ((1, 1, "신정"), (6, 19, "준틴스"),
                                 (7, 4, "독립기념일"), (12, 25, "크리스마스")):
            actual = date(year, month, day)
            # NYSE does not observe Saturday New Year's Day on Friday.
            observed = actual
            if actual.weekday() == 6:
                observed += timedelta(days=1)
            elif actual.weekday() == 5 and month != 1:
                observed -= timedelta(days=1)
            holidays[observed] = name + (" 대체휴장" if observed != actual else "")
        for day, name in (
            (_nth(year, 1, 0, 3), "마틴 루터 킹 데이"),
            (_nth(year, 2, 0, 3), "대통령의 날"),
            (calendar._easter(year).date() - timedelta(days=2), "성금요일"),
            (calendar._last_weekday(year, 5, 0).date(), "메모리얼 데이"),
            (_nth(year, 9, 0, 1), "노동절"),
            (_nth(year, 11, 3, 4), "추수감사절"),
        ):
            holidays[day] = name
        return holidays

    if year not in calendar.KOREA_LUNAR_HOLIDAYS:
        raise ValueError(f"{year}년 한국 음력 휴장일 데이터 업데이트 필요")
    substitutes = []
    fixed = [(1, 1, "신정", False), (3, 1, "삼일절", True),
             (5, 1, "근로자의 날", False), (5, 5, "어린이날", True),
             (6, 6, "현충일", False), (8, 15, "광복절", True),
             (10, 3, "개천절", True), (10, 9, "한글날", True),
             (12, 25, "크리스마스", True)]
    if year >= 2026:
        fixed.append((7, 17, "제헌절", True))
    for month, day, name, substitute in fixed:
        actual = date(year, month, day)
        holidays[actual] = name
        if substitute and actual.weekday() >= 5:
            substitutes.append((actual, name))
    lunar = [dt.date() for dt in calendar.KOREA_LUNAR_HOLIDAYS[year]]
    for group, name in ((lunar[:3], "설날"), (lunar[3:4], "부처님오신날"),
                        (lunar[4:], "추석")):
        collision = any(d in holidays for d in group)
        for day in group:
            holidays[day] = name if day not in holidays else holidays[day] + " / " + name
        # Seollal/Chuseok: Sunday or another holiday, not Saturday alone.
        weekend = any(d.weekday() >= (5 if name == "부처님오신날" else 6) for d in group)
        if weekend or collision:
            substitutes.append((max(group), name))
    for actual, name in sorted(substitutes):
        observed = actual + timedelta(days=1)
        while observed.weekday() >= 5 or observed in holidays:
            observed += timedelta(days=1)
        holidays[observed] = name + " 대체공휴일"
    last = date(year, 12, 31)
    while last.weekday() >= 5 or last in holidays:
        last -= timedelta(days=1)
    holidays[last] = "연말 휴장"
    return holidays


def load_overrides():
    """Read each time so calendar updates take effect without restarting."""
    with OVERRIDES_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)["holidays"]


def holidays_for_year(market, year, overrides):
    result = dict(_base_holidays(market, year))
    for item in overrides:
        day = date.fromisoformat(item["date"])
        if item["market"] == market and day.year == year:
            if item["name"] is None:
                result.pop(day, None)
            else:
                result[day] = item["name"]
    return result


def fetch_weekly_events(now=None):
    """Collect Monday–Sunday events; dates use each exchange's local date."""
    now = now or calendar.get_korea_now()
    if isinstance(now, datetime) and now.tzinfo is not None:
        from datetime import timezone
        now = now.astimezone(timezone(timedelta(hours=9)))
    today = now.date() if isinstance(now, datetime) else now
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    overrides = load_overrides()
    events, warnings = [], []
    months = {(d.year, d.month) for d in (start, end)}
    for market in ("KR", "US"):
        try:
            holidays = {}
            for year in {start.year, end.year}:
                holidays.update(holidays_for_year(market, year, overrides))
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        def add(day, title):
            if start <= day <= end:
                events.append({"date": day.isoformat(), "market": market, "title": title})

        for day, name in holidays.items():
            if day.weekday() < 5:
                add(day, "휴장 · " + name)
        for year, month in sorted(months):
            nominal = _nth(year, month, 3 if market == "KR" else 4, 2 if market == "KR" else 3)
            expiry = nominal
            while expiry.weekday() >= 5 or expiry in holidays:
                expiry -= timedelta(days=1)
            quarterly = month in (3, 6, 9, 12)
            title = ("선물·옵션 동시만기 (네 마녀의 날)" if market == "KR" else
                     "분기 동시만기 (트리플 위칭, 통칭 네 마녀의 날)") if quarterly else "월별 옵션 만기"
            if expiry != nominal:
                title += f" · 휴장으로 {nominal:%m/%d}에서 앞당김"
            add(expiry, title)
            if market == "US":
                candidates = [(_nth(year, 11, 3, 4) + timedelta(days=1), "추수감사절 다음 날"),
                              (date(year, 12, 24), "크리스마스 이브")]
                if date(year, 7, 4).weekday() in (1, 2, 3, 4):
                    candidates.append((date(year, 7, 3), "독립기념일 전날"))
                for day, name in candidates:
                    if day.month == month and day.weekday() < 5 and day not in holidays:
                        utc_close = datetime.combine(day, datetime.min.time()).replace(hour=18)
                        dst = calendar._is_us_dst(utc_close)
                        kst = utc_close + timedelta(hours=8 if dst else 9)
                        add(day, f"조기폐장 · {name} (미 동부 13:00 / 한국 {kst:%m/%d %H:%M})")
    return {"week_start": start.isoformat(), "week_end": end.isoformat(),
            "events": sorted(events, key=lambda e: (e["date"], e["market"], e["title"])),
            "warnings": warnings}


def format_weekly_events(data):
    lines = ["<b>📅 이번 주 주요 주식 일정</b>",
             f"{data['week_start']} ~ {data['week_end']} (월~일)",
             "날짜: 한국장은 한국, 미국장은 미국 동부 기준", ""]
    for event in data["events"]:
        day = date.fromisoformat(event["date"])
        market = "🇰🇷 한국" if event["market"] == "KR" else "🇺🇸 미국"
        lines.append(f"• {day:%m/%d}({'월화수목금토일'[day.weekday()]}) {market} · {escape(event['title'])}")
    if not data["events"]:
        lines.append("확인된 휴장·만기·조기폐장 일정이 없습니다.")
    for warning in data["warnings"]:
        lines.append("⚠️ " + escape(warning))
    lines.extend(["", "<i>거래소 정기 규칙·등록된 예외 일정 기준입니다. 임시휴장은 추가 공지를 확인하세요.",
                  "만기일은 대표 월물 기준이며 상품별 최종거래일은 다를 수 있습니다.</i>",
                  "출처: " + " · ".join(f'<a href="{url}">{name}</a>' for name, url in SOURCES.items())])
    return "\n".join(lines)
