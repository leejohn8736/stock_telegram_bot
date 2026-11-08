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
        """Make HTTP request to Telegram Bot API with strict UTF-8 byte encoding preventing ASCII codec crashes."""
        if not self.token:
            print("Telegram Bot Token is not configured. API call skipped.")
            return None
            
        url = f"{self.base_url}/{method}"
        headers = {"Content-Type": "application/json; charset=utf-8"}
        
        try:
            # 아스키 인코딩 에러(ascii codec can't encode)를 원천 차단하기 위해 utf-8 바이트로 직접 변환
            if data is not None:
                json_str = json.dumps(data, ensure_ascii=False)
                req_data = json_str.encode("utf-8")
            else:
                req_data = json.dumps({}, ensure_ascii=False).encode("utf-8")
                
            req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            
            with urllib.request.urlopen(req, context=self.ssl_context, timeout=25) as response:
                if response.status == 200:
                    response_bytes = response.read()
                    return json.loads(response_bytes.decode("utf-8"))
                else:
                    print(f"Telegram API Error on {method}: HTTP Error {response.status}")
        except urllib.error.HTTPError as e:
            err_body = e.file.read().decode('utf-8', errors='ignore') if e.file else ""
            print(f"Telegram API Error on {method}: HTTP Error {e.code}: {e.reason} - {err_body}")
        except Exception as e:
            print(f"Telegram API Error on {method}: {e}")
        return None

    def send_message(self, chat_id, text, parse_mode="HTML", reply_to_message_id=None, message_thread_id=None, reply_markup=None):
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
        payload = {
            "chat_id": chat_id,
            "message_id": message_id
        }
        return self._api_call("deleteMessage", payload)

    @contextmanager
    def _loading_message(self, chat_id, text, **reply_options):
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
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        return self._api_call("answerCallbackQuery", payload)

    def edit_message_text(self, chat_id, message_id, text, parse_mode="HTML", reply_markup=None):
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
        payload = {"timeout": 20}
        if self.offset is not None:
            payload["offset"] = self.offset
            
        updates = self._api_call("getUpdates", payload)
        if updates and updates.get("ok"):
            return updates.get("result", [])
        return None

    def set_my_commands(self):
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
        self.is_running = False
        self._stop_event.set()
        print("Stopping Telegram Bot Polling...")

    def _poll_loop(self):
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
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()
        message_id = message.get("message_id")
        message_thread_id = message.get("message_thread_id")
        
        if not chat_id or not text:
            return
            
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            command = parts[0].lower()
            if "@" in command:
                command = command.split("@", 1)[0]
            arg = parts[1].strip() if len(parts) > 1 else ""
            
            self._dispatch_command(chat_id, command, arg, reply_to_message_id=message_id, message_thread_id=message_thread_id)

    def _dispatch_command(self, chat_id, command, arg, reply_to_message_id=None, message_thread_id=None):
        command_map = {
            "/start": "/start", "/시작": "/start",
            "/help": "/help", "/도움말": "/help",
            "/add": "/add", "/추가": "/add", "/a": "/add",
            "/del": "/del", "/d": "/del", "/삭제": "/del",
            "/list": "/list", "/목록": "/list", "/l": "/list",
            "/predict": "/predict", "/예측": "/predict", "/p": "/predict",
            "/predict_short": "/predict_short", "/단기예측": "/predict_short", "/ps": "/predict_short",
            "/predict_weekly": "/predict_weekly", "/장기예측": "/predict_weekly", "/pw": "/predict_weekly",
            "/settopic": "/settopic", "/토픽설정": "/settopic", "/topic": "/settopic",
            "/indices": "/indices", "/지수": "/indices", "/시장": "/indices", "/i": "/indices",
            "/korea": "/korea", "/한국": "/korea", "/kr": "/korea",
            "/alerts": "/alerts", "/알림": "/alerts", "/alert": "/alerts",
            "/alarms": "/alarms", "/알람": "/alarms", "/알람설정": "/alarms",
            "/weekly": "/weekly", "/주간": "/weekly", "/주간리포트": "/weekly", "/w": "/weekly",
            "/calendar": "/calendar", "/일정": "/calendar"
        }
        
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
            "<b>ℹ️ 시작하려면 /help 를 입력하여 사용 가능한 명령어 목록을 확인하세요!</b>"
        )
        self.send_message(chat_id, welcome_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_help(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        help_text = (
            "<b>🛠️ 사용 가능한 명령어 목록</b>\n\n"
            "📌 <b>/add [티커]</b> 또는 <b>/추가 [티커]</b> - 관심 주식을 등록합니다.\n"
            "📌 <b>/del [티커]</b> 또는 <b>/삭제 [티커]</b> - 관심 주식을 삭제합니다.\n"
            "📌 <b>/list</b> 또는 <b>/목록</b> - 내가 구독 중인 주식 리스트와 현재가를 조회합니다.\n"
            "📌 <b>/predict [티커]</b> 또는 <b>/예측 [티커]</b> - 특정 주식의 기술적 지표 분석 및 매매 예측 가격 조회\n"
            "📌 <b>/predict_short [티커]</b> 또는 <b>/ps [티커]</b> - 5분봉 단기 예측\n"
            "📌 <b>/predict_weekly [티커]</b> 또는 <b>/pw [티커]</b> - 주봉 장기 예측\n"
            "📌 <b>/indices</b> 또는 <b>/지수</b> - 현재 시장 인덱스 및 공포탐욕지수 조회\n"
            "📌 <b>/korea</b> 또는 <b>/한국</b> - 한국 시장(KOSPI/KOSDAQ) 및 환율 조회\n"
            "📌 <b>/weekly</b> 또는 <b>/주간</b> - 주간 요약 리포트\n"
            "📌 <b>/calendar</b> 또는 <b>/일정</b> - 증시 주요 일정\n"
            "📌 <b>/alarms</b> 또는 <b>/알람</b> - 알람 수신 수준 선택\n"
            "📌 <b>/help</b> 또는 <b>/도움말</b> - 도움말 표시"
        )
        self.send_message(chat_id, help_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_add(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/add [티커]</code> 형태 입력 (예: <code>/add AAPL</code>)",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
            
        ticker = arg.upper().strip()
        self.send_message(chat_id, f"🔍 <code>{ticker}</code>의 유효성을 검증하는 중입니다...",
                        reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        
        stock_data = stock_api.fetch_stock_data(ticker)
        if not stock_data:
            self.send_message(chat_id, f"❌ <code>{ticker}</code>는 유효하지 않은 티커이거나 데이터를 가져올 수 없습니다.",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
            
        success = database.add_subscription(chat_id, ticker)
        stock_name = stock_data.get("name", ticker)
        if success:
            self.send_message(chat_id, f"✅ <b>{html.escape(stock_name)}</b> (<code>{ticker}</code>) 등록 완료!\n현재가: <b>{stock_data['current_price']:.2f} {stock_data['currency']}</b>",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        else:
            self.send_message(chat_id, f"ℹ️ <code>{ticker}</code>는 이미 등록된 관심 주식입니다.",
                            reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_del(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/del [티커]</code>", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
            
        ticker = arg.upper().strip()
        success = database.remove_subscription(chat_id, ticker)
        if success:
            database.clear_all_signals_for_ticker(chat_id, ticker)
            self.send_message(chat_id, f"✅ <code>{ticker}</code> 삭제 완료.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        else:
            self.send_message(chat_id, f"⚠️ 등록되지 않은 티커입니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_list(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        subscriptions = database.get_user_subscriptions(chat_id)
        if not subscriptions:
            self.send_message(chat_id, "📂 구독 중인 관심 주식이 없습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
            
        with self._loading_message(chat_id, "🔄 현재가 조회 중...", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id):
            kst_offset = 9 * 60 * 60
            now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + kst_offset))
            lines = [f"<b>📋 나의 관심 주식 리스트</b>\n⏱ 조회시간: <code>{now_str}</code>\n"]

            price_cache = stock_api.fetch_current_prices_batch(subscriptions) if stock_api.is_toss_enabled() else None
            max_workers = min(8, max(1, len(subscriptions)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                row_futures = [executor.submit(self._build_list_row, idx, ticker, price_cache) for idx, ticker in enumerate(subscriptions, 1)]
                for future in row_futures:
                    lines.append(future.result())

            self.send_message(chat_id, "\n".join(lines), reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _build_list_row(self, idx, ticker, price_cache=None):
        price_cache = price_cache or {}
        try:
            full_data = stock_api.fetch_stock_data(ticker, price_cache)
            if full_data:
                cached = price_cache.get(ticker) or {}
                price = cached.get("price") or full_data["current_price"]
                prev_close = cached.get("previous_close") or full_data.get("previous_close")
                currency = cached.get("currency") or full_data["currency"]
                stock_name = full_data.get("name") or ticker

                change_str = self._build_change_str(price, prev_close)
                rec_str = self._build_recommendation_str(full_data)

                return f"{idx}. <b>{html.escape(stock_name)}</b> (<code>{ticker}</code>): {price:.2f} {currency}{change_str}{rec_str}"
            return f"{idx}. <code>{ticker}</code>: 데이터 로드 실패 ⚠️"
        except Exception:
            return f"{idx}. <code>{ticker}</code>: 데이터 로드 실패 ⚠️"

    @staticmethod
    def _build_change_str(price, prev_close):
        if prev_close is not None and prev_close > 0:
            pct_change = ((price - prev_close) / prev_close) * 100
            if abs(pct_change) < 0.01: return " (보합)"
            elif pct_change > 0: return f" (📈 <b>+{pct_change:.2f}%</b>)"
            else: return f" (📉 <b>{pct_change:.2f}%</b>)"
        return ""

    @staticmethod
    def _build_recommendation_str(full_data):
        try:
            analysis = predictor.predict_buy_sell_prices(full_data)
            if "error" not in analysis:
                rec = analysis["recommendation"]
                conf = analysis["confidence"]
                if "STRONG BUY" in rec: return f" 🟢🔥 <b>{rec}</b> ({conf}%)"
                elif "BUY" in rec: return f" 🟢 <b>{rec}</b> ({conf}%)"
                elif "STRONG SELL" in rec: return f" 🔴🔥 <b>{rec}</b> ({conf}%)"
                elif "SELL" in rec: return f" 🔴 <b>{rec}</b> ({conf}%)"
                else: return f" 🟡 <b>{rec}</b> ({conf}%)"
        except Exception:
            pass
        return ""

    def _handle_predict(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/predict [티커]</code>", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
        ticker = arg.upper().strip()
        with self._loading_message(chat_id, f"📊 <code>{html.escape(ticker)}</code> 분석 중...", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id):
            stock_data = stock_api.fetch_stock_data(ticker)
            if not stock_data:
                self.send_message(chat_id, "❌ 데이터를 가져오지 못했습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return
            analysis = predictor.predict_buy_sell_prices(stock_data)
            if "error" in analysis:
                self.send_message(chat_id, f"⚠️ 분석 실패: {html.escape(analysis['error'])}", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return
            self.send_message(chat_id, self._format_prediction_report(analysis, uses_short_term=False), reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_predict_short(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/predict_short [티커]</code>", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
        ticker = arg.upper().strip()
        with self._loading_message(chat_id, f"📊 <code>{html.escape(ticker)}</code> 단기 분석 중...", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id):
            stock_data = stock_api.fetch_stock_data_intraday(ticker, interval="5m", range_str="5d") or stock_api.fetch_stock_data_intraday(ticker, interval="15m", range_str="5d")
            if not stock_data:
                self.send_message(chat_id, "❌ 단기 데이터를 가져오지 못했습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return
            analysis = predictor.predict_buy_sell_prices(stock_data)
            if "error" in analysis:
                self.send_message(chat_id, f"⚠️ 분석 실패: {html.escape(analysis['error'])}", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return
            self.send_message(chat_id, self._format_prediction_report(analysis, uses_short_term=True), reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_predict_weekly(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        if not arg:
            self.send_message(chat_id, "⚠️ 사용법: <code>/predict_weekly [티커]</code>", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return
        ticker = arg.upper().strip()
        with self._loading_message(chat_id, f"📊 <code>{html.escape(ticker)}</code> 장기 분석 중...", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id):
            stock_data = stock_api.fetch_stock_data_weekly(ticker)
            if not stock_data:
                self.send_message(chat_id, "❌ 주봉 데이터를 가져오지 못했습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return
            analysis = predictor.predict_buy_sell_prices(stock_data)
            if "error" in analysis:
                self.send_message(chat_id, f"⚠️ 분석 실패: {html.escape(analysis['error'])}", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
                return
            self.send_message(chat_id, self._format_prediction_report(analysis, uses_short_term=False), reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _format_prediction_report(self, analysis, uses_short_term=False):
        currency = html.escape(str(analysis["currency"]))
        rec = analysis["recommendation"]
        ticker_safe = html.escape(str(analysis["ticker"]))
        candle_name = analysis.get("candle_name", "5분봉" if uses_short_term else "주봉")
        
        emoji = "⚪"
        if "STRONG BUY" in rec: emoji = "🟢🔥"
        elif "BUY" in rec: emoji = "🟢"
        elif "STRONG SELL" in rec: emoji = "🔴🔥"
        elif "SELL" in rec: emoji = "🔴"
        else: emoji = "🟡"
            
        signals = analysis.get("signals", [])
        score = analysis.get("score", 0)
        score_interpretation = "중립 (점수: {:.1f})".format(score)
        if score >= 4.0: score_interpretation = "매우 강한 매수 신호 (점수: +{:.1f})".format(score)
        elif score >= 2.0: score_interpretation = "강한 매수 신호 (점수: +{:.1f})".format(score)
        elif score >= 0.5: score_interpretation = "약한 매수 신호 (점수: +{:.1f})".format(score)
        elif score <= -4.0: score_interpretation = "매우 강한 매도 신호 (점수: {:.1f})".format(score)
        elif score <= -2.0: score_interpretation = "강한 매도 신호 (점수: {:.1f})".format(score)
        elif score <= -0.5: score_interpretation = "약한 매도 신호 (점수: {:.1f})".format(score)
        
        current_price = analysis['current_price']
        buy_target = analysis['buy_target']
        sell_target = analysis['sell_target']
        
        report_text = (
            f"<b>📊 [{ticker_safe}] {candle_name} 기술적 분석 리포트</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💵 현재가: <b>{current_price:.2f} {currency}</b>\n"
            f"📢 추천 등급: <b>{emoji} {html.escape(rec)}</b>\n"
            f"🎯 예측 신뢰도: <b>{analysis['confidence']}%</b>\n"
            f"📊 종합 점수: <b>{html.escape(score_interpretation)}</b>\n\n"
            f"🎯 <b>최적의 매수 목표가:</b> <code>{buy_target:.2f} {currency}</code> 이하\n"
            f"🎯 <b>최적의 매도 목표가:</b> <code>{sell_target:.2f} {currency}</code> 이상\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>🔍 판단 근거</b>\n"
        )
        if signals:
            for i, signal in enumerate(signals[:5], 1):
                report_text += f"{i}. {html.escape(signal)}\n"
        else:
            report_text += "분석된 신호가 없습니다.\n"
        return report_text

    def _handle_indices(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        loading_id = self.send_message(chat_id, "📊 시장 인덱스 데이터 조회 중...", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        try:
            data = market_indices.fetch_all_indices()
            report_text = market_indices.format_indices_report(data)
            extreme = market_indices.check_extreme_conditions(data)
            if extreme:
                report_text += "\n\n<b>⚠️ 극단 조건 경고</b>\n"
                for _, msg in extreme: report_text += f"• {msg}\n"
            self.send_message(chat_id, report_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        except Exception as e:
            self.send_message(chat_id, f"⚠️ 조회 실패: {str(e)}", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        if loading_id:
            try: self.delete_message(chat_id, loading_id)
            except Exception: pass

    def _handle_korea(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        loading_id = self.send_message(chat_id, "🇰🇷 한국 시장 데이터 조회 중...", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        try:
            data = market_indices.fetch_korea_market_close_data()
            report_text = market_indices.format_korea_market_close_report(data)
            self.send_message(chat_id, report_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        except Exception as e:
            self.send_message(chat_id, f"⚠️ 조회 실패: {str(e)}", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        if loading_id:
            try: self.delete_message(chat_id, loading_id)
            except Exception: pass

    def _handle_calendar(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        try:
            report = market_events.format_weekly_events(market_events.fetch_weekly_events())
        except Exception:
            report = "⚠️ 주식 일정을 불러오지 못했습니다."
        self.send_message(chat_id, report, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _handle_weekly(self, chat_id, reply_to_message_id=None, message_thread_id=None):
        loading_id = self.send_message(chat_id, "📊 주간 리포트 데이터 수집 중...", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        try:
            data = weekly_report.fetch_weekly_report_data()
            report_text = weekly_report.format_weekly_report(data)
            self.send_message(chat_id, report_text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        except Exception as e:
            self.send_message(chat_id, f"⚠️ 주간 리포트 생성 실패: {str(e)}", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        if loading_id:
            try: self.delete_message(chat_id, loading_id)
            except Exception: pass

    def _handle_settopic(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        if not arg:
            if message_thread_id is not None:
                database.set_chat_topic(chat_id, message_thread_id)
                self.send_message(chat_id, f"✅ 알림 토픽이 현재 토픽(<code>{message_thread_id}</code>)으로 설정되었습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            else:
                self.send_message(chat_id, "⚠️ 현재 메시지가 토픽에 속해 있지 않습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return

        arg_lower = arg.lower().strip()
        if arg_lower in ("off", "해제", "끄기", "none", "기본"):
            database.clear_chat_topic(chat_id)
            self.send_message(chat_id, "✅ 알림 토픽 설정이 해제되었습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return

        try:
            thread_id = int(arg)
            database.set_chat_topic(chat_id, thread_id)
            self.send_message(chat_id, f"✅ 알림 토픽이 <code>{thread_id}</code>(으)로 설정되었습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
        except ValueError:
            self.send_message(chat_id, "⚠️ 올바르지 않은 형식입니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

    def _build_alarm_settings_text(self):
        return (
            "<b>🔔 알람 수신 수준 설정</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "아래 버튼을 눌러 받고 싶은 알람의 수준을 선택하세요.\n\n"
            "🔕 <b>모든 알람 받지 않음</b>\n"
            "🌍 <b>시장 알림만</b>\n"
            "⭐ <b>중요 알림 + 시장 알림</b>\n"
            "🔔 <b>모든 알람 받기</b>\n"
        )

    def _build_alarm_keyboard(self, chat_id, current_level=None):
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
        current_level = database.get_chat_alert_level(chat_id)
        text = self._build_alarm_settings_text()
        keyboard = self._build_alarm_keyboard(chat_id, current_level)
        current_label = database.ALERT_LEVEL_LABELS.get(current_level, current_level)
        text += f"━━━━━━━━━━━━━━━━━━━\n<b>현재 설정:</b> {current_label}"
        self.send_message(chat_id, text, reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id, reply_markup=keyboard)

    def _handle_callback_query(self, callback_query):
        query_id = callback_query.get("id")
        data = callback_query.get("data", "")
        message = callback_query.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")

        try:
            if not data.startswith("alarm:") or not chat_id:
                return
            level = data.split(":", 1)[1].strip().upper()
            if level not in database.ALERT_LEVELS:
                self.answer_callback_query(query_id, text="⚠️ 알 수 없는 설정입니다.")
                return

            database.set_chat_alert_level(chat_id, level)
            level_label = database.ALERT_LEVEL_LABELS.get(level, level)
            self.answer_callback_query(query_id, text=f"설정 변경됨: {level_label}")

            if message_id:
                new_text = self._build_alarm_settings_text() + f"━━━━━━━━━━━━━━━━━━━\n<b>현재 설정:</b> {level_label}"
                self.edit_message_text(chat_id, message_id, new_text, reply_markup=self._build_alarm_keyboard(chat_id, level))
        except Exception:
            if query_id:
                try: self.answer_callback_query(query_id, text="⚠️ 설정 변경 실패")
                except Exception: pass

    def _handle_alerts(self, chat_id, arg, reply_to_message_id=None, message_thread_id=None):
        arg_lower = (arg or "").strip().lower()
        if arg_lower in ("", "status", "상태", "현재"):
            enabled = database.get_chat_alerts_enabled(chat_id)
            state_text = "켜짐 ✅" if enabled else "꺼짐 🔕"
            level = database.get_chat_alert_level(chat_id)
            level_label = database.ALERT_LEVEL_LABELS.get(level, level)
            self.send_message(chat_id, f"🔔 알림 상태: <b>{state_text}</b>\n수신 수준: {level_label}", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return

        if arg_lower in ("off", "끄기", "disable", "false", "0"):
            database.set_chat_alerts_enabled(chat_id, False)
            self.send_message(chat_id, "🔕 자동 알림이 꺼졌습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return

        if arg_lower in ("on", "켜기", "enable", "true", "1"):
            database.set_chat_alerts_enabled(chat_id, True)
            self.send_message(chat_id, "🔔 자동 알림이 켜졌습니다.", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)
            return

        self.send_message(chat_id, "⚠️ 사용법: <code>/alerts on</code> 또는 <code>/alerts off</code>", reply_to_message_id=reply_to_message_id, message_thread_id=message_thread_id)

if __name__ == "__main__":
    bot = TelegramBot()
    bot.start_polling()
    time.sleep(2)
    bot.stop_polling()
