import urllib.request
import json
import ssl
import time
import threading
import html
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from config import TELEGRAM_BOT_TOKEN
import database
import stock_api
import predictor
import market_indices
import weekly_report
import market_events

# Telegram BotCommand list for auto-complete menu
COMMANDS = [
    {"command": "start", "description": "환영 메시지 표시"},
    {"command": "help", "description": "명령어 도움말 표시"},
    {"command": "add", "description": "관심 주식 등록 (예: /add AAPL)"},
    {"command": "del", "description": "관심 주식 삭제 (예: /del AAPL)"},
    {"command": "list", "description": "구독 중인 주식 리스트와 현재가"},
    {"command": "predict", "description": "기술적 분석 및 매수/매도 예측 (예: /predict TSLA)"},
    {"command": "predict_short", "description": "5분봉 단기 예측 (예: /ps AAPL)"},
    {"command": "predict_weekly", "description": "주봉 장기 예측 (예: /pw AAPL)"},
    {"command": "indices", "description": "시장 인덱스/공포탐욕지수/환율 조회"},
    {"command": "korea", "description": "한국 시장(KOSPI/KOSDAQ) 및 원/달러 환율"},
    {"command": "alerts", "description": "가격 변동 알림 설정/조회"},
    {"command": "alarms", "description": "🔔 알람 수신 수준 선택 (버튼으로 설정)"},
    {"command": "weekly", "description": "지난주 주요지수 및 관심종목 주간 요약 리포트"},
    {"command": "calendar", "description": "이번 주 한미 증시 휴장·만기·조기폐장 일정"},
]

class TelegramBot:
    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.offset = None
        self.is_running = False
        self.polling_thread = None
        self._stop_event = threading.Event()
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
    def _api_call(self, method, data=None):
        """Make HTTP request to Telegram Bot API."""
        if not self.token:
            print("Telegram Bot Token is not configured. API call skipped.")
            return None
            
        url = f"{self.base_url}/{method}"
        headers = {"Content-Type": "application/json"}
        
        req_data = None
        if data:
            req_data = json.dumps(data).encode("utf-8")
            
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST" if data else "GET")
        
        try:
            with urllib.request.urlopen(req, context=self.ssl_context, timeout=25) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
                else:
                    print(f"Telegram API Error on {method}: HTTP Error {response.status}: {response.read().decode('utf-8')}")
        except urllib.error.HTTPError as e:
            print(f"Telegram API Error on {method}: HTTP Error {e.code}: {e.reason} - {e.file.read().decode('utf-8')}")
        except Exception as e:
            print(f"Telegram API Error on {method}: {e}")
        return None

    def send_message(self, chat_id, text, parse_mode="HTML", reply_to_message_id=None, message_thread_id=None, reply_markup=None):
        """
        Send message to chat_id.
        Returns message_id on success, None on failure.
        """
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
            
        result = self._api_call("sendMessage", payload)
        if result and result.get("ok") and result.get("result"):
            return result["result"].get("message_id")
        return None

    def delete_message(self, chat_id, message_id):
        """
        Deletes a message by its message_id.
        """
        payload = {
            "chat_id": chat_id,
            "message_id": message_id
        }
        return self._api_call("deleteMessage", payload)

    @contextmanager
    def _loading_message(self, chat_id, text, **reply_options):
        """Always remove the progress message, including when a handler fails."""
        message_id = self.send_message(chat_id, text, **reply_options)
        try:
            yield
        finally:
            if message_id is not None:
                try:
                    self.delete_message(chat_id, message_id)
                except Exception as exc:
                    print(f"Error removing loading message: {exc}")

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        """
        Acknowledges a callback query (인라인 버튼 클릭 시 로딩 표시 제거 및 알림 표시).
        """
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        return self._api_call("answerCallbackQuery", payload)

    def edit_message_text(self, chat_id, message_id, text, parse_mode="HTML", reply_markup=None):
        """
        Edits an existing message's text (인라인 키보드 상태 갱신용).
        """
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._api_call("editMessageText", payload)

    def get_updates(self):
        """
        Retrieves new messages via Long Polling; returns None on API failure.
        """
        payload = {"timeout": 20}
        if self.offset is not None:
            payload["offset"] = self.offset
            
        updates = self._api_call("getUpdates", payload)
        if updates and updates.get("ok"):
            return updates.get("result", [])
        return None

    def set_my_commands(self):
        """
        setMyCommands API를 호출하여 봇 명령어를 등록합니다.
        등록된 명령어는 사용자가 "/" 를 입력하면 자동완성 메뉴로 표시되고,
        채팅 하단 메뉴 버튼(≡)에서도 확인/선택할 수 있습니다.
        """
        if not self.token:
            print("Cannot set bot commands: TELEGRAM_BOT_TOKEN is empty.")
            return None
        payload = {"commands": COMMANDS}
        result = self._api_call("setMyCommands", payload)
        if result and result.get("ok"):
            print(f"✅ Registered {len(COMMANDS)} bot commands for autocomplete (/ 메뉴).")
        else:
            print("⚠️ Failed to set bot commands.")
        return result

    def start_polling(self):
        """
        Starts the long polling thread.
        """
        if self.polling_thread is not None and self.polling_thread.is_alive():
            return
        if not self.token:
            print("Cannot start Telegram Bot: TELEGRAM_BOT_TOKEN is empty.")
            return
            
        self.is_running = True
        self._stop_event.clear()
        self.polling_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.polling_thread.start()
        print("Telegram Bot Polling Thread Started.")

    def stop_polling(self):
        """
        Stops the long polling thread.
        """
        self.is_running = False
        self._stop_event.set()
        print("Stopping Telegram Bot Polling...")

    def _poll_loop(self):
        """
        Infinite polling loop running in the background thread.
        """
        while self.is_running:
            try:
                updates = self.get_updates()
                if updates is None:
                    self._stop_event.wait(5)
                    continue
                for update in updates:
                    if not self.is_running:
                        break
                    self.offset = update["update_id"] + 1
                    try:
                        if "message" in update:
                            self._handle_message(update["message"])
                        if "callback_query" in update:
                            self._handle_callback_query(update["callback_query"])
                    except Exception as e:
                        print(f"Error handling update {update['update_id']}: {e}")
            except Exception as e:
                print(f"Error in Polling Loop: {e}")
                self._stop_event.wait(5)
            self._stop_event.wait(0.5)

    def _handle_message(self, message):
        """
        Processes incoming messages and dispatches commands.
        단체방 Topics(스레드) 기능을 지원합니다.
        """
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()
        message_id = message.get("message_id")
        
        # Topics(스레드)가 활성화된 그룹에서 메시지가 속한 스레드 ID
        # message_thread_id: 스레드 ID (일반 메시지면 None)
        message_thread_id = message.get("message_thread_id")
        
        if not chat_id or not text:
            return
            
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            command = parts[0].lower()
            # 단체방에서 봇을 호출하면 "/korea@UKC_Stock_Bot" 처럼 명령어 뒤에
            # "@봇이름" 접미사가 자동으로 붙습니다. 이 접미사를 제거해 명령어를 인식합니다.
            if "@" in command:
                command = command.split("@", 1)[0]
            arg = parts[1].strip() if len(parts) > 1 else ""
            
            # 단체방 Topics 지원: 봇의 응답을 같은 스레드에 보냄
            self._dispatch_command(chat_id, command, arg, reply_to_message_id=message_id, message_thread_id=message_thread_id)

    def _dispatch_command(self, chat_id, command, arg, reply_to_message_id=None, message_thread_id=None):
        """
        Routes the command to the appropriate handler.
        모든 핸들러에 reply_to_message_id와 message_thread_id를 전달합니다.
        한국어 명령어도 지원합니다.
        """
        # 한국어 명령어 매핑
        command_map = {
            "/start": "/start",
            "/시작": "/start",
            "/help": "/help",
            "/도움말": "/help",
            "/add": "/add",
            "/추가": "/add",
            "/a": "/add",
            "/del": "/del",
            "/d": "/del",
            "/삭제": "/del",
            "/list": "/list",
            "/목록": "/list",
            "/l": "/list",
            "/predict": "/predict",
            "/예측": "/predict",
            "/p": "/predict",
            "/predict_short": "/predict_short",
            "/단기예측": "/predict_short",
            "/ps": "/predict_short",
            "/predict_weekly": "/predict_weekly",
            "/장기예측": "/predict_weekly",
            "/pw": "/predict_weekly",
            "/settopic": "/settopic",
            "/토픽설정": "/settopic",
            "/topic": "/settopic",
            "/indices": "/indices",
            "/지수": "/indices",
            "/시장": "/indices",
            "/i": "/indices",
            "/korea": "/korea",
            "/한국": "/korea",
            "/kr": "/korea",
            "/alerts": "/alerts",
            "/알림": "/alerts",
            "/alert": "/alerts",
            "/alarms": "/alarms",
            "/알람": "/alarms",
            "/알람설정": "/alarms",
            "/weekly": "/weekly",
            "/주간": "/weekly",
            "/주간리포트": "/weekly",
            "/w": "/weekly",
            "/calendar": "/calendar",
            "/일정": "/calendar"
        }
        
        # 명령어 정규화
        normalized_cmd = command_map.get(command, command)
        
        if normalized_cmd == "/start":
            self._handle_start(chat_id, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/help":
            self._handle_help(chat_id, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/add":
            self._handle_add(chat_id, arg, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/del":
            self._handle_del(chat_id, arg, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/list":
            self._handle_list(chat_id, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/predict":
            self._handle_predict(chat_id, arg, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/predict_short":
            self._handle_predict_short(chat_id, arg, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/predict_weekly":
            self._handle_predict_weekly(chat_id, arg, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/settopic":
            self._handle_settopic(chat_id, arg, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/indices":
            self._handle_indices(chat_id, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/korea":
            self._handle_korea(chat_id, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/alerts":
            self._handle_alerts(chat_id, arg, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/alarms":
            self._handle_alarm_settings(chat_id, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/calendar":
            self._handle_calendar(chat_id, reply_to_message_id, message_thread_id)
        elif normalized_cmd == "/weekly":
            self._handle_weekly(chat_id, reply_to_message_id, message_thread_id)
        else:
            self.send_message(chat_id, "⚠️ 알 수 없는 명령어입니다. 사용 가능한 명령어를 보려면 /help 를 입력하세요.",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_start(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        welcome_text = (
            "<b>📈 주식 모니터링 & 알림 봇에 오신 것을 환영합니다!</b>\n\n"
            "이 봇은 등록하신 관심 주식을 주기적으로 모니터링하여 "
            "<b>20일선/60일선/120일선 이탈, 볼린저 밴드 하단 돌파, RSI 과매수/과매도</b> 등 "
            "주요 기술적 이벤트 발생 시 자동으로 알림을 전송해 줍니다.\n\n"
            "또한, 순수 기술적 지표들을 종합 분석하여 최적의 매수/매도 가격과 "
            "매매 추천 정보를 언제든지 바로 예측해서 제공합니다.\n\n"
            "<b>ℹ️ 시작하려면 /help 를 입력하여 사용 가능한 명령어 목록을 확인하세요!</b>"
        )
        self.send_message(chat_id, welcome_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_help(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        help_text = (
            "<b>🛠️ 사용 가능한 명령어 목록</b>\n\n"
            "📌 <b>/add [티커]</b> 또는 <b>/추가 [티커]</b> - 관심 주식을 등록합니다.\n"
            "<i>예시: /add AAPL (미국), /추가 005930.KS (삼성전자)</i>\n\n"
            "📌 <b>/del [티커]</b> 또는 <b>/삭제 [티커]</b> - 관심 주식을 삭제합니다.\n"
            "<i>예시: /del AAPL, /삭제 TSLA</i>\n\n"
            "📌 <b>/list</b> 또는 <b>/목록</b> - 내가 구독 중인 주식 리스트와 현재가를 조회합니다.\n"
            "<i>전일 종가 대비 변동률도 함께 표시됩니다.</i>\n\n"
            "📌 <b>/predict [티커]</b> 또는 <b>/예측 [티커]</b> - 특정 주식의 기술적 지표 분석 및 매수/매도 예측 가격을 즉시 조회합니다.\n"
            "<i>예시: /predict TSLA, /예측 AAPL</i>\n\n"
            "📌 <b>/predict_short [티커]</b> 또는 <b>/ps [티커]</b> - <b>5분봉</b> 기준 단기 예측을 제공합니다.\n"
            "<i>단기 트레이딩(수시간~1일)에 적합한 분석을 제공합니다.</i>\n"
            "<i>예시: /ps AAPL, /단기예측 TSLA</i>\n\n"
            "📌 <b>/predict_weekly [티커]</b> 또는 <b>/pw [티커]</b> - <b>주봉</b> 기준 장기 예측을 제공합니다.\n"
            "<i>장기 투자(수주~3개월)에 적합한 분석을 제공합니다.</i>\n"
            "<i>예시: /pw AAPL, /장기예측 TSLA</i>\n\n"
            "📌 <b>/indices</b> 또는 <b>/지수</b> 또는 <b>/시장</b> - 현재 시장 인덱스 현황을 조회합니다.\n"
            "<i>공포탐욕지수, VIX, 주요 지수(S&P500, NASDAQ, NASDAQ 100, DOW), 환율, 국채수익률을 한눈에 확인</i>\n\n"
            "📌 <b>/korea</b> 또는 <b>/한국</b> 또는 <b>/kr</b> - 한국 시장(KOSPI/KOSDAQ) 및 원/달러 환율을 조회합니다.\n\n"
            "📌 <b>/weekly</b> 또는 <b>/주간</b> 또는 <b>/w</b> - 지난주 주요 지수 및 관심 종목의 주간 변화 요약 리포트를 제공합니다.\n"
            "📌 <b>/calendar</b> 또는 <b>/일정</b> - 이번 주 한국·미국 증시 주요 일정 (월요일 08:00 자동 알림).\n"
            "<i>주요 지수(S&P500, NASDAQ, DOW, KOSPI, KOSDAQ)와 구독 중인 종목의 주간 변동을 한눈에 확인</i>\n\n"
            "📌 <b>/alarms</b> 또는 <b>/알람</b> - 알람 수신 수준을 버튼으로 선택합니다.\n"
            "<i>모두 끄기 / 시장 알림만 / 중요 알림만 / 모든 알람 중에서 선택 가능</i>\n\n"
            "📌 <b>/alerts on|off</b> 또는 <b>/알림 on|off</b> - 자동 알림을 켜거나 끕니다.\n\n"
            "📌 <b>/help</b> 또는 <b>/도움말</b> - 이 도움말을 표시합니다.\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🔔 <b>자동 알림 기능</b>\n\n"
            "🎚 <b>알람 수신 수준</b> (<code>/알람</code>)\n"
            "버튼 클릭으로 받을 알람의 수준을 선택할 수 있습니다.\n"
            "🔕 모두 끄기 / 🌍 시장 알림만 / ⭐ 중요 알림만 / 🔔 모든 알람\n\n"
            "📊 <b>가격 변동 알림</b>\n"
            "등록된 종목이 전일 종가 대비 <b>5%, 10%, 20%</b> 이상 변동하면 자동으로 알림을 보내드립니다.\n"
            "동일한 변동폭(임계값)과 방향(상승/하락) 조합은 하루에 1번만 알림이 전송됩니다.\n"
            "예: 5% 상승 후 10% 상승 또는 5% 하락은 새로 알립니다. 10%에서 5%로 되돌아오면 추가 알림은 없습니다. 당일 분봉으로 지나간 도달도 확인하며, 시세 지연에 따라 알림이 늦을 수 있습니다.\n\n"
            "📉 <b>이동평균선 이탈/회복 알림</b>\n"
            "등록된 종목이 <b>20일선, 60일선, 120일선</b>을 이탈하거나 다시 회복하면 자동으로 알림을 보내드립니다.\n"
            "각 이평선은 서로 다른 주기(단기/중기/장기)의 추세 전환 신호로 활용됩니다.\n"
            "동일한 신호는 하루에 1번만 전송됩니다. (하루에 여러 번 반복되어도 1번만 알림)\n\n"
            "🇺🇸 <b>미국장 마감 요약 (매일 5~6시)</b>\n"
            "매일 미국장이 끝난 후 (한국 시간 오전 5~6시 경) 당일 시장 흐름을 요약하여 자동으로 전송합니다.\n"
            "공포탐욕지수, VIX, 주요 지수, 환율 등을 확인하고 하루를 마무리하세요!\n\n"
            "🇰🇷 <b>한국장 마감 요약 (매일 15:30 이후)</b>\n"
            "매일 한국장이 끝난 후 (한국 시간 오후 3시 30분 이후) 코스피/코스닥 등락과 원/달러 환율을 자동으로 전송합니다.\n"
            "공포탐욕지수, VIX, 미국 지수 등 참고 정보도 함께 제공됩니다.\n\n"
            "🚨 <b>극단적 시장 조건 알림</b>\n"
            "다음 조건 감지 시 자동으로 알림을 보내드립니다:\n"
            "• VIX 25 이상 (변동성 확대)\n"
            "• 공포탐욕지수 25 이하 또는 75 이상\n"
            "• 주요 지수 2% 이상 급등락\n"
            "• 원/달러 환율 2% 이상 급변동\n\n"
            "🟢🔴 <b>매수/매도 권장 알림</b>\n"
            "기술적 지표가 <b>STRONG BUY</b> 또는 <b>STRONG SELL</b> 신호를 보일 때\n"
            "권장 매수/매도 가격과 함께 자동으로 알림을 보내드립니다.\n"
            "• 🟢🔥 <b>STRONG BUY</b>: 무조건 매수해야 하는 상황 → 권장 매수 가격 제시\n"
            "• 🔴🔥 <b>STRONG SELL</b>: 무조건 매도해야 하는 상황 → 권장 매도 가격 제시\n"
            "STRONG BUY/STRONG SELL 각 유형당 하루 <b>1회</b>씩 전송됩니다. (과도한 알림 방지)\n\n"
            "🏆 <b>최고치 돌파 알림</b>\n"
            "등록된 종목 또는 주요 지수(S&P 500, NASDAQ, DOW, KOSPI, KOSDAQ)가\n"
            "<b>역대 최고가</b> 또는 <b>52주 최고가</b>를 돌파하면 자동으로 알림을 보내드립니다.\n"
            "동일한 종목/지수의 최고치 돌파 알림은 하루에 1번만 전송됩니다.\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "💡 <b>티커 팁:</b>\n"
            "- 미국 주식: 티커명 그대로 입력 (AAPL, TSLA, MSFT)\n"
            "- 한국 코스피: 종목코드.KS 입력 (005930.KS, 000660.KS)\n"
            "- 한국 코스닥: 종목코드.KQ 입력 (247540.KQ)"
        )
        self.send_message(chat_id, help_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_add(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/add [티커]</code> 형태로 입력해 주세요.\n예시: <code>/add AAPL</code>",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
            
        ticker = arg.upper().strip()
        self.send_message(chat_id, f"🔍 <code>{ticker}</code>의 유효성을 검증하는 중입니다...",
                        reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        
        # Validate stock ticker from API
        stock_data = stock_api.fetch_stock_data(ticker)
        if not stock_data:
            self.send_message(
                chat_id, 
                f"❌ <code>{ticker}</code>는 유효하지 않은 티커이거나 데이터를 가져올 수 없습니다.\n"
                f"티커명이 올바른지 확인해 주세요. (예: AAPL, 005930.KS)",
                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id
            )
            return
            
        # Add to database
        success = database.add_subscription(chat_id, ticker)
        stock_name = stock_data.get("name", ticker)
        if success:
            self.send_message(
                chat_id, 
                f"✅ <b>{html.escape(stock_name)}</b> (<code>{ticker}</code>)가 성공적으로 등록되었습니다!\n"
                f"실시간 현재가: <b>{stock_data['current_price']:.2f} {stock_data['currency']}</b>\n"
                f"주기적으로 주가를 검사하여 특이사항 발생 시 알림을 보내드릴게요.",
                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id
            )
        else:
            self.send_message(chat_id, f"ℹ️ <code>{ticker}</code>는 이미 등록된 관심 주식입니다.",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_del(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/del [티커]</code> 형태로 입력해 주세요.\n예시: <code>/del AAPL</code>",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
            
        ticker = arg.upper().strip()
        success = database.remove_subscription(chat_id, ticker)
        
        if success:
            # Clear signal history to free up memory
            database.clear_all_signals_for_ticker(chat_id, ticker)
            self.send_message(chat_id, f"✅ <code>{ticker}</code>가 관심 주식에서 성공적으로 삭제되었습니다.",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        else:
            self.send_message(chat_id, f"⚠️ 등록되지 않은 티커입니다. 등록 정보는 <b>/list</b> 명령어로 확인해 보세요.",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_list(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        subscriptions = database.get_user_subscriptions(chat_id)
        if not subscriptions:
            self.send_message(
                chat_id, 
                "📂 구독 중인 관심 주식이 없습니다.\n"
                "<code>/add [티커]</code> 명령어로 먼저 등록해 보세요!",
                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id
            )
            return
            
        # 성공/실패 여부와 관계없이 조회 중 메시지를 정리합니다.
        with self._loading_message(chat_id, "🔄 구독 중인 종목들의 현재가를 가져오는 중...",
                        reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id):

            # 한국 시간 (KST = UTC + 9)
            kst_offset = 9 * 60 * 60
            now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + kst_offset))
            lines = [f"<b>📋 나의 관심 주식 리스트</b>\n⏱ 조회시간: <code>{now_str}</code>\n"]

            # 토스증권 Open API 설정 시: 배치 요청 1회로 전체 현재가를 미리 조회 (속도 최적화)
            # (부분 실패 시 누락 종목만 개별 조회로 보완됨)
            price_cache = stock_api.fetch_current_prices_batch(subscriptions) if stock_api.is_toss_enabled() else None

            # 최대 8개 종목을 동시에 조회하고 구독 순서대로 출력합니다.
            max_workers = min(8, max(1, len(subscriptions)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                row_futures = [
                    executor.submit(self._build_list_row, idx, ticker, price_cache)
                    for idx, ticker in enumerate(subscriptions, 1)
                ]
                for future in row_futures:
                    lines.append(future.result())

            self.send_message(chat_id, "\n".join(lines), reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _build_list_row(self, idx, ticker, price_cache=None):
        """
        /list 출력의 종목 1줄을 생성합니다. (병렬 실행용 — 항상 문자열을 반환)

        전체 데이터(fetch_stock_data)를 1회 조회해 현재가/전일종가/종목명/기술적 지표를
        모두 확보합니다. 기존에는 가벼운 현재가 조회와 전체 데이터 조회를 각각 순차 호출해
        요청이 2배 발생했으나, 전체 데이터에 동일한 정보가 모두 포함되어 있어 1회로 통합했습니다.
        전체 조회 실패 시에만 가벼운 현재가 조회로 폴백합니다.
        """
        price_cache = price_cache or {}
        try:
            # price_cache를 함께 전달해 토스 사용 시 개별 현재가 재요청을 생략
            full_data = stock_api.fetch_stock_data(ticker, price_cache)
            if full_data:
                cached = price_cache.get(ticker) or {}
                price = cached.get("price")
                if price is None:
                    price = full_data["current_price"]
                prev_close = cached.get("previous_close")
                if prev_close is None or prev_close <= 0:
                    prev_close = full_data.get("previous_close")
                currency = cached.get("currency") or full_data["currency"]
                stock_name = full_data.get("name") or ticker

                change_str = self._build_change_str(price, prev_close)
                rec_str = self._build_recommendation_str(full_data)

                return (f"{idx}. <b>{html.escape(stock_name)}</b> (<code>{ticker}</code>): "
                        f"{price:.2f} {currency}{change_str}{rec_str}")

            # 전체 데이터 조회 실패 시: 가벼운 현재가 조회로 폴백
            price_data = price_cache.get(ticker) or stock_api.fetch_current_price_only(ticker)
            if price_data:
                price = price_data["price"]
                prev_close = price_data.get("previous_close")
                currency = price_data.get("currency") or "USD"
                change_str = self._build_change_str(price, prev_close)

                return (f"{idx}. <b>{html.escape(ticker)}</b> (<code>{ticker}</code>): "
                        f"{price:.2f} {currency}{change_str}")

            return f"{idx}. <code>{ticker}</code>: 데이터 로드 실패 ⚠️"
        except Exception as e:
            print(f"Error building list row for {ticker}: {e}")
            return f"{idx}. <code>{ticker}</code>: 데이터 로드 실패 ⚠️"

    @staticmethod
    def _build_change_str(price, prev_close):
        """전일 종가 대비 등락률 문자열을 생성합니다."""
        if prev_close is not None and prev_close > 0:
            pct_change = ((price - prev_close) / prev_close) * 100
            if abs(pct_change) < 0.01:
                return " (보합)"
            elif pct_change > 0:
                return f" (📈 <b>+{pct_change:.2f}%</b>)"
            else:
                return f" (📉 <b>{pct_change:.2f}%</b>)"
        return ""

    @staticmethod
    def _build_recommendation_str(full_data):
        """기술적 분석 기반 추천 등급 문자열을 생성합니다."""
        try:
            analysis = predictor.predict_buy_sell_prices(full_data)
            if "error" not in analysis:
                rec = analysis["recommendation"]
                conf = analysis["confidence"]
                if "STRONG BUY" in rec:
                    return f" 🟢🔥 <b>{rec}</b> ({conf}%)"
                elif "BUY" in rec:
                    return f" 🟢 <b>{rec}</b> ({conf}%)"
                elif "STRONG SELL" in rec:
                    return f" 🔴🔥 <b>{rec}</b> ({conf}%)"
                elif "SELL" in rec:
                    return f" 🔴 <b>{rec}</b> ({conf}%)"
                else:
                    return f" 🟡 <b>{rec}</b> ({conf}%)"
        except Exception:
            pass
        return ""

    def _handle_predict(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/predict [티커]</code> 형태로 입력해 주세요.\n예시: <code>/predict TSLA</code>",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
            
        ticker = arg.upper().strip()
        # 성공/실패 여부와 관계없이 분석 중 메시지를 정리합니다.
        with self._loading_message(chat_id, f"📊 <code>{html.escape(ticker)}</code> 기술적 지표를 분석하여 매매 가격을 예측하고 있습니다...",
                        reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id):

            stock_data = stock_api.fetch_stock_data(ticker)
            if not stock_data:
                self.send_message(chat_id, f"❌ <code>{html.escape(ticker)}</code> 데이터를 가져오지 못했습니다. 티커명을 확인하세요.",
                                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return

            analysis = predictor.predict_buy_sell_prices(stock_data)
            if "error" in analysis:
                self.send_message(chat_id, f"⚠️ 분석 실패: {html.escape(analysis['error'])}",
                                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return

            # Format prediction response
            currency = html.escape(str(analysis["currency"]))
            rec = analysis["recommendation"]
            ticker_safe = html.escape(str(analysis["ticker"]))
            stock_name = html.escape(str(stock_data.get("name", analysis["ticker"])))

            # Color rating decoration
            emoji = "⚪"
            if "STRONG BUY" in rec:
                emoji = "🟢🔥"
            elif "BUY" in rec:
                emoji = "🟢"
            elif "STRONG SELL" in rec:
                emoji = "🔴🔥"
            elif "SELL" in rec:
                emoji = "🔴"
            else:
                emoji = "🟡"

            # 판단 근거 설명 추가
            signals = analysis.get("signals", [])
            score = analysis.get("score", 0)

            # 점수 해석
            score_interpretation = ""
            if score >= 4.0:
                score_interpretation = "매우 강한 매수 신호 (점수: +{:.1f})".format(score)
            elif score >= 2.0:
                score_interpretation = "강한 매수 신호 (점수: +{:.1f})".format(score)
            elif score >= 0.5:
                score_interpretation = "약한 매수 신호 (점수: +{:.1f})".format(score)
            elif score <= -4.0:
                score_interpretation = "매우 강한 매도 신호 (점수: {:.1f})".format(score)
            elif score <= -2.0:
                score_interpretation = "강한 매도 신호 (점수: {:.1f})".format(score)
            elif score <= -0.5:
                score_interpretation = "약한 매도 신호 (점수: {:.1f})".format(score)
            else:
                score_interpretation = "중립 (점수: {:.1f})".format(score)

            # 현재가 대비 매수/매도 목표가 차이 계산
            current_price = analysis['current_price']
            buy_target = analysis['buy_target']
            sell_target = analysis['sell_target']
            buy_discount = ((current_price - buy_target) / current_price * 100) if current_price > 0 else 0
            sell_premium = ((sell_target - current_price) / current_price * 100) if current_price > 0 else 0

            report_text = (
                f"<b>📊 [{stock_name}] ({ticker_safe}) 기술적 분석 & 예측 리포트</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💵 현재가: <b>{current_price:.2f} {currency}</b>\n"
                f"📢 추천 등급: <b>{emoji} {html.escape(rec)}</b>\n"
                f"🎯 예측 신뢰도: <b>{analysis['confidence']}%</b>\n"
                f"📊 종합 점수: <b>{html.escape(score_interpretation)}</b>\n\n"
                f"🎯 <b>최적의 매수 목표가:</b>\n"
                f"👉 <code>{buy_target:.2f} {currency}</code> 이하 추천\n"
                f"<i>(현재가 대비 {buy_discount:.1f}% 하락 시 매수 기회)</i>\n"
                f"<i>산출 기준: 볼린저 하단(60%) + 지지선(40%)</i>\n\n"
                f"🎯 <b>최적의 매도 목표가:</b>\n"
                f"👉 <code>{sell_target:.2f} {currency}</code> 이상 추천\n"
                f"<i>(현재가 대비 {sell_premium:.1f}% 상승 시 매도 기회)</i>\n"
                f"<i>산출 기준: 볼린저 상단(60%) + 저항선(40%)</i>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"<b>🔍 판단 근거 (6개 지표 분석)</b>\n"
            )

            # 신호 설명 추가 (최대 5개)
            if signals:
                for i, signal in enumerate(signals[:5], 1):
                    report_text += f"{i}. {html.escape(signal)}\n"
            else:
                report_text += "분석된 신호가 없습니다.\n"

            self.send_message(chat_id, report_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_predict_short(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        """
        5분봉 기반 단기 예측 핸들러.
        /predict_short [ticker] 또는 /ps [ticker] 또는 /단기예측 [ticker]
        """
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/predict_short [티커]</code>\n예시: <code>/ps AAPL</code> (5분봉 단기 예측)",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
            
        ticker = arg.upper().strip()
        with self._loading_message(chat_id, f"📊 <code>{html.escape(ticker)}</code> 5분봉 기준 단기 기술적 분석을 수행하고 있습니다...",
                        reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id):

            # 5분봉 데이터 가져오기
            stock_data = stock_api.fetch_stock_data_intraday(ticker, interval="5m", range_str="5d")
            if not stock_data:
                # fallback: 15분봉 시도
                stock_data = stock_api.fetch_stock_data_intraday(ticker, interval="15m", range_str="5d")

            if not stock_data:
                self.send_message(chat_id, f"❌ <code>{html.escape(ticker)}</code> 단기 차트 데이터를 가져오지 못했습니다. 티커명을 확인하세요.",
                                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return

            analysis = predictor.predict_buy_sell_prices(stock_data)
            if "error" in analysis:
                self.send_message(chat_id, f"⚠️ 단기 분석 실패: {html.escape(analysis['error'])}",
                                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return

            # 리포트 포맷팅 (공통 메서드 활용)
            report_text = self._format_prediction_report(analysis, uses_short_term=True)
            self.send_message(chat_id, report_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_predict_weekly(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        """
        주봉 기반 장기 예측 핸들러.
        /predict_weekly [ticker] 또는 /pw [ticker] 또는 /장기예측 [ticker]
        """
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/predict_weekly [티커]</code>\n예시: <code>/pw AAPL</code> (주봉 장기 예측)",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
            
        ticker = arg.upper().strip()
        with self._loading_message(chat_id, f"📊 <code>{html.escape(ticker)}</code> 주봉 기준 장기 기술적 분석을 수행하고 있습니다...",
                        reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id):

            # 주봉 데이터 가져오기
            stock_data = stock_api.fetch_stock_data_weekly(ticker)
            if not stock_data:
                self.send_message(chat_id, f"❌ <code>{html.escape(ticker)}</code> 주봉 데이터를 가져오지 못했습니다. 티커명을 확인하세요.",
                                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return

            analysis = predictor.predict_buy_sell_prices(stock_data)
            if "error" in analysis:
                self.send_message(chat_id, f"⚠️ 장기 분석 실패: {html.escape(analysis['error'])}",
                                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return

            # 리포트 포맷팅 (공통 메서드 활용)
            report_text = self._format_prediction_report(analysis, uses_short_term=False)
            self.send_message(chat_id, report_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _format_prediction_report(self, analysis, uses_short_term=False):
        """
        분석 결과를 HTML 포맷의 리포트 문자열로 변환합니다.
        uses_short_term: True면 단기(분봉), False면 장기(주봉) 헤더 표시
        """
        currency = html.escape(str(analysis["currency"]))
        rec = analysis["recommendation"]
        ticker_safe = html.escape(str(analysis["ticker"]))
        
        # 캔들 정보 (timeframe)
        candle_name = analysis.get("candle_name", "")
        if not candle_name:
            candle_name = "5분봉" if uses_short_term else "주봉"
        elif uses_short_term and candle_name not in ("5분봉", "15분봉", "30분봉", "60분봉", "1시간봉"):
            candle_name = "5분봉"
        elif not uses_short_term and candle_name != "주봉":
            candle_name = "주봉"
        
        # 분석 기준 시간 표시
        timeframe_label = "단기(분봉)" if uses_short_term else "장기(주봉)"
        
        # Color rating decoration
        emoji = "⚪"
        if "STRONG BUY" in rec:
            emoji = "🟢🔥"
        elif "BUY" in rec:
            emoji = "🟢"
        elif "STRONG SELL" in rec:
            emoji = "🔴🔥"
        elif "SELL" in rec:
            emoji = "🔴"
        else:
            emoji = "🟡"
            
        indicators = analysis["indicators"]
        signals = analysis.get("signals", [])
        score = analysis.get("score", 0)
        
        # 점수 해석
        score_interpretation = ""
        if score >= 4.0:
            score_interpretation = "매우 강한 매수 신호 (점수: +{:.1f})".format(score)
        elif score >= 2.0:
            score_interpretation = "강한 매수 신호 (점수: +{:.1f})".format(score)
        elif score >= 0.5:
            score_interpretation = "약한 매수 신호 (점수: +{:.1f})".format(score)
        elif score <= -4.0:
            score_interpretation = "매우 강한 매도 신호 (점수: {:.1f})".format(score)
        elif score <= -2.0:
            score_interpretation = "강한 매도 신호 (점수: {:.1f})".format(score)
        elif score <= -0.5:
            score_interpretation = "약한 매도 신호 (점수: {:.1f})".format(score)
        else:
            score_interpretation = "중립 (점수: {:.1f})".format(score)
        
        current_price = analysis['current_price']
        buy_target = analysis['buy_target']
        sell_target = analysis['sell_target']
        buy_discount = ((current_price - buy_target) / current_price * 100) if current_price > 0 else 0
        sell_premium = ((sell_target - current_price) / current_price * 100) if current_price > 0 else 0
        
        # 봉 종류에 따른 설명
        candle_desc = candle_name
        if uses_short_term:
            period_desc = "단기(수시간~1일)"
            sma_desc = "20봉/50봉선"
            bb_desc = "볼린저밴드(20봉)"
            rsi_desc = "RSI(14봉)"
            sr_desc = "지지/저항선(20봉)"
        else:
            period_desc = "장기(수주~3개월)"
            sma_desc = "20주/50주선"
            bb_desc = "볼린저밴드(20주)"
            rsi_desc = "RSI(14주)"
            sr_desc = "지지/저항선(20주)"
        
        report_text = (
            f"<b>📊 [{ticker_safe}] {candle_desc} 기술적 분석 리포트</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🕯 <b>분석 기준:</b> {candle_desc} ({period_desc})\n"
            f"💵 현재가: <b>{current_price:.2f} {currency}</b>\n"
            f"📢 추천 등급: <b>{emoji} {html.escape(rec)}</b>\n"
            f"🎯 예측 신뢰도: <b>{analysis['confidence']}%</b>\n"
            f"📊 종합 점수: <b>{html.escape(score_interpretation)}</b>\n\n"
            f"🎯 <b>최적의 매수 목표가 ({candle_desc}):</b>\n"
            f"👉 <code>{buy_target:.2f} {currency}</code> 이하 추천\n"
            f"<i>(현재가 대비 {buy_discount:.1f}% 하락 시 매수 기회)</i>\n"
            f"<i>산출 기준: {candle_desc} 볼린저 하단(60%) + 지지선(40%)</i>\n\n"
            f"🎯 <b>최적의 매도 목표가 ({candle_desc}):</b>\n"
            f"👉 <code>{sell_target:.2f} {currency}</code> 이상 추천\n"
            f"<i>(현재가 대비 {sell_premium:.1f}% 상승 시 매도 기회)</i>\n"
            f"<i>산출 기준: {candle_desc} 볼린저 상단(60%) + 저항선(40%)</i>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>🔍 판단 근거 (6개 지표, {candle_desc} 기준)</b>\n"
        )
        
        # 신호 설명 추가 (최대 5개)
        if signals:
            for i, signal in enumerate(signals[:5], 1):
                report_text += f"{i}. {html.escape(signal)}\n"
        else:
            report_text += "분석된 신호가 없습니다.\n"
        
        # RSI 상태 설명
        rsi_val = indicators['rsi']
        rsi_status = ""
        if rsi_val >= 70:
            rsi_status = "과매수 ⚠️ (하락 가능성)"
        elif rsi_val >= 60:
            rsi_status = "고평가 구간 (주의)"
        elif rsi_val >= 45:
            rsi_status = "중립 (안정적)"
        elif rsi_val >= 30:
            rsi_status = "저평가 구간 (관심)"
        else:
            rsi_status = "과매도 ⚡ (반등 가능성)"
        
        # MACD 상태 설명
        macd_val = indicators['macd']
        macd_hist = indicators['macd_histogram']
        macd_status = ""
        if macd_hist > 0:
            macd_status = "상승 추세 (매수 우위)"
        elif macd_hist < 0:
            macd_status = "하락 추세 (매도 우위)"
        else:
            macd_status = "중립"
        
        # SMA 상태 설명
        sma20 = indicators['sma_20']
        sma50 = indicators['sma_50']
        sma_status = ""
        if sma20 > sma50:
            sma_status = "골든크로스 (상승 추세)"
        else:
            sma_status = "데드크로스 (하락 추세)"
        
        # 볼린저 밴드 위치 설명
        bb_lower = indicators['bb_lower']
        bb_upper = indicators['bb_upper']
        bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) * 100 if (bb_upper != bb_lower) else 50
        bb_status = ""
        if bb_position <= 20:
            bb_status = "하단 부근 (매수 신호)"
        elif bb_position >= 80:
            bb_status = "상단 부근 (매도 신호)"
        else:
            bb_status = "중앙 (안정적)"
        
        # 시장 국면 표시
        market_regime = analysis.get("market_regime", "RANGING")

        regime_label = {
            "TRENDING_UP": "상승 추세장 📈",
            "TRENDING_DOWN": "하락 추세장 📉",
            "RANGING": "횡보장 ↔️"
        }.get(market_regime, "횡보장 ↔️")

        # ATR/손절가 표시
        atr_val = indicators.get('atr', 0)
        atr_pct = indicators.get('atr_pct', 0)
        stop_loss = analysis.get('stop_loss', 0)

        # 지표 요약 추가
        report_text += (
            f"\n<b>📈 주요 보조지표 ({candle_desc} 기준)</b>\n"
            f"• <b>시장 국면:</b> {regime_label}\n"
            f"• <b>{rsi_desc}:</b> {rsi_val:.1f} → {rsi_status}\n"
            f"• <b>MACD:</b> {macd_val:.4f} / Histogram: {macd_hist:.4f} → {macd_status}\n"
            f"• <b>{bb_desc}:</b> {bb_lower:.2f} ~ {bb_upper:.2f} {currency}\n"
            f"  현재 위치: 밴드 {bb_position:.0f}% → {bb_status}\n"
            f"• <b>{sma_desc}:</b> {sma20:.2f} vs {sma50:.2f} → {sma_status}\n"
        )

        report_text += (
            f"• <b>{sr_desc}:</b> {indicators['support']:.2f} / {indicators['resistance']:.2f} {currency}\n"
            f"• <b>거래량 동향:</b> {indicators['volume_ratio']:.2f}x\n"
            f"• <b>ATR (변동성):</b> {atr_val:.2f} ({atr_pct:.1f}%)\n"
        )

        # 손절가 표시
        if stop_loss > 0:
            report_text += (
                f"🛑 <b>손절가:</b> <code>{stop_loss:.2f} {currency}</code> (ATR 1.5배 기준)\n"
            )

        return report_text

    # ================================================================
    # 알람 수신 수준 설정 (/alarms, /알람)
    # ================================================================

    def _build_alarm_settings_text(self):
        """알람 수신 수준 설정 화면의 안내 텍스트를 생성합니다."""
        return (
            "<b>🔔 알람 수신 수준 설정</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "아래 버튼을 눌러 받고 싶은 알람의 수준을 선택하세요.\n\n"
            "🔕 <b>모든 알람 받지 않음</b>\n"
            "<i>자동 알람을 완전히 끕니다.</i>\n\n"
            "🌍 <b>시장 알림만</b>\n"
            "<i>장 마감 요약(미국/한국), 주간 리포트·주식 일정, 극단적 시장 조건,\n"
            "지수 최고치 돌파 등 종목과 무관한 시장 메시지만 받습니다.</i>\n\n"
            "⭐ <b>중요 알림 + 시장 알림</b>\n"
            "<i>시장 알림에 더해 개별 종목의 정말 중요한 알림만 받습니다.\n"
            "(STRONG BUY/SELL 권장, 전일 종가 대비 급등락, 역대/52주 최고가 돌파)\n"
            "→ 20일선 돌파 등 잦은 기술적 신호 알림은 받지 않습니다.</i>\n\n"
            "🔔 <b>모든 알람 받기</b>\n"
            "<i>기술적 지표 신호를 포함한 모든 자동 알람을 받습니다.</i>\n"
        )

    def _build_alarm_keyboard(self, chat_id, current_level=None):
        """알람 수신 수준 인라인 키보드를 생성합니다. 현재 설정에는 ✅를 표시합니다."""
        if current_level is None:
            current_level = database.get_chat_alert_level(chat_id)
        rows = []
        for level in database.ALERT_LEVELS:
            label = database.ALERT_LEVEL_LABELS.get(level, level)
            if level == current_level:
                label = f"✅ {label}"
            rows.append([{"text": label, "callback_data": f"alarm:{level}"}])
        return {"inline_keyboard": rows}

    def _handle_alarm_settings(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        """
        알람 수신 수준 설정 명령어.
        사용법: /alarms 또는 /알람 → 인라인 버튼으로 수신 수준 선택
        """
        current_level = database.get_chat_alert_level(chat_id)
        text = self._build_alarm_settings_text()
        keyboard = self._build_alarm_keyboard(chat_id, current_level)
        current_label = database.ALERT_LEVEL_LABELS.get(current_level, current_level)
        text += f"━━━━━━━━━━━━━━━━━━━\n<b>현재 설정:</b> {current_label}"
        self.send_message(
            chat_id,
            text,
            reply_to_message_id=reply_to_message_id,
            message_thread_id=message_thread_id,
            reply_markup=keyboard
        )

    def _handle_callback_query(self, callback_query):
        """
        인라인 키보드 버튼 클릭(callback query)을 처리합니다.
        - data 형식: "alarm:<LEVEL>" (LEVEL ∈ OFF/MARKET/IMPORTANT/ALL)
        """
        callback_query_id = callback_query.get("id")
        data = callback_query.get("data", "")
        message = callback_query.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")

        try:
            if not data.startswith("alarm:") or not chat_id:
                return

            level = data.split(":", 1)[1].strip().upper()
            if level not in database.ALERT_LEVELS:
                self.answer_callback_query(callback_query_id, text="⚠️ 알 수 없는 설정입니다.")
                return

            database.set_chat_alert_level(chat_id, level)
            level_label = database.ALERT_LEVEL_LABELS.get(level, level)

            # 버튼 클릭 피드백 (상단 알림)
            feedback = {
                "OFF": "🔕 모든 자동 알람이 꺼졌습니다.",
                "MARKET": "🌍 시장 알림만 받도록 설정했습니다.",
                "IMPORTANT": "⭐ 중요 알림 + 시장 알림을 받도록 설정했습니다.",
                "ALL": "🔔 모든 자동 알람을 받도록 설정했습니다.",
            }.get(level, f"✅ {level_label} (으)로 설정했습니다.")
            self.answer_callback_query(callback_query_id, text=feedback)

            # 설정 메시지를 현재 상태가 반영된 내용으로 갱신
            if message_id:
                new_text = self._build_alarm_settings_text()
                new_text += f"━━━━━━━━━━━━━━━━━━━\n<b>현재 설정:</b> {level_label}"
                self.edit_message_text(
                    chat_id,
                    message_id,
                    new_text,
                    reply_markup=self._build_alarm_keyboard(chat_id, level)
                )
        except Exception as e:
            print(f"Error handling callback query: {e}")
            if callback_query_id:
                try:
                    self.answer_callback_query(callback_query_id, text="⚠️ 설정 변경에 실패했습니다.")
                except Exception:
                    pass

    def _handle_alerts(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        """
        자동 알림 켜기/끄기 명령어.
        사용법: /alerts on|off
        """
        arg_lower = (arg or "").strip().lower()
        if arg_lower in ("", "status", "상태", "현재"):
            enabled = database.get_chat_alerts_enabled(chat_id)
            state_text = "켜짐 ✅" if enabled else "꺼짐 🔕"
            level = database.get_chat_alert_level(chat_id)
            level_label = database.ALERT_LEVEL_LABELS.get(level, level)
            self.send_message(
                chat_id,
                f"🔔 현재 자동 알림 상태: <b>{state_text}</b>\n"
                f"<b>수신 수준:</b> {level_label}\n"
                "사용법: <code>/alerts on</code> 또는 <code>/alerts off</code>",
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id
            )
            return

        if arg_lower in ("off", "끄기", "disable", "false", "0"):
            database.set_chat_alerts_enabled(chat_id, False)
            self.send_message(
                chat_id,
                "🔕 자동 알림이 꺼졌습니다.\n"
                "다시 켜려면 <code>/alerts on</code>을 입력하세요.",
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id
            )
            return

        if arg_lower in ("on", "켜기", "enable", "true", "1"):
            database.set_chat_alerts_enabled(chat_id, True)
            level = database.get_chat_alert_level(chat_id)
            level_label = database.ALERT_LEVEL_LABELS.get(level, level)
            self.send_message(
                chat_id,
                "🔔 자동 알림이 켜졌습니다.\n"
                f"<b>현재 수신 수준:</b> {level_label}",
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id
            )
            return

        self.send_message(
            chat_id,
            "⚠️ 올바른 형식이 아닙니다.\n"
            "사용법: <code>/alerts on</code> 또는 <code>/alerts off</code>",
            reply_to_message_id=reply_to_message_id,
            message_thread_id=message_thread_id
        )

    def _handle_indices(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        """
        시장 인덱스 현황을 조회합니다.
        - 공포탐욕지수 (Fear & Greed Index)
        - VIX (변동성 지수)
        - S&P 500, NASDAQ, DOW 지수
        - USD/KRW 환율
        - 미국 10년물 국채 수익률
        """
        # "가져오는 중..." 메시지를 보내고 message_id를 저장
        loading_msg_id = self.send_message(
            chat_id, 
            "📊 시장 인덱스 데이터를 가져오는 중...\n"
            "<i>(공포탐욕지수, VIX, 주요 지수, 환율, 국채수익률)</i>",
            reply_to_message_id=reply_to_message_id, 
            message_thread_id=message_thread_id
        )
        
        try:
            # 모든 인덱스 데이터 가져오기
            data = market_indices.fetch_all_indices()
            
            # 리포트 생성
            report_text = market_indices.format_indices_report(data)
            
            # 극단 조건이 있으면 경고 추가
            extreme_alerts = market_indices.check_extreme_conditions(data)
            if extreme_alerts:
                report_text += "\n\n<b>⚠️ 극단 조건 경고</b>\n"
                for alert_type, message in extreme_alerts:
                    report_text += f"• {message}\n"
            
            # 결과 전송
            self.send_message(
                chat_id, 
                report_text, 
                reply_to_message_id=reply_to_message_id, 
                message_thread_id=message_thread_id
            )
            
        except Exception as e:
            self.send_message(
                chat_id, 
                f"⚠️ 시장 인덱스 데이터 조회 실패: {str(e)}",
                reply_to_message_id=reply_to_message_id, 
                message_thread_id=message_thread_id
            )
        
        # 로딩 메시지 삭제
        if loading_msg_id:
            try:
                self.delete_message(chat_id, loading_msg_id)
            except Exception:
                pass

    def _handle_korea(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        """
        한국 시장 (KOSPI/KOSDAQ) 현황 및 환율을 조회합니다.
        """
        # "가져오는 중..." 메시지를 보내고 message_id를 저장
        loading_msg_id = self.send_message(
            chat_id, 
            "🇰🇷 한국 시장 데이터를 가져오는 중...\n"
            "<i>(KOSPI, KOSDAQ, 원/달러 환율)</i>",
            reply_to_message_id=reply_to_message_id, 
            message_thread_id=message_thread_id
        )
        
        try:
            # 한국 시장 데이터 가져오기
            data = market_indices.fetch_korea_market_close_data()
            
            # 리포트 생성
            report_text = market_indices.format_korea_market_close_report(data)
            
            # 결과 전송
            self.send_message(
                chat_id, 
                report_text, 
                reply_to_message_id=reply_to_message_id, 
                message_thread_id=message_thread_id
            )
            
        except Exception as e:
            self.send_message(
                chat_id, 
                f"⚠️ 한국 시장 데이터 조회 실패: {str(e)}",
                reply_to_message_id=reply_to_message_id, 
                message_thread_id=message_thread_id
            )
        
        # 로딩 메시지 삭제
        if loading_msg_id:
            try:
                self.delete_message(chat_id, loading_msg_id)
            except Exception:
                pass

    def _handle_calendar(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        try:
            report = market_events.format_weekly_events(market_events.fetch_weekly_events())
        except Exception as exc:
            print(f"Error collecting market events: {exc}")
            report = "⚠️ 주식 일정을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."
        self.send_message(chat_id, report, reply_to_message_id=reply_to_message_id,
                          message_thread_id=message_thread_id)

    def _handle_weekly(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        """
        지난주 주요 지수 및 관심 종목의 주간 변화 요약 리포트를 제공합니다.
        /weekly 또는 /주간 또는 /w
        """
        # "가져오는 중..." 메시지를 보내고 message_id를 저장
        loading_msg_id = self.send_message(
            chat_id,
            "📊 주간 시장 요약 데이터를 수집하는 중...\n"
            "<i>(주요 지수, 관심 종목 주간 변동)</i>",
            reply_to_message_id=reply_to_message_id,
            message_thread_id=message_thread_id
        )

        try:
            # 주간 리포트 데이터 수집
            data = weekly_report.fetch_weekly_report_data()

            # 리포트 생성
            report_text = weekly_report.format_weekly_report(data)

            # 결과 전송
            self.send_message(
                chat_id,
                report_text,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id
            )

        except Exception as e:
            self.send_message(
                chat_id,
                f"⚠️ 주간 리포트 생성 실패: {str(e)}",
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id
            )

        # 로딩 메시지 삭제
        if loading_msg_id:
            try:
                self.delete_message(chat_id, loading_msg_id)
            except Exception:
                pass

    def _handle_settopic(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        """
        단체방에서 알림을 받을 토픽(스레드)을 설정합니다.
        사용법:
          /settopic              - 현재 토픽을 알림 토픽으로 설정
          /settopic [thread_id]  - 특정 thread_id를 알림 토픽으로 설정
          /settopic off          - 토픽 설정 해제 (기본 토픽으로 알림)
        """
        if not arg:
            # 인자가 없으면 현재 메시지가 속한 thread_id를 알림 토픽으로 설정
            if message_thread_id is not None:
                database.set_chat_topic(chat_id, message_thread_id)
                self.send_message(
                    chat_id,
                    f"✅ 알림 토픽이 현재 토픽(<code>{message_thread_id}</code>)으로 설정되었습니다.\n"
                    f"이제부터 모든 자동 알림이 이 토픽으로 전송됩니다.",
                    reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id
                )
            else:
                # 일반 채팅방이거나 토픽이 없는 그룹
                self.send_message(
                    chat_id,
                    "⚠️ 현재 메시지가 토픽(스레드)에 속해 있지 않습니다.\n"
                    "토픽이 활성화된 그룹의 특정 토픽에서 이 명령어를 사용하거나,\n"
                    "<code>/settopic [thread_id]</code> 형식으로 직접 thread_id를 지정해 주세요.\n\n"
                    "💡 thread_id는 토픽 링크에서 확인할 수 있습니다.\n"
                    "예: <code>/settopic 12345</code>",
                    reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id
                )
            return

        arg_lower = arg.lower().strip()
        if arg_lower in ("off", "해제", "끄기", "none", "기본"):
            # 토픽 설정 해제
            database.clear_chat_topic(chat_id)
            self.send_message(
                chat_id,
                "✅ 알림 토픽 설정이 해제되었습니다.\n"
                "이제부터 모든 자동 알림이 기본 토픽(General)으로 전송됩니다.",
                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id
            )
            return

        # 숫자로 된 thread_id 직접 지정
        try:
            thread_id = int(arg)
            database.set_chat_topic(chat_id, thread_id)
            self.send_message(
                chat_id,
                f"✅ 알림 토픽이 <code>{thread_id}</code>(으)로 설정되었습니다.\n"
                f"이제부터 모든 자동 알림이 이 토픽으로 전송됩니다.",
                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id
            )
        except ValueError:
            self.send_message(
                chat_id,
                "⚠️ 올바르지 않은 형식입니다.\n"
                "사용법:\n"
                "• <code>/settopic</code> - 현재 토픽을 알림 토픽으로 설정\n"
                "• <code>/settopic [thread_id]</code> - 특정 thread_id 지정\n"
                "• <code>/settopic off</code> - 토픽 설정 해제",
                reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id
            )

if __name__ == "__main__":
    # Local dry run
    bot = TelegramBot()
    # It won't actually poll if TOKEN is empty, but will print info.
    bot.start_polling()
    time.sleep(2)
    bot.stop_polling()
