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


### 2.3 데이터 흐름 (알람 1건 기준)

```text
[CHECK_INTERVAL 도래]
      ▼
scheduler._check_all_subscribed_stocks()
      ▼  (티커별 / 휴장일이면 스킵)
stock_api.fetch_stock_data(ticker)       # 현재가 + 일봉 OHLCV (프리/애프터 포함)
      ▼
_check_stock_high_breakouts()             # 역대/52주 신고가 돌파 체크
      ▼
_process_ticker_alerts()
      ├─ _check_price_change_alerts()     # 전일 종가 기준 변동 %
      └─ 기술적 이벤트 5종 (볼린저·SMA20/60/120·RSI)
---

## 4. 예측 알고리즘 (predictor.py)

### 4.1 사용 지표 8종
| 지표 | 파라미터 | 분석 목적 |
|------|----------|-----------|
| RSI | 14 (Wilder Smoothing) | 과매수/과매도 |
| MACD | 12, 26, 9 | 추세 전환 감지 |
| 볼린저 밴드 | 20, 2σ | 밴드 위치 + 변동성(스퀴즈) |
| SMA 20 vs 50 | 20/50 | 골든/데드크로스 (이전 값 비교로 실제 크로스 판정) |
| 거래량 추이 | 최근 5일 vs 이전 20일 평균 | 매수/매도세 동반 확인 |
| 지지/저항 | 프랙탈 피봇(좌우 2봉) + 터치 횟수 가중 | 지지/저항 근접도 |
| ATR | 14 | 변동성 기반 목표가/손절가 |
| 시장 국면 | SMA50 기준 상승/하락 비율 | 추세장↔횡보장 가중치 조정 |

### 4.2 점수화 (Score)
* 각 지표는 **-2 ~ +2 범위**의 가중치를 갖고 합산 (최대 ±9.0, 추세장 ±11.7)
* **시장 국면별 가중치 적응**:
  * 추세장(TRENDING_UP/DOWN): 추세 지표(MACD, SMA) ×1.3 / 평균회귀 지표(RSI, BB) ×0.8
  * 횡보장(RANGING): 반대 적용
* **볼린저 스퀴즈**(bandwidth < 0.05): 방향성 돌파 예상 → 신뢰도 +5, RSI 극단 신호 강화

### 4.3 추천 등급과 임계값
| 등급 | 조건 (추세장 판정 시 조정) |
|------|-----------------------------|
| STRONG BUY | score ≥ +3.5 |
| BUY | score ≥ +1.5 (횡보) / +1.0 (상승추세) / +2.5 (하락추세) |
| SELL | score ≤ -1.5 (횡보) / -2.5 (상승추세) / -1.0 (하락추세) |
| STRONG SELL | score ≤ -3.5 |
| HOLD | 그 외, 신뢰도 50 + 점수 방향성 반영 |

* **신뢰도(confidence)**: 25~95 제한. 지표 일치도(+10 / 충돌 -10), 매도 신호에 추세 확인 조건 등 보정.

### 4.4 목표가 계산 (ATR 기반 + 볼린저/지지저항 혼합)
* 1차: `매수목표 = 현재가 - 0.5×ATR`, `매도목표 = 현재가 + 1.0×ATR`, `손절 = 현재가 - 1.5×ATR`
* 2차: `볼린저 하단(60%) + 지지선(40%)` / `볼린저 상단(60%) + 저항선(40%)`
* ATR 비중(ATR/현재가): >5% 고변동 → ATR 30%/지지저항 70%, >2% → 50/50, 이하 → 70/30
* 최소/최대 보정: 매수 목표가 > 현재가면 `현재가×0.98`, 매도 목표가 < 현재가면 `현재가×1.02`

### 4.5 최근 개선 사항 (커밋 `5508a83` 반영)
* 추세장에서 잘못된 매도/매수 신호 감소를 위한 **국면별 임계값 조정** (2)
* 지표 일치도 기반 **신뢰도 계산 개선** (3)
* **스퀴즈 상태 보정** (5), **매도 신호에 추세 확인 추가** (6)

---

## 5. 자동 알림 기능 명세

### 5.1 개별 종목 기술적 알림 (주기 스캔)
| 알림 | 조건 (이전 봉 → 현재 봉) | 시그널 타입 |
|------|--------------------------|-------------|
| 볼린저 하단 이탈 | 종가가 하단선 상회 → 하회 | BB_LOWER |
| 볼린저 상단 돌파 | 종가가 상단선 하회 → 상회 | BB_UPPER |
| 20일선 이탈/회복 | 종가가 20일선(볼린저 중단) 기준 통과 | SMA_20_UNDER / SMA_20_OVER |
| 60일선 이탈/회복 | 종가가 60일선 기준 통과 | SMA_60_UNDER / SMA_60_OVER |
| 120일선 이탈/회복 | 종가가 120일선 기준 통과 | SMA_120_UNDER / SMA_120_OVER |
| RSI 과매도 진입 | RSI 30 이하 하향 돌파 | RSI_OVERSOLD |
| RSI 과매수 진입 | RSI 70 이상 상향 돌파 | RSI_OVERBOUGHT |
| 가격 변동 % | 전일 종가 대비 5/10/20% 변동 | daily_price_alerts (1일 1회) |
| 역대/52주 신고가 | 현재가 > 역대/52주 최고가 | ALL_TIME_HIGH / WEEK52_HIGH (1일 1회) |
| STRONG BUY/SELL | predictor 추천이 STRONG 등급 | recommendation_alerts (1일 최대 3회) |

> **중복 방지 원리**: `last_signals`에 (chat_id, ticker, signal_type, price) 저장. 상태가 해제(예: 가격이 다시 밴드 안)되면 `clear` → 다음 이벤트때 다시 알림.

### 5.2 시장 전역 알림 (인덱스)
| 알림 | 조건 |
|------|------|
| 극단적 시장 조건 | VIX ≥25/30, 공포탐욕 ≤15/25·≥75/85, 지수 ±2%, 환율 ±2%, 국채 0.1%p, 달러 ±1% (한국 장중 9~16시, 1일 1회) |
| 지수 신고가 | S&P500·NASDAQ·DOW·KOSPI·KOSDAQ 역대/52주 최고치 돌파 (1일 1회) |
| 미국장 마감 요약 | 미국 동부 16:00~17:59 (거래일만) |
| 한국장 마감 요약 | 한국 15:30부터 독립 스케줄러로 국내 종가 확인 즉시 전송 (거래일만, 미확인 시 조회 완료 10초 후 재시도, 네이버 우선/Yahoo 보조) |
| 주간 리포트 | 매주 월요일 08:00~09:59 (지난주 요약, 1주 1회) |

### 5.3 명령어 메뉴
| 명령어 | 별칭 | 기능 |
|--------|------|------|
| `/start` | - | 환영 메시지 |
| `/help` | - | 도움말 |
| `/add [티커]` | - | 종목 구독 (예: AAPL, 005930.KS) |
| `/del [티커]` | - | 구독 해제 |
| `/list` | - | 구독 목록 + 현재가 + 추천 등급 |
| `/predict [티커]` | `/예측`, `/p` | 일봉 기반 분석 리포트 |
| `/predict_short` | `/단기예측`, `/ps` | 5분봉 기반 단기 예측 |
| `/predict_weekly` | `/장기예측`, `/pw` | 주봉 기반 장기 예측 |
| `/settopic` | `/토픽설정`, `/topic` | 단체방 알림 토픽 설정 |
| `/indices` | `/지수`, `/시장`, `/i` | 시장 지수 현황 |
| `/korea` | `/한국`, `/kr` | KOSPI·KOSDAQ·환율 |
| `/alerts` | `/알림`, `/alert` | 자동 알림 on/off 설정/조회 |
| `/weekly` | `/주간`, `/주간리포트`, `/w` | 주간 요약 리포트 |

> 단체방에서 봇 이름이 붙은 `/korea@봇이름` 형식도 자동완성 접미사를 제거해 인식합니다.

---

## 6. 환경 설정 (config.py / .env)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `TELEGRAM_BOT_TOKEN` | (필수) | 봇 토큰, 없으면 시작 거부 |
| `CHECK_INTERVAL` | 3600 | 구독 종목 스캔 주기(초), 기본 1시간 |
| `DB_PATH` | data/stock_bot.db | SQLite 경로 (부모 디렉터리 자동 생성) |

> `.env` 파서는 외부 `python-dotenv` 없이 커스텀 구현 (주석/`#`, `'`/`"` 제거, `=` 분리).

---

## 7. 백테스트 명세

### 7.1 `backtest.py` (예측·방향 정확도 검증)
* **방법**: 각 시점 i에서 `data[0:i+1]`(과거만)로 `predict_buy_sell_prices()` 호출 → horizon일 뒤 실가격과 비교 (Look-ahead bias 방지)
* **정확도 기준**: 매수→상승, 매도→하락, 홀드→±2.5% 이내
* **CLI 옵션**: `--ticker/-t`, `--years/-y`(5), `--horizon/-H`(5), `--step/-s`(1), `--capital/-c`(10000), `--json`, `--output/-o`
* **출력 지표**: 전체/추천별/방향별/신뢰도별 정확도, 총 수익률, 승률, PF, MDD, 샤프, Buy&Hold 비교

### 7.2 `backtest_strong_alerts.py` (STRONG 신호 액션 전략)
* STRONG BUY→무조건 매수, STRONG SELL→무조건 매도, 그 외 무시
* 12개 종목(성향 다양화): AAPL/MSFT/GOOGL(대형기술), NVDA/TSLA/AMZN(고성장), JNJ/KO/PG(방어), XOM(에너지), 005930.KS/000660.KS(한국)
* 결과는 `results/strong_alert_backtest_results.json` + 종목별 JSON 저장

### 7.3 결과 분석 스크립트 (`results/`)
* `summary_analysis.py`: 12개 종목 종합 테이블 출력
* `compare_analysis.py`: **개선 전(results) vs 개선 후(results_v2)** 비교 (정확도/수익률/승률/PF/MDD/샤프)

---

## 8. 데이터 소스 요약

| 데이터 | 소스 API | 비고 |
|--------|----------|------|
| 주가 OHLCV / 현재가 | Yahoo Finance `v8/finance/chart` | 1m/5m/1d/1wk, `includePrePost=true` |
| 종목명 | Yahoo Finance | - |
| 공포탐욕지수 | CNN Money `production.dataviz.cnn.io` | 공식 API |
| VIX / 지수 / 국채 / 달러 | Yahoo Finance (^VIX, ^GSPC 등) | 표준시 DST 변환 포함 |
| USD/KRW 환율 | Daum 금융 `finance.daum.net/api/exchanges` | HTML 스크래핑 없이 경량 JSON 1회 호출 |

* 모든 `urllib` 요청에 재시도(기본 3회, 2초 간격) + 타임아웃 부여
* 종목당 스캔 사이 1초 쿨다운 → API rate limit 대응

---

## 9. 제약 사항 / 한계
1. **투자 조언 아님**: 순수 기술적 보조지표 기반 휴리스틱이므로 투자 판단 근거로 부적합 (README 명시)
2. **API 불안정**: Yahoo Finance는 비공식 API, 요청 제한·차단 가능 → 재시도/쿨다운으로 완화
3. **한국 음력 공휴일 하드코딩**: `market_calendar.py`에 2024~2029년 음력 휴일을 수동 명시 → 연도가 지나면 갱신 필요
4. **인메모리 상태**: 스케줄러의 "오늘 보냄" 여부(`last_*_date`)는 메모리 기반 → 재시작 시 중복 전송 가능성 (단, DB 기반 기록으로 보완된 항목도 다수)
5. **SSL 검증 비활성**: `ssl.CERT_NONE` 사용 → MITM 위험 (실운영 시 재고 필요)
6. **Windows 콘솔 이모지 인코딩**: CP949 터미널에서 이모지 출력 시 `UnicodeEncodeError` 발생 (단위 테스트 1건 비통과 원인 — 로직 문제 아님)

---

## 10. 향후 개선 제안 (제안 사항)
- [ ] 한국 음력 공휴일 자동 계산 (KST 기준 라이브러리 로직으로 대체)
- [ ] 중복 전송 상태를 DB 영속화 (재시작 후에도 1일 1회 보장)
- [ ] 백테스트 데이터 저장소 분리 및 CI 연동 (정기 정확도 리그레션)
- [ ] 외부 패키지 도입 옵션 (yfinance, python-telegram-bot 등) — 표준 라이브러리 전략과 상충 시 명시적 선택
- [ ] 프리마켓/애프터마켓 가격과 이전 봉 참조 정합성 개선
- [ ] Docker Compose / systemd 구성 문서화

|--------|------|------|
| `/start` | - | 환영 메시지 |
| `/help` | - | 도움말 |
| `/add [티커]` | - | 종목 구독 (예: AAPL, 005930.KS) |
| `/del [티커]` | - | 구독 해제 |
| `/list` | - | 구독 목록 + 현재가 + 추천 등급 |
| `/predict [티커]` | `/예측`, `/p` | 일봉 기반 분석 리포트 |
| `/predict_short` | `/단기예측`, `/ps` | 5분봉 기반 단기 예측 |
| `/predict_weekly` | `/장기예측`, `/pw` | 주봉 기반 장기 예측 |
| `/settopic` | `/토픽설정`, `/topic` | 단체방 알림 토픽 설정 |
| `/indices` | `/지수`, `/시장`, `/i` | 시장 지수 현황 |
| `/korea` | `/한국`, `/kr` | KOSPI·KOSDAQ·환율 |
| `/alerts` | `/알림`, `/alert` | 자동 알림 on/off 설정/조회 |
| `/weekly` | `/주간`, `/주간리포트`, `/w` | 주간 요약 리포트 |

> 단체방에서 봇 이름이 붙은 `/korea@봇이름` 형식도 자동완성 접미사를 제거해 인식합니다.
      └─ _check_recommendation_alerts()   # STRONG BUY/SELL → 권장가 알림
      ▼ (중복 방지: last_signals / 일일 제한: recommendation_alerts 테이블)
telegram_bot.send_message(chat_id, ...)   # chat_topics 토픽 설정 반영

---
3. 데이터 모델 (SQLite 스키마)테이블PK용도subscriptions(chat_id, ticker)구독자-종목 매핑, ticker는 대문자 저장last_signals(chat_id, ticker, signal_type)기술적 시그널 중복 전송 방지 (발생→해제 시 삭제)last_prices(chat_id, ticker)마지막 가격/마지막 알림가/변동 임계값(%) 관리daily_price_alerts(chat_id, ticker, alert_date, threshold_pct, direction)전일 종가 기준 5/10/20% 변동 알림 (1일 1회)high_breakout_alerts(chat_id, ticker, alert_type, alert_date)역대/52주 신고가 알림 (1일 1회)weekly_report_sends(chat_id, week_start)주간 리포트 1주 1회 전송 기록chat_topics(chat_id)단체방 알림 토픽(message_thread_id) 저장chat_alert_settings(chat_id)자동 알림 on/off (기본 1=ON)recommendation_alerts(chat_id, ticker, alert_date, alert_type)STRONG BUY/SELL 권장 알림 (일일 최대 3회)⚠️ daily_price_alerts 는 구버전 PK 구조 → 새 PK 구조로 자동 마이그레이션 코드 포함.
