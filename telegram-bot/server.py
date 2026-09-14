#!/usr/bin/env python3
"""Small dependency-free TRIPY Telegram bridge for the working travel version."""
import json, os, re, shutil, subprocess, tempfile, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("TRIPY_DATA_FILE", ROOT / "trip-data.json"))
TRIPS = DATA.with_name("trips.json")
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

def load_trips():
    if not TRIPS.exists(): return []
    try: return json.loads(TRIPS.read_text(encoding="utf-8"))
    except (ValueError, OSError): return []

def save_trips(trips):
    tmp = TRIPS.with_suffix(".tmp")
    tmp.write_text(json.dumps(trips, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TRIPS)

def extract_pdf_text(path):
    text = ""
    try:
        result = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, timeout=20)
        if result.returncode == 0: text = result.stdout
    except (OSError, subprocess.SubprocessError): pass
    try:
        with tempfile.TemporaryDirectory() as d:
            prefix = str(Path(d) / "page")
            subprocess.run(["pdftoppm", "-f", "1", "-l", "3", "-r", "200", "-png", str(path), prefix], capture_output=True, timeout=45, check=True)
            for image in sorted(Path(d).glob("page-*.png")):
                result = subprocess.run(["tesseract", str(image), "stdout", "-l", "eng"], capture_output=True, text=True, timeout=30)
                if result.returncode == 0: text += "\n" + result.stdout
    except (OSError, subprocess.SubprocessError): pass
    if text.strip(): return text
    try: return path.read_bytes().decode("utf-8", errors="ignore")
    except OSError: return ""

def create_trip_from_document(filename, path, source="Telegram"):
    text = extract_pdf_text(path)
    known = [("מינכן", "גרמניה"), ("München", "גרמניה"), ("Munich", "גרמניה"), ("ציריך", "שווייץ"), ("Zurich", "שווייץ"), ("רומא", "איטליה"), ("Rome", "איטליה"), ("פריז", "צרפת"), ("Paris", "צרפת"), ("לונדון", "בריטניה"), ("London", "בריטניה")]
    cities = []
    for city, country in known:
        if city.lower() in text.lower() and country not in [x[1] for x in cities]: cities.append((city, country))
    dates = re.findall(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", text)
    if not dates:
        dates = re.findall(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b", text)
        dates = [(y, m, d) for d, m, y in dates]
    if not dates:
        dates = re.findall(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})\b", text)
        dates = [("20" + y, m, d.zfill(2)) for d, m, y in dates]
    months = {"JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06","JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"}
    text_dates = re.findall(r"\b(MON|TUE|WED|THU|FRI|SAT|SUN|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s+(\d{1,2}),?\s+(20\d{2})\b", text, re.I)
    if text_dates: dates += [(y, months[m[:3].upper()], d.zfill(2)) for m, d, y in text_dates]
    start = "-".join(dates[0]) if dates else ""
    end = "-".join(dates[-1]) if len(dates) > 1 else start
    title = (" · ".join(dict.fromkeys(x[1] for x in cities)) + (" · " + " · ".join(dict.fromkeys(x[0] for x in cities)) if cities else "")) or Path(filename).stem or "טיול חדש"
    title = title.replace("Munich", "מינכן").replace("München", "מינכן")
    image = "https://images.unsplash.com/photo-1595867818082-083862f3d630?auto=format&fit=crop&w=1200&q=80" if any(x[1] == "גרמניה" for x in cities) else "https://images.unsplash.com/photo-1527668752968-14dc70a27c95?auto=format&fit=crop&w=1200&q=80"
    trip = {"id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"), "title": title, "start": start, "end": end, "days": len(dates) if dates else 0, "source": source, "document": filename, "image": image}
    trips = load_trips(); trips.insert(0, trip); save_trips(trips)
    return trip

def delete_trip(trip_id):
    trips = [t for t in load_trips() if str(t.get("id")) != str(trip_id)]
    save_trips(trips)
    return trips

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
        local = UPLOADS / (datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S_") + Path(name).name)
        try:
            file_info = api("getFile", {"file_id": item["file_id"]})
            download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info['file_path']}"
            with urllib.request.urlopen(download_url, timeout=60) as response: local.write_bytes(response.read())
            trip = create_trip_from_document(name, local)
            title = trip["title"]
            add_event("טיול חדש · " + title, "נוצר אוטומטית מ־" + name, "מסמך")
            send(chat, f"קיבלתי את {name} ✅\\nנוצר טיול חדש: {title}")
        except Exception as exc:
            print("Document processing error:", exc, flush=True)
            add_event("מסמך חדש · " + name, "התקבל דרך Telegram · ממתין לעיבוד", "מסמך")
            send(chat, f"קיבלתי את {name} ✅\\nהמסמך נשמר, אבל לא הצלחתי לחלץ ממנו את פרטי הטיול.")
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
        self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_OPTIONS(self): self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers()
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/events": self._json(200, {"events": load_events()}); return
        if path == "/api/trips": self._json(200, {"trips": load_trips()}); return
        if path in ("/", "/index.html"):
            raw = (ROOT / "index.html").read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        static = {"/tripy-icon.png": (ROOT / "tripy-icon.png", "image/png"), "/manifest.webmanifest": (ROOT / "manifest.webmanifest", "application/manifest+json")}
        if path in static:
            file_path, content_type = static[path]
            if file_path.exists():
                raw = file_path.read_bytes(); self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        self._json(404, {"error": "not_found"})
    def do_POST(self):
        if self.path != "/api/trips": self._json(404, {"error": "not_found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
            trip = {"id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"), "title": str(payload.get("title") or "טיול חדש")[:120], "start": str(payload.get("start") or ""), "end": str(payload.get("end") or ""), "days": int(payload.get("days") or 0), "source": "Web"}
            trips = load_trips(); trips.insert(0, trip); save_trips(trips); self._json(201, {"trip": trip})
        except (ValueError, TypeError, json.JSONDecodeError): self._json(400, {"error": "invalid_json"})
    def do_DELETE(self):
        prefix = "/api/trips/"
        if not self.path.startswith(prefix): self._json(404, {"error": "not_found"}); return
        self._json(200, {"trips": delete_trip(self.path[len(prefix):])})

def main():
    if not TOKEN: raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting TRIPY bot")
    api("deleteWebhook")
    threading.Thread(target=poll, daemon=True).start()
    print(f"TRIPY bot/API listening on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__": main()
