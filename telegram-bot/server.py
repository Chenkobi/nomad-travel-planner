#!/usr/bin/env python3
"""Small dependency-free TRIPY Telegram bridge for the working travel version."""
import json, os, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("TRIPY_DATA_FILE", ROOT / "trip-data.json"))
UPLOADS = Path(os.getenv("TRIPY_UPLOAD_DIR", ROOT / "trip-uploads"))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_CHAT = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "8787"))
OFFSET = 0
LOCK = threading.Lock()

ICONS = {"טיסה": "✈️", "רכבת": "🚆", "מלון": "🏨", "אטרקציה": "🎟️", "מסעדה": "🍽️"}

def load_events():
    if not DATA.exists(): return []
    try: return json.loads(DATA.read_text(encoding="utf-8"))
    except (ValueError, OSError): return []

def save_events(events):
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA)

def api(method, payload=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    body = urllib.parse.urlencode(payload or {}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=65) as response:
        result = json.loads(response.read().decode())
    if not result.get("ok"): raise RuntimeError(result.get("description", "Telegram API error"))
    return result["result"]

def send(chat_id, text):
    api("sendMessage", {"chat_id": chat_id, "text": text})

def add_event(title, details, kind, source="Telegram"):
    events = load_events()
    now = datetime.now().strftime("%H:%M")
    event = [now, ICONS.get(kind, "📌"), title, details, kind, source]
    events.insert(0, event)
    save_events(events)
    return event

def handle_message(message):
    chat = str(message.get("chat", {}).get("id", ""))
    if ALLOWED_CHAT and chat != ALLOWED_CHAT:
        send(chat, "הבוט מחובר כרגע לטיול הפרטי של קובי בלבד.")
        return
    text = (message.get("text") or "").strip()
    if text.startswith("/start"):
        send(chat, "TRIPY מחובר ✅\nשלח טיסה, רכבת, מלון, אטרקציה או אישור הזמנה.\nלדוגמה: מלון Ruby Mimi בציריך, צ׳ק-אין 12 ביוני")
        return
    if text.startswith("/today"):
        events = load_events()[:8]
        send(chat, "\n".join(f"{e[0]} {e[1]} {e[2]} — {e[3]}" for e in events) or "אין עדיין אירועים שמורים.")
        return
    if message.get("document") or message.get("photo"):
        item = message.get("document") or message.get("photo", [{}])[-1]
        name = item.get("file_name", "telegram-photo")
        UPLOADS.mkdir(parents=True, exist_ok=True)
        # Store metadata now; downloading can be enabled after deployment storage is configured.
        add_event("מסמך חדש · " + name, "התקבל דרך Telegram · זמין לעיבוד", "מסמך")
        send(chat, f"קיבלתי את {name} ✅\nנוסף למסמכי הטיול.")
        return
    if not text:
        send(chat, "שלח טקסט עם פרטי הזמנה או קובץ.")
        return
    kind = next((k for k in ICONS if k in text), "עדכון")
    title = text.split("\n", 1)[0][:90]
    add_event(title, "נוסף מהבוט · " + (text[:180]), kind)
    send(chat, f"נוסף ל-TRIPY ✅\n{title}")

def poll():
    global OFFSET
    if not TOKEN: raise SystemExit("Missing TELEGRAM_BOT_TOKEN")
    while True:
        try:
            updates = api("getUpdates", {"timeout": 50, "offset": OFFSET})
            for update in updates:
                OFFSET = update["update_id"] + 1
                if update.get("message"): handle_message(update["message"])
        except Exception as exc:
            print("Telegram polling error:", exc, flush=True)
            time.sleep(5)

class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_OPTIONS(self): self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS"); self.end_headers()
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/events": self._json(200, {"events": load_events()}); return
        if self.path in ("/", "/index.html"):
            raw = (ROOT / "index.html").read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        self._json(404, {"error": "not_found"})
    def log_message(self, format, *args): pass

def main():
    if not TOKEN: raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting TRIPY bot")
    api("deleteWebhook")
    threading.Thread(target=poll, daemon=True).start()
    print(f"TRIPY bot/API listening on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__": main()
