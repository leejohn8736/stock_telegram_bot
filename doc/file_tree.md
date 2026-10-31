
# 프로젝트 파일 구성

## 운영 코드는 Python 3.11 이상 표준 라이브러리만 사용합니다.

| 파일 | 역할 |
| :--- | :--- |
| `main.py` | 설정 검증, DB 초기화, 봇과 스케줄러 실행 |
| `config.py` | 환경 변수와 .env 설정 |
| `database.py` | 구독, 알림 설정 및 전송 기록 저장 |
| `telegram_bot.py` | 텔레그램 명령어와 메시지 처리 |
| `scheduler.py` | 정기 종목 스캔과 독립적인 한국장 마감 조회 |
| `stock_api.py` | 종목 시세와 일봉·분봉·주봉 조회 |
| `toss_api.py` | 토스 Open API 인증 및 데이터 조회 |
| `market_indices.py` | 시장 지수, 환율, 마감 리포트 |
| `market_calendar.py` | 한국·미국 거래일과 시장 시각 |
| `indicators.py` | 기술적 지표 계산 |
| `predictor.py` | 지표 기반 가격 분석 |
| `weekly_report.py` | 지수와 구독 종목의 주간 리포트 |
| `Dockerfile`, `.dockerignore` | 운영 이미지 빌드와 제외 파일 |
| `.env.example`, `.gitignore` | 설정 예시와 Git 제외 파일 |
| `README.md` | 설치, 실행 및 기능 안내 |
| `doc/spec.md`, `doc/progress.md` | 정리 이전 설계·진행 이력 |
