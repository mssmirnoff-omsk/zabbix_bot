#!/usr/bin/env python3
import json
import os
import sys
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SECRET = os.environ["ZABBIX_WEBHOOK_SECRET"] # Используем тот же секрет для проверки
PORT = int(os.environ.get("PORT", "3000"))

def esc(value) -> str:
    if value is None:
        return ""
    val_str = str(value).strip()
    if val_str.startswith("{") and val_str.endswith("}"):
        return ""
    return (
        val_str
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

def send_telegram(text: str, disable_sound: bool = False) -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    data = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": disable_sound,
    }).encode()
    
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()

class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str = "ok") -> None:
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        params = urllib.parse.parse_qs(parsed_url.query)

        if path == "/mikrotik":
            req_secret = params.get("secret", [""])[0]
            if req_secret != SECRET:
                self._send(403, "forbidden")
                return

            text_raw = params.get("text", [""])[0]
            if not text_raw:
                self._send(400, "missing text")
                return

            formatted_text = f"📟 <b>MikroTik Alert</b>\n{esc(text_raw)}"

            try:
                send_telegram(formatted_text, disable_sound=False)
            except Exception as exc:
                print(f"MIKROTIK TELEGRAM ERROR: {exc}", file=sys.stderr, flush=True)
                self._send(502, f"telegram error: {str(exc)}")
                return

            self._send(200, "ok")
            return

        if path == "/health":
            self._send(200, "ok")
        else:
            self._send(404, "not found")

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path != "/zabbix":
            self._send(404, "not found")
            return
        if self.headers.get("X-Webhook-Secret", "") != SECRET:
            self._send(403, "forbidden")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._send(400, "bad json")
            return

        severity = str(payload.get("severity", "Not classified")).strip()
        status = str(payload.get("status", "PROBLEM")).upper()
        is_problem = (status == "PROBLEM")

        # Перевод уровней важности на русский язык
        severity_map = {
            "Not classified": {"mark": "⚪", "silent": True,  "ru": "Не классифицировано"},
            "Information":    {"mark": "⚪", "silent": True,  "ru": "Информация"},
            "Warning":        {"mark": "🟡", "silent": True,  "ru": "Предупреждение"},
            "Average":        {"mark": "🟠", "silent": False, "ru": "Средняя"},
            "High":           {"mark": "🔴", "silent": False, "ru": "Высокая"},
            "Disaster":       {"mark": "🚨", "silent": False, "ru": "Чрезвычайная"},
        }

        # Дефолтное значение на случай, если прилетит неизвестный статус
        sev_settings = severity_map.get(severity, {"mark": "🔴", "silent": False, "ru": severity})

        if not is_problem:
            mark = "🟢"
            disable_sound = False
            header_text = f"<b>{esc(payload.get('status', ''))}</b> (Решено)"
        else:
            mark = sev_settings["mark"]
            disable_sound = sev_settings["silent"]
            
            # Получаем перевод на русский язык
            severity_ru = sev_settings["ru"]
            if severity == "Disaster":
                header_text = f"🔥 <b>{esc(payload.get('status', '')).upper()}: {esc(severity_ru).upper()}</b> 🔥"
            else:
                header_text = f"<b>{esc(payload.get('status', ''))}</b> [{esc(severity_ru)}]"
        
        lines = [
            f"{mark} {header_text}",
            f"<b>Триггер:</b> {esc(payload.get('name', ''))}",
            f"<b>Хост:</b> {esc(payload.get('host', ''))}",
        ]
        
        opdata = payload.get("opdata") or ""
        if opdata:
            lines.append(f"<b>Данные:</b> {esc(opdata)}")
            
        lines.append("")

        e_date = esc(payload.get("event_date", ""))
        e_time = esc(payload.get("event_time", ""))
        r_date = esc(payload.get("event_recovery_date", ""))
        r_time = esc(payload.get("event_recovery_time", ""))
        duration = esc(payload.get("event_duration", ""))
        
        if is_problem:
            if e_date and e_time:
                lines.append(f"🕒 <b>Старт:</b> {e_date} {e_time}")
        else:
            if e_date and e_time:
                lines.append(f"🕒 <b>Старт:</b> {e_date} {e_time}")
            if r_date and r_time:
                lines.append(f"✅ <b>Конец:</b> {r_date} {r_time}")
            if duration:
                lines.append(f"⏱ <b>Длительность:</b> {duration}")

        try:
            send_telegram("\n".join(lines), disable_sound=disable_sound)
        except Exception as exc:
            print(f"CRITICAL TELEGRAM ERROR: {exc}", file=sys.stderr, flush=True)
            self._send(502, f"telegram error: {str(exc)}")
            return
        self._send(200, "ok")

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()
