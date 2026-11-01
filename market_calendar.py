# market_calendar.py
# 한국 및 미국 시장 개장일 및 캘린더 유틸리티

from datetime import datetime

def is_market_open(market: str = "KR", target_date: datetime = None) -> bool:
    """
    주어진 시장(KR/US)이 해당 날짜에 개장하는지 여부를 반환합니다.
    주말(토, 일)은 기본적으로 휴장으로 처리합니다.
    """
    if target_date is None:
        target_date = datetime.now()
    
    # 요일 확인 (0: 월요일 ~ 6: 일요일)
    weekday = target_date.weekday()
    
    # 주말(토요일: 5, 일요일: 6)인 경우 휴장
    if weekday >= 5:
        return False
        
    return True
