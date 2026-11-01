"""
US/Korea market holiday calendar.
Determines if markets are open based on NYSE and KRX holiday rules.
"""

from datetime import datetime, timedelta

MONDAY = 0
SUNDAY = 6


def _nth_weekday(year, month, weekday, n):
    """Return date of nth weekday in month (e.g., 3rd Monday)."""
    first = datetime(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year, month, weekday):
    """Return date of last weekday in month."""
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _easter(year):
    """Calculate Easter Sunday using Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime(year, month, day)


def _get_observed_date(month, day, year):
    """
    Get observed market holiday date per NYSE rules:
    Saturday holidays -> preceding Friday
    Sunday holidays -> following Monday
    """
    holiday = datetime(year, month, day)
    if holiday.weekday() == 5:      # Saturday
        return holiday - timedelta(days=1)
    elif holiday.weekday() == 6:    # Sunday
        return holiday + timedelta(days=1)
    return holiday


def _is_observed_market_holiday(date):
    """Check if date is a US market holiday."""
    year = date.year
    today = date.date()

    fixed_holidays = [
        (year, 1, 1),      # New Year's Day
        (year, 6, 19),     # Juneteenth
        (year, 7, 4),      # Independence Day
        (year, 12, 25),    # Christmas
        (year + 1, 1, 1),  # Next Year's New Year
    ]
    for y, m, d in fixed_holidays:
        if _get_observed_date(m, d, y).date() == today:
            return True

    weekday_holidays = [
        _nth_weekday(year, 1, MONDAY, 3),    # MLK Day
        _nth_weekday(year, 2, MONDAY, 3),    # Presidents' Day
        _last_weekday(year, 5, MONDAY),      # Memorial Day
        _nth_weekday(year, 9, MONDAY, 1),    # Labor Day
        _nth_weekday(year, 11, 3, 4),        # Thanksgiving
        _easter(year) - timedelta(days=2),   # Good Friday
    ]
    for holiday in weekday_holidays:
        if holiday.date() == today:
            return True

    return False


def _is_us_dst(dt):
    """
    UTC 기준 datetime이 미국 일광절약시간(DST)에 해당하는지 판별합니다.
    - DST 시작: 3월 둘째 일요일 02:00 EST (= 07:00 UTC)
    - DST 종료: 11월 첫째 일요일 02:00 EDT (= 06:00 UTC)
    """
    year = dt.year
    dst_start = _nth_weekday(year, 3, SUNDAY, 2).replace(hour=2) + timedelta(hours=5)
    dst_end = _nth_weekday(year, 11, SUNDAY, 1).replace(hour=2) + timedelta(hours=4)
    return dst_start <= dt < dst_end


def get_us_eastern_now():
    """현재 미국 동부 표준시(EST/EDT) 기준 naive datetime을 반환합니다."""
    utc_now = datetime.utcnow()
    offset = -4 if _is_us_dst(utc_now) else -5
    return utc_now + timedelta(hours=offset)


def is_us_trading_day(now_et=None):
    """
    현재(미국 동부 기준)가 미국 증시 거래일인지 확인합니다.
    주말 및 미국 공휴일(대체 휴장일 포함)이면 False를 반환합니다.
    """
    if now_et is None:
        now_et = get_us_eastern_now()

    # 주말 제외
    if now_et.weekday() >= 5:
        return False

    # 공휴일 제외
    if _is_observed_market_holiday(now_et):
        return False

    return True



# ============================================================
# 한국 시장 (KRX) 관련 함수
# ============================================================

def get_korea_now():
    """
    현재 한국 표준시(KST, UTC+9) 기준 naive datetime을 반환합니다.
    """
    return datetime.utcnow() + timedelta(hours=9)


# 한국 음력 공휴일 (설날/추석 연휴와 부처님오신날) - 해당 연도의 양력 날짜
# key: 연도, value: 양력 공휴일(휴장일) 날짜 목록
# 설날(음력 1/1)과 추석(음력 8/15)은 전날~다음날 3일 연휴를 포함하여 명시합니다.
KOREA_LUNAR_HOLIDAYS = {
    2023: [
        datetime(2023, 1, 21), datetime(2023, 1, 22), datetime(2023, 1, 23),  # 설날
        datetime(2023, 5, 27),                                                  # 부처님오신날
        datetime(2023, 9, 28), datetime(2023, 9, 29), datetime(2023, 9, 30),  # 추석
    ],
    2024: [
        datetime(2024, 2, 9), datetime(2024, 2, 10), datetime(2024, 2, 11),  # 설날
        datetime(2024, 5, 15),                                                 # 부처님오신날
        datetime(2024, 9, 16), datetime(2024, 9, 17), datetime(2024, 9, 18),  # 추석
    ],
    2025: [
        datetime(2025, 1, 28), datetime(2025, 1, 29), datetime(2025, 1, 30),  # 설날
        datetime(2025, 5, 5),                                                   # 부처님오신날
        datetime(2025, 10, 5), datetime(2025, 10, 6), datetime(2025, 10, 7),  # 추석
    ],
    2026: [
        datetime(2026, 2, 16), datetime(2026, 2, 17), datetime(2026, 2, 18),  # 설날
        datetime(2026, 5, 24),                                                  # 부처님오신날
        datetime(2026, 9, 24), datetime(2026, 9, 25), datetime(2026, 9, 26),  # 추석
    ],
    2027: [
        datetime(2027, 2, 6), datetime(2027, 2, 7), datetime(2027, 2, 8),    # 설날
        datetime(2027, 5, 13),                                                  # 부처님오신날
        datetime(2027, 9, 14), datetime(2027, 9, 15), datetime(2027, 9, 16),  # 추석
    ],
    2028: [
        datetime(2028, 1, 25), datetime(2028, 1, 26), datetime(2028, 1, 27),  # 설날
        datetime(2028, 5, 1),                                                   # 부처님오신날
        datetime(2028, 10, 2), datetime(2028, 10, 3), datetime(2028, 10, 4),  # 추석
    ],
    2029: [
        datetime(2029, 2, 12), datetime(2029, 2, 13), datetime(2029, 2, 14),  # 설날
        datetime(2029, 5, 21),                                                  # 부처님오신날
        datetime(2029, 9, 21), datetime(2029, 9, 22), datetime(2029, 9, 23),  # 추석
    ],
}


def _korea_observed_date(month, day, year):
    """
    한국 대체공휴일(휴장일): 공휴일이 주말(토/일)이면 다음 평일이 대체 휴장일이 됩니다.
    """
    holiday = datetime(year, month, day)
    if holiday.weekday() < 5:  # 평일이면 그대로
        return holiday
    # 주말이면 다음 평일로 이동 (대체공휴일)
    next_day = holiday + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    return next_day


def _is_korea_market_holiday(date):
    """
    주어진 날짜가 한국 증시 공휴일(휴장일)인지 확인합니다.
    양력 고정 공휴일 + 음력 공휴일(설날/추석/부처님오신날) 포함.
    """
    year = date.year

    # 음력 공휴일 (연휴 포함 날짜로 직접 명시)
    if year in KOREA_LUNAR_HOLIDAYS:
        for h in KOREA_LUNAR_HOLIDAYS[year]:
            if h.date() == date:
                return True

    # 양력 고정 공휴일 (대체공휴일 포함)
    fixed_holidays = [
        (year, 1, 1),    # 신정
        (year, 3, 1),    # 삼일절
        (year, 5, 5),    # 어린이날
        (year, 6, 6),    # 현충일
        (year, 8, 15),   # 광복절
        (year, 10, 3),   # 개천절
        (year, 10, 9),   # 한글날
        (year, 12, 25),  # 기독탄신일
    ]
    for y, m, d in fixed_holidays:
        if _korea_observed_date(m, d, y).date() == date:
            return True

    return False


def is_korea_trading_day(now_kst=None):
    """
    현재(한국 표준시 기준)가 한국 증시 거래일인지 확인합니다.
    주말 및 한국 공휴일이면 False를 반환합니다.
    """
    if now_kst is None:
        now_kst = get_korea_now()

    # 주말 제외
    if now_kst.weekday() >= 5:
        return False

    # 공휴일 제외
    if _is_korea_market_holiday(now_kst.date()):
        return False

    return True


if __name__ == "__main__":
    now_et = get_us_eastern_now()
    print(f"현재 미국 동부 시간: {now_et.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"미국 거래일 여부: {is_us_trading_day(now_et)}")

    now_kst = get_korea_now()
    print(f"현재 한국 시간: {now_kst.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"한국 거래일 여부: {is_korea_trading_day(now_kst)}")
