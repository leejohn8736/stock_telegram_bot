보관 문서: 아래 내용은 정리 이전의 설계·진행 이력입니다. 2026-09-09에 테스트 파일과 백테스트 실행 도구를 삭제했습니다. 현재 실행 방법은 README.md, 파일 구성은 doc/file_tree.md를 참고하세요.

# 📊 진행 상황 (progress.md)

프로젝트: 텔레그램 주식 알림 봇 (Stock Alert Telegram Bot) 작성일: 2026-08-09 (코드/커밋 분석 기준) Git 상태: `main` 브랜치, 로컬이 `origin/main` 보다 3커밋 앞섬 (push 대기)

## 1. 요약 (한눈에 보기)

| 항목 | 상태 | 비고 |
| :--- | :--- | :--- |
| 전체 기능 구현 | ✅ 거의 완료 | 기획서(spec.md) 5장의 기능 전부 구현 |
| 자동 알림 체계 | ✅ 완료 | 기술적/가격/신고가/극단조건/마감/주간 6종 |
| 예측 엔진 | ✅ 완료 (+개선 중) | 규칙 기반 8지표 점수화 |
| 백테스트 검증 | 🔄 진행 중 | 1차 → 2차(로직 개선) 결과 확보, STRONG 전략 검증 추가 |
| 단위 테스트 | ⚠️ 39/40 통과 | 1건은 Windows 콘솔 cp949 인코딩 이슈 |
| 배포 | ✅ 준비 | Dockerfile, TZ=Asia/Seoul, 볼륨 구성 |

보관 문서: 아래 내용은 정리 이전의 설계·진행 이력입니다. 2026-09-09에 테스트 파일과 백테스트 실행 도구를 삭제했습니다. 현재 실행 방법은 README.md, 파일 구성은 doc/file_tree.md를 참고하세요.

# 📊 진행 상황 (progress.md)

프로젝트: 텔레그램 주식 알림 봇 (Stock Alert Telegram Bot) 작성일: 2026-08-09 (코드/커밋 분석 기준) Git 상태: `main` 브랜치, 로컬이 `origin/main` 보다 3커밋 앞섬 (push 대기)

## 1. 요약 (한눈에 보기)

| 항목 | 상태 | 비고 |
| :--- | :--- | :--- |
| 전체 기능 구현 | ✅ 거의 완료 | 기획서(spec.md) 5장의 기능 전부 구현 |
| 자동 알림 체계 | ✅ 완료 | 기술적/가격/신고가/극단조건/마감/주간 6종 |
| 예측 엔진 | ✅ 완료 (+개선 중) | 규칙 기반 8지표 점수화 |
| 백테스트 검증 | 🔄 진행 중 | 1차 → 2차(로직 개선) 결과 확보, STRONG 전략 검증 추가 |
| 단위 테스트 | ⚠️ 39/40 통과 | 1건은 Windows 콘솔 cp949 인코딩 이슈 |
| 배포 | ✅ 준비 | Dockerfile, TZ=Asia/Seoul, 볼륨 구성 |

🟢 사용자 명령어

- [x] `/start` `/help`
- [x] `/add` `/del` `/list` (구독 관리 + 현재가 + 추천 등급)
- [x] `/predict` (일봉 분석), `/predict_short` (5분봉), `/predict_weekly` (주봉) — `84bc325`
- [x] `/indices` `/korea` `/weekly` `/alerts` `/settopic`
- [x] 한글 별칭 + 단축 명령어 (예: `/예측` , `/ps` , `/주간` )
- [x] 텔레그램 명령어 자동완성 메뉴 (setMyCommands) — `4013bc9`
- [x] 단체방 봇 이름 접미사(`@봇이름`) 인식 — `cbafec3`

🟢 인프라/편의

- [x] `.env` 환경변수 설정, DB 자동 생성
- [x] 휴장일 판별 (미국 NYSE + 한국, 음력 포함) — `3b7a63c` 개선
- [x] Docker 배포 (python:3.11-slim, 볼륨)
- [x] 단체방 Topics(토픽) 알림 지원 — `e356caa`

## 3. 개발 타임라인 (git 커밋 기준)

| 날짜 | 커밋 수 | 주요 내용 |
| :--- | :---: | :--- |
| 2026-07-29 | 9 | 프로젝트 첫 업로드, 지표 계산·분석·가격 예측, list 등락/토픽 답장, 가격알림 (`a5900ba`), API 예외처리, 텔레그램 이스케이프/번호 구문 수정, 프리장 반영 |
| 2026-07-30 | 5 | 단축명령어, 단체방 토픽지정, 주요지수 모니터링/알림 (`d033cbf`), 전일종가 계산 수정, 달러인덱스+공포지수, 프리/애프터장 데이터 수정 |
| 2026-07-31 | 1 | 5분봉/주봉으로 예측하는 기능 추가 (`84bc325`) |
| 2026-08-04 | 4 | 종목명 표시·휴장일 알림 제외·나스닥100 지수, 변동폭 알림 로직 수정, 전일 종가 출력 수정, 프리마켓 가격 수정 |
| 2026-08-05 | 1 | 자동알림 on/off 기능 (`6169dc7`) |
| 2026-08-06 | 2 | 미국장 마감 알림 추가, 한국장 마감 알림 수정 |
| 2026-08-07 | 3 | 텔레그램 자동완성, 한국 지수 조회 수정, 단체방 봇이름 인식 수정 |
| 2026-08-08 | 2 | 역대/52주 신고가 알림 (`b523fdf`), 주간 리포트 (`5a442d5`) |
| 2026-08-09 | 5 | 휴장일 조회 스킵, 60/120 이평선 알림, 예측 로직 대규모 개선, 백테스트 프로그램(+STRONG 전략), 임계값 수정·매수/매도 알림·일일제한 |

📌 최근 3개 커밋 상세 (2026-08-09)

1. `2a4c230` 시장 예측 로직 대규모 수정
   - 지표 6종+ 구성 (RSI, MACD, 볼린저, SMA20/50 크로스, 거래량, 지지/저항, ATR, 시장 국면)
   - 시장 국면별 가중치 적응형 스코어링 도입 (추세장/횡보장)
2. `0f6ed16` 백테스트용 프로그램 추가
   - `backtest.py` (5년 일봉, look-ahead 방지, 정확도/수익률 검증)
   - 12개 종목 1차 결과 → `results/`
3. `5508a83` 백테스트 결과를 반영하여 예측 로직의 임계값 수정
   - 추세장 임계값 조정, 신뢰도 계산 개선, 스퀴즈 보정
   - 개선 후 2차 결과 → `results_v2/` (개선 전/후 비교 스크립트 포함)
4. `e81b822` 매수, 매도 알림 추가, 하루 최대알림 제한 추가 (미푸시)
   - STRONG BUY/SELL 권장 알림 + `recommendation_alerts` 테이블 일일 제한
   - `tests/test_recommendation_alerts.py` 추가


## 4. 백테스트 진행 현황

| 단계 | 내용 | 산출물 |
| :--- | :--- | :--- |
| 1차 | 예측 로직 개선 전, 12개 미국 대형주 (5년/horizon 5) | `results/*_backtest.json` |
| 2차 | 예측 로직 대규모 개선 후 동일 조건 재측정 | `results_v2/*_backtest.json` |
| STRONG 전략 | STRONG BUY/SELL "무조건 액션" 12종(한국 포함) 검증 | `results/strong_alert_backtest_results.json` |
| 비교 분석 | `python results/compare_analysis.py` → 개선 전/후 diff | `results/compare_analysis.py` |
| 종합 분석 | `python results/summary_analysis.py` → 12종 평균 | `results/summary_analysis.py` |

💡 코드 구조의 핵심 장점: 백테스트(`backtest.py`)가 실운영 로직(`predictor.predict_buy_sell_prices`)을 그대로 호출 → 로직 개선 후 백테스트만 다시 돌리면 즉시 회귀 검증 가능. 개선 전/후 비교 스크립트가 준비되어 있어 수치 근거로 의사결정하는 워크플로가 잡히 있음.


## 5. 테스트 현황

`python -m unittest discover -s tests -v`
→ Ran 40 tests, FAILED (errors=1)

🟢 통과 (39개):
- `test_alert_settings` : 자동 알림 on/off
- `test_backtest` : 데이터 수집, 백테스트 실행, Look-ahead 방지, 파라미터 반영
- `test_korea_market_close` : 한국 휴장일 판별, 마감 리포트 포맷(부분 데이터 포함)
- `test_recommendation_alerts` : STRONG 알림 전송/전송안함/일일 제한
- `test_scheduler_trading_day` : 휴장일 스캔 스킵, 티커별 거래일 판별

⚠️ 실패 1건 (환경 이슈, 로직 버グ 아님):
- `test_index_high_breakouts_skipped_on_weekend`
원인: Windows 콘솔 CP949 인코딩이 이모지(🟩) 출력 불가
→ `UnicodeEncodeError: 'cp949' codec can't encode character '\U0001f4c5'`
해결 후보: `PYTHONIOENCODING=utf-8` 환경변수, 또는 print 아스키 대체

참고: `main.py` 실행에는 영향 없음 (텔레그램 메시지는 UTF-8 전송). 테스트 출력이 터미널에 이모지로 찍히는 print 문들에서만 발생합니다.


## 6. 알려진 이슈 / 리스크

| # | 이슈 | 영향 | 해결 방안 |
| :--- | :--- | :--- | :--- |
| 1 | Windows CP949 콘솔에서 이모지 print 오류 | 테스트 1건 실패 | 테스트/로깅 출력 시 아스키 대체 또는 `PYTHONIOENCODING=utf-8` |
| 2 | 한국 음력 공휴일 수동 명시 (2024~2029) | 2030년부터 기능 저하 | 날짜 계산 라이브러리/알고리즘화 |
| 3 | `datetime.utcnow()` DeprecationWarning | Python 3.12+에서 경고 | `datetime.now(datetime.UTC)` 로 교체 |
| 4 | scheduler의 "오늘 보냄" 상태 일부 메모리 상주 | 재시작 시 중복 알림 가능 | DB 영속화 (부분 적용됨: 주간/신고가/권장 알림은 DB 기록) |
| 5 | SSL 검증 비활성장(`CERT_NONE`) | 보안상 위험 | 운영 환경에서 검증 활성화 옵션 제공 |
| 6 | `backtest_strong_alerts.py` 미커밋 | git untracked | 커밋/푸시 필요 |

## 7. 다음 작업 제안 (우선순위)

- [ ] P0 `backtest_strong_alerts.py` 포함 전체 커밋 push (`main` 이 origin보다 3커밋 앞섬)
- [ ] P0 `test_scheduler_trading_day.py` 이모지 인코딩 이슈 수정 → 40/40 테스트 그린
- [ ] P1 음력 공휴일 자동 계산 로직 도입
- [ ] P1 `datetime.utcnow()` Deprecation 제거
- [ ] P2 스케줄러 전송 상태 DB 영속화 완료 (미국/한국 마감, 극단조건)
- [ ] P2 백테스트 결과의 예측 정확도/수익률을 README·progress에 수치 정리
