보관 문서: 아래 내용은 정리 이전의 설계·진행 이력입니다. 2026-09-09에 테스트 파일과 백테스트 실행 도구를 삭제했습니다. 현재 실행 방법은 README.md, 파일 구성은 doc/file_tree.md를 참고하세요.

# 📋 기획 사양서 (spec.md)

## 1. 프로젝트 개요

### 1.1 한 줄 소개
텔레그램 봇을 통해 관심 종목(미국/한국 주식)의 기술적 지표 기반 가격 알림과 매수/매도 예측 리포트를 자동으로 제공하는 개인용 주식 모니터링 봇.

### 1.2 목적 / 배경
* "특정 주식을 구독하면 시세를 일일이 확인하지 않아도 주요 기술적 이벤트가 발생했을 때 알림을 받고 싶다"
* 기술적 분석에 대한 전문 지식이 없어도 이해하기 쉬운 한국어 알림(지표 상황 → 권장 행동)을 제공
* 백테스트로 예측 로직의 정확도를 지속적으로 검증/개선하는 구조

### 1.3 핵심 가치 / 설계 철학

| 원칙 | 설명 |
| :--- | :--- |
| 외부 의존성 제로 | Python 표준 라이브러리만 사용 (`urllib`, `sqlite3`, `json`, `math`, `threading`) |
| 실전 로직 재사용 | 백테스트가 운영 로직(`predict_buy_sell_prices`)을 그대로 호출 → 검증 신뢰성 확보 |
| 알림 스팸 방지 | 모든 자동 알림에 "1일 N회" / "상태 변화 시에만" 중복 방지 로직 적용 |
| 휴장일 인식 | 미국/한국 공휴일·주말에는 API 호출 자체를 생략 (비용·사용성) |
| 설정 유연성 | `.env` 환경변수를 통한 조정 (체크 주기, DB 경로, 하루 최대 알림 수) |

### 1.4 대상 사용자

* 미국 주식(AAPL, TSLA 등)과 한국 주식(삼성전자, SK하이닉스 등)을 동시에 모니터링하는 개인 투자자
* 텔레그램 단체방에서 토픽별(스레드) 알림을 받고 싶은 사용자

## 2. 시스템 구성

### 2.1 구성 요소

| 구성 | 기술 | 비고 |
| :--- | :--- | :--- |
| 실행 주체 | Python 3.11+ (코드에서 3.8+, Docker는 3.11-slim) | 표준 라이브러리만 |
| 봇 서버 | Telegram Bot API (Long Polling) | `urllib` 직접 호출, SSL 인증 무시 옵션 |
| 데이터 소스 | Yahoo Finance chart API / CNN Money Fear&Greed API | 공식·비공식 REST |
| 저장소 | SQLite (`data/stock_bot.db`) | 멀티스레드 고려해 연결을 요청마다 open/close |
| 배포 | Docker (`python:3.11-slim`, `TZ=Asia/Seoul`) | `/app/data` 볼륨 마운트 |
| 스케줄링 | `threading.Thread` 데몬 + `time.sleep` 루프 | 봇 폴링과 별도 스레드 |

### 2.2 실행 흐름 (`main.py`)

```text
main.py
├─ 1) check_environment()   → TELEGRAM_BOT_TOKEN 필수 확인, 없으면 종료
├─ 2) database.init_db()    → 테이블 9개 생성 (스키마 마이그레이션 포함)
├─ 3) TelegramBot()         → set_my_commands() + start_polling() (Long Polling 스레드)
├─ 4) AlertScheduler(bot)   → start() (백그라운드 스캔 스레드)
└─ 5) 메인 스레드 대기 + Ctrl+C 시 graceful shutdown



### 2.3 데이터 흐름 (알림 1건 기준)

