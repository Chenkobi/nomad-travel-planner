#!/usr/bin/env python3
"""Small dependency-free TRIPY Telegram bridge for the working travel version."""
import base64, json, os, re, shutil, subprocess, tempfile, threading, time, urllib.error, urllib.parse, urllib.request
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
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

def gemini_extract(path):
    if not GEMINI_API_KEY: return None
    schema = {"type":"object","properties":{"type":{"type":"string","enum":["hotel","flight","train","attraction","car_rental","insurance","other"]},"hotel":{"type":"string"},"city":{"type":"string"},"country":{"type":"string"},"check_in":{"type":"string"},"check_out":{"type":"string"},"check_in_time":{"type":"string"},"check_out_time":{"type":"string"},"airline":{"type":"string"},"flight_number":{"type":"string"},"departure_date":{"type":"string"},"departure_time":{"type":"string"},"arrival_date":{"type":"string"},"arrival_time":{"type":"string"},"origin":{"type":"string"},"destination":{"type":"string"},"train_number":{"type":"string"},"attraction":{"type":"string"},"date":{"type":"string"},"time":{"type":"string"},"location":{"type":"string"},"pickup_date":{"type":"string"},"pickup_time":{"type":"string"},"pickup_location":{"type":"string"},"vehicle_type":{"type":"string"},"dropoff_date":{"type":"string"},"dropoff_time":{"type":"string"},"dropoff_location":{"type":"string"}},"required":["type","hotel","city","country","check_in","check_out","check_in_time","check_out_time","airline","flight_number","departure_date","departure_time","arrival_date","arrival_time","origin","destination"]}
    prompt = "Classify this travel PDF and extract only clearly present facts. Return JSON matching the schema. type must be hotel, flight, train, attraction, car_rental, insurance, or other. Insurance must be document-only: do not invent itinerary facts. For flights extract airline, flight_number, departure/arrival ISO dates and 24-hour times, origin and destination airports or cities. For hotels extract exact property name, stay city/country, check-in/out ISO dates and times. For trains extract train_number, origin, destination, departure_date and departure_time. For attractions extract attraction, date, time and location. For car rentals extract pickup_date, pickup_time, pickup_location, vehicle_type, and when clearly present dropoff_date, dropoff_time and dropoff_location. Use empty strings for fields not clearly present; never guess."
    payload = {"contents":[{"parts":[{"text":prompt},{"inline_data":{"mime_type":"application/pdf","data":base64.b64encode(path.read_bytes()).decode("ascii")}}]}],"generationConfig":{"responseMimeType":"application/json","responseSchema":schema,"temperature":0}}
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent", data=json.dumps(payload).encode(), headers={"Content-Type":"application/json","x-goog-api-key":GEMINI_API_KEY}, method="POST")
            with urllib.request.urlopen(req, timeout=90) as response:
                body = json.loads(response.read())
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(text)
            return result if isinstance(result, dict) else None
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt); continue
            print("Gemini extraction HTTP error:", exc.code, flush=True)
            return None
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print("Gemini extraction error:", type(exc).__name__, flush=True)
            return None


def extract_hotel_name(text):
    patterns = [
        r"Adina Apartment Hotel[^\n]+",
        r"Hotel\s+[A-Z][A-Za-zÀ-ÿ]+(?:[ ,&'-]+[A-Za-zÀ-ÿ]+){0,8}",
        r"[A-Z][A-Za-zÀ-ÿ]+(?:[ ,&'-]+[A-Za-zÀ-ÿ]+){0,5}\s+(?:Hotel|Meliá|Melia)(?:[ ,&'-]+[A-Za-zÀ-ÿ]+){0,5}"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = match.group(0).strip()
            if value.lower() not in {"hotel's local time", "hotel's local time fee"}:
                return value
    return ""


def validate_booking_trip(trip):
    hotel = str(trip.get("hotel") or "").strip()
    destinations = trip.get("destinations") or []
    has_destination = any(str(item.get("city") or "").strip() for item in destinations if isinstance(item, dict))
    return bool(hotel and has_destination and trip.get("start") and trip.get("end"))


def create_trip_from_document(filename, path, source="Telegram"):
    text = extract_pdf_text(path)
    ai = gemini_extract(path) or {}
    doc_type = str(ai.get("type") or "hotel").strip().lower()
    if doc_type == "flight":
        departure = str(ai.get("departure_date") or "").strip()
        arrival = str(ai.get("arrival_date") or departure).strip()
        required = [str(ai.get(k) or "").strip() for k in ("airline", "flight_number", "origin", "destination", "departure_date", "departure_time", "arrival_date", "arrival_time")]
        if not departure or not arrival or not all(required):
            raise ValueError("flight document missing unambiguous flight facts")
        trips = load_trips()
        target = next((t for t in trips if t.get("start") <= departure <= t.get("end")), None)
        event = [str(ai.get("departure_time")), "✈️", f"טיסה {ai.get('airline')} {ai.get('flight_number')}", f"{ai.get('origin')} → {ai.get('destination')} · יציאה {ai.get('departure_time')} · הגעה {ai.get('arrival_time')}", "טיסה", source, departure]
        if target:
            target.setdefault("flights", []).append({"airline":ai.get("airline"),"number":ai.get("flight_number"),"origin":ai.get("origin"),"destination":ai.get("destination"),"departure_date":departure,"departure_time":ai.get("departure_time"),"arrival_date":arrival,"arrival_time":ai.get("arrival_time"),"document":filename})
            target["flights"] = list({json.dumps(f, sort_keys=True, ensure_ascii=False): f for f in target["flights"]}.values())
            target.setdefault("documents", []).append(filename)
            target["documents"] = list(dict.fromkeys(target["documents"]))
            target.setdefault("document_titles", {})[filename] = f"כרטיס טיסה · {ai.get('airline')} {ai.get('flight_number')}"
            save_trips(trips)
            events = load_events()
            if not any(len(e) > 6 and e[2] == event[2] and e[6] == departure for e in events):
                events.insert(0, event); save_events(events)
            return {**target, "_ingested_type": "flight"}
        raise ValueError("flight has no existing dated trip to attach to")
    if doc_type == "insurance":
        trips = load_trips()
        policy_start = str(ai.get("check_in") or ai.get("departure_date") or "").strip(); policy_end = str(ai.get("check_out") or ai.get("arrival_date") or "").strip()
        candidates = [t for t in trips if policy_start and t.get("start") <= policy_start <= t.get("end")]
        target = candidates[0] if len(candidates) == 1 else (trips[0] if len(trips) == 1 else None)
        if not target: raise ValueError("insurance cannot be assigned to one dated trip")
        target.setdefault("documents", []).append(filename); target["documents"] = list(dict.fromkeys(target["documents"])); target.setdefault("document_titles", {})[filename] = "ביטוח נסיעות לחו״ל"; save_trips(trips)
        return {**target, "_ingested_type": "insurance"}
    if doc_type == "car_rental":
        pickup_date = str(ai.get("pickup_date") or "").strip(); pickup_time = str(ai.get("pickup_time") or "").strip(); pickup_location = str(ai.get("pickup_location") or "").strip(); vehicle_type = str(ai.get("vehicle_type") or "").strip()
        if not all((pickup_date, pickup_time, pickup_location, vehicle_type)): raise ValueError("car rental missing pickup location, time, date, or vehicle type")
        trips = load_trips(); target = next((t for t in trips if t.get("start") <= pickup_date <= t.get("end")), None)
        if not target: raise ValueError("car rental has no existing dated trip to attach to")
        record = {"pickup_date":pickup_date,"pickup_time":pickup_time,"pickup_location":pickup_location,"vehicle_type":vehicle_type,"dropoff_date":str(ai.get("dropoff_date") or "").strip(),"dropoff_time":str(ai.get("dropoff_time") or "").strip(),"dropoff_location":str(ai.get("dropoff_location") or "").strip(),"document":filename}
        target.setdefault("rentals", []).append(record); target["rentals"] = list({json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in target["rentals"]}.values()); target.setdefault("documents", []).append(filename); target["documents"] = list(dict.fromkeys(target["documents"])); target.setdefault("document_titles", {})[filename] = f"השכרת רכב · {vehicle_type}"; save_trips(trips)
        events = load_events(); title = f"איסוף רכב · {vehicle_type}"; details = f"{pickup_location} · שעה {pickup_time}"; event = [pickup_time, "🚗", title, details, "רכב", source, pickup_date]
        if not any(len(e) > 6 and e[2] == title and e[6] == pickup_date for e in events): events.insert(0, event); save_events(events)
        return {**target, "_ingested_type": "car_rental"}
    if doc_type in ("train", "attraction"):
        if doc_type == "train":
            date = str(ai.get("departure_date") or "").strip(); time_value = str(ai.get("departure_time") or "").strip(); name = str(ai.get("train_number") or "").strip(); origin = str(ai.get("origin") or "").strip(); destination = str(ai.get("destination") or "").strip()
            if not all((date, time_value, name, origin, destination)): raise ValueError("train document missing unambiguous facts")
            title = f"רכבת {name}"; details = f"{origin} → {destination}"; record = {"number":name,"origin":origin,"destination":destination,"date":date,"time":time_value,"document":filename}; kind = "רכבת"; icon = "🚆"
        else:
            date = str(ai.get("date") or "").strip(); time_value = str(ai.get("time") or "").strip(); name = str(ai.get("attraction") or "").strip(); location = str(ai.get("location") or "").strip()
            if not all((date, time_value, name, location)): raise ValueError("attraction document missing unambiguous facts")
            title = name; details = location; record = {"name":name,"location":location,"date":date,"time":time_value,"document":filename}; kind = "אטרקציה"; icon = "🎟️"
        trips = load_trips(); target = next((t for t in trips if t.get("start") <= date <= t.get("end")), None)
        if not target: raise ValueError(f"{doc_type} has no existing dated trip to attach to")
        key = "trains" if doc_type == "train" else "attractions"; target.setdefault(key, []).append(record); target[key] = list({json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in target[key]}.values()); target.setdefault("documents", []).append(filename); target["documents"] = list(dict.fromkeys(target["documents"])); target.setdefault("document_titles", {})[filename] = ((f"כרטיס רכבת · {name}") if doc_type == "train" else f"אטרקציה · {name}"); save_trips(trips)
        events = load_events(); event = [time_value, icon, title, details, kind, source, date]
        if not any(len(e) > 6 and e[2] == title and e[6] == date for e in events): events.insert(0, event); save_events(events)
        return {**target, "_ingested_type": doc_type}
    known = [("Budapest", "הונגריה"), ("בודפשט", "הונגריה"), ("מינכן", "גרמניה"), ("München", "גרמניה"), ("Munich", "גרמניה"), ("פרנקפורט", "גרמניה"), ("Frankfurt", "גרמניה"), ("ציריך", "שווייץ"), ("Zurich", "שווייץ"), ("רומא", "איטליה"), ("Rome", "איטליה"), ("פריז", "צרפת"), ("Paris", "צרפת"), ("לונדון", "בריטניה"), ("London", "בריטניה")]
    city_labels = {"Budapest":"בודפשט", "בודפשט":"בודפשט", "Munich":"מינכן", "München":"מינכן", "מינכן":"מינכן", "Frankfurt":"פרנקפורט", "פרנקפורט":"פרנקפורט"}
    ai_city, ai_country = str(ai.get("city") or "").strip(), str(ai.get("country") or "").strip()
    if ai_city:
        known_city = next((pair for pair in known if pair[0].lower() == ai_city.lower()), (ai_city, ai_country))
        if known_city[0] not in [x[0] for x in known]: known.append(known_city)
    cities = []
    for city, country in known:
        if city.lower() in text.lower() and city not in [x[0] for x in cities]: cities.append((city, country))
    if ai_city and not cities: cities.append((ai_city, ai_country))
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
    label_dates = re.findall(r"check[- ]?in\s*[:\-]?\s*(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s+(\d{1,2}),?\s+(20\d{2})", text, re.I) + re.findall(r"check[- ]?out\s*[:\-]?\s*(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s+(\d{1,2}),?\s+(20\d{2})", text, re.I)
    table_dates = re.findall(r"check[- ]?in\s+check[- ]?out[\s\S]{0,120}?(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s+(\d{1,2}),?\s+(20\d{2})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s+(\d{1,2}),?\s+(20\d{2})", text, re.I)
    if table_dates:
        m1, d1, y1, m2, d2, y2 = table_dates[0]
        dates = [(y1, months[m1[:3].upper()], d1.zfill(2)), (y2, months[m2[:3].upper()], d2.zfill(2))]
    elif label_dates: dates = [(y, months[m[:3].upper()], d.zfill(2)) for m, d, y in label_dates]
    ai_dates = [str(ai.get("check_in") or "").strip(), str(ai.get("check_out") or "").strip()]
    if all(re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value) for value in ai_dates): dates = [(value[:4], value[5:7], value[8:10]) for value in ai_dates]
    start = "-".join(dates[0]) if dates else ""
    end = "-".join(dates[-1]) if len(dates) > 1 else start
    display_cities = list(dict.fromkeys((city_labels.get(x[0]) or x[0]) for x in cities))
    title = (" · ".join(dict.fromkeys(x[1] for x in cities))) or Path(filename).stem or "טיול חדש"
    title = title.replace("Munich", "מינכן").replace("München", "מינכן").replace("Frankfurt", "פרנקפורט").replace("Budapest", "בודפשט")
    image_by_city = {"מינכן": "https://images.unsplash.com/photo-1595867818082-083862f3d630?auto=format&fit=crop&w=1200&q=80", "פרנקפורט": "https://images.unsplash.com/photo-1520699049698-acd2fccb8cc8?auto=format&fit=crop&w=1200&q=80"}
    images = list(dict.fromkeys(image_by_city.get(city.replace("Munich", "מינכן").replace("München", "מינכן").replace("Frankfurt", "פרנקפורט"), "https://images.unsplash.com/photo-1527668752968-14dc70a27c95?auto=format&fit=crop&w=1200&q=80") for city, _ in cities)) or ["https://images.unsplash.com/photo-1527668752968-14dc70a27c95?auto=format&fit=crop&w=1200&q=80"]
    image = images[0]
    destinations = [{"city": city_labels.get(city.replace("Munich", "מינכן").replace("München", "מינכן").replace("Frankfurt", "פרנקפורט").replace("Budapest", "בודפשט")) or city, "country": country, "image": image_by_city.get(city_labels.get(city) or city, image)} for city, country in cities]
    hotel = str(ai.get("hotel") or "").strip() or extract_hotel_name(text)
    checkin_time = str(ai.get("check_in_time") or "14:00").strip()
    checkout_time = str(ai.get("check_out_time") or "11:00").strip()
    document_title = (f"אישור מלון · {hotel}" if hotel else "אישור הזמנה")
    trip = {"id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"), "title": title, "start": start, "end": end, "days": 0, "source": source, "document": filename, "documents": [filename], "image": image, "images": images, "destinations": destinations, "hotel": hotel, "hotels": [{"name": hotel, "city": display_cities[0] if display_cities else ai_city, "country": ai_country or (cities[0][1] if cities else ""), "start": start, "end": end, "checkin_time": checkin_time, "checkout_time": checkout_time, "document": filename}], "checkin_time": checkin_time, "checkout_time": checkout_time, "document_titles": {filename: document_title}}
    if start and end:
        trip["days"] = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1
    if not validate_booking_trip(trip):
        raise ValueError("missing hotel, destination, or dates")
    trips = load_trips()
    def as_date(value):
        try: return datetime.fromisoformat(value).date()
        except (TypeError, ValueError): return None
    new_start, new_end = as_date(start), as_date(end)
    match = next((existing for existing in trips if existing.get("document") == filename or filename in existing.get("documents", [])), None)
    if not match and new_start and new_end:
        for existing in trips:
            old_start, old_end = as_date(existing.get("start")), as_date(existing.get("end"))
            if not old_start or not old_end: continue
            gap = max((new_start - old_end).days, (old_start - new_end).days, 0)
            if gap <= 14:
                match = existing; break
    if match:
        old_start, old_end = as_date(match.get("start")), as_date(match.get("end"))
        merged_start = min(x for x in (old_start, new_start) if x)
        merged_end = max(x for x in (old_end, new_end) if x)
        destinations = match.get("destinations", []) + destinations
        unique = {(d.get("city"), d.get("country")): d for d in destinations}
        match["destinations"] = list(unique.values())
        countries = list(dict.fromkeys(d["country"] for d in match["destinations"]))
        match["title"] = " · ".join(countries)
        match["start"], match["end"] = merged_start.isoformat(), merged_end.isoformat()
        match["days"] = (merged_end - merged_start).days + 1
        match["images"] = list(dict.fromkeys(match.get("images", [match.get("image")] if match.get("image") else []) + [image]))
        match["image"] = match["images"][0]
        same_booking = old_start == new_start and old_end == new_end or any(h.get("start") == start and h.get("end") == end for h in (match.get("hotels") or []))
        if same_booking:
            old_docs = list(match.get("documents", []))
            old_titles = match.get("document_titles", {})
            replaced_docs = []
            for doc in old_docs:
                title = str(old_titles.get(doc, ""))
                if title.startswith("אישור מלון") and any(h.get("document") == doc and h.get("start") == start and h.get("end") == end for h in (match.get("hotels") or [])):
                    continue
                replaced_docs.append(doc)
            match["documents"] = list(dict.fromkeys(replaced_docs + [filename]))
            match["document_titles"] = {**old_titles, **trip["document_titles"]}
            current_events = load_events()
            current_events = [e for e in current_events if not (len(e) > 6 and e[4] == "מלון" and e[6] in (start, end))]
            save_events(current_events)
        else:
            match["documents"] = list(dict.fromkeys(match.get("documents", [match.get("document")] if match.get("document") else []) + [filename]))
            match["document_titles"] = {**match.get("document_titles", {}), **trip["document_titles"]}
        booking = {"name": hotel, "city": display_cities[0] if display_cities else ai_city, "country": ai_country or (cities[0][1] if cities else ""), "start": start, "end": end, "checkin_time": checkin_time, "checkout_time": checkout_time, "document": filename}
        existing_hotels = match.get("hotels") or ([{"name": match.get("hotel"), "start": match.get("start"), "end": match.get("end"), "checkin_time": match.get("checkin_time", "14:00"), "checkout_time": match.get("checkout_time", "11:00")} ] if match.get("hotel") else [])
        if same_booking:
            existing_hotels = [h for h in existing_hotels if not (h.get("start") == start and h.get("end") == end)] + [booking]
        elif hotel:
            existing_hotels = [h for h in existing_hotels if not (h.get("start") == start and h.get("end") == end)] + [booking]
        match["hotels"] = existing_hotels
        if hotel and same_booking: match["hotel"] = hotel; match["checkin_time"] = checkin_time; match["checkout_time"] = checkout_time
        elif not match.get("hotel") and hotel: match["hotel"] = hotel
        save_trips(trips)
        return match
    trips.insert(0, trip); save_trips(trips)
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

def add_event(title, details, kind, source="Telegram", event_time=None):
    events = load_events()
    now = event_time or datetime.now().strftime("%H:%M")
    event = [now, ICONS.get(kind, "📌"), title, details, kind, source]
    events.insert(0, event)
    save_events(events)
    return event

def ensure_hotel_events():
    events = load_events()
    trips = load_trips()
    before = len(events)
    demo_markers = ("LX 162", "Ruby Mimi", "רכבת לציריך", "אישור חדש")
    events = [e for e in events if len(e) < 5 or (e[2] not in demo_markers and e[4] != "מסמך")]
    seen = set()
    seen_hotel_dates = set()
    cleaned = []
    for event in events:
        event_date = str(event[6] if len(event) > 6 else "")
        if len(event) > 6 and event[4] == "מלון" and event_date:
            hotel_key = ("out" if "אאוט" in str(event[2]) else "in", event_date)
            if hotel_key in seen_hotel_dates: continue
            seen_hotel_dates.add(hotel_key)
        key = (str(event[2]), event_date)
        if key not in seen:
            seen.add(key); cleaned.append(event)
    events = cleaned
    changed = len(events) != before
    trips_changed = False
    for trip in trips:
        label_map = {"Budapest":"בודפשט", "Munich":"מינכן", "München":"מינכן", "Frankfurt":"פרנקפורט"}
        for destination in trip.get("destinations", []):
            if destination.get("city") in label_map: destination["city"] = label_map[destination["city"]]; trips_changed = True
        if trip.get("destinations"):
            countries = list(dict.fromkeys(d.get("country", "") for d in trip["destinations"] if d.get("country")))
            cities = list(dict.fromkeys(d.get("city", "") for d in trip["destinations"] if d.get("city")))
            normalized_title = " · ".join(countries)
            if normalized_title and trip.get("title") != normalized_title: trip["title"] = normalized_title; trips_changed = True
        hotel = trip.get("hotel") or ""
        if not hotel and trip.get("document"):
            candidates = list(UPLOADS.glob("*" + Path(trip["document"]).name)) + list(UPLOADS.glob("*" + Path(trip["document"]).stem + "*"))
            for candidate in candidates:
                hotel = extract_hotel_name(extract_pdf_text(candidate))
                if hotel:
                    trip["hotel"] = hotel; trips_changed = True; break
        hotel = hotel or "המלון"
        docs = list(dict.fromkeys(trip.get("documents") or ([trip.get("document")] if trip.get("document") else [])))
        titles = trip.get("document_titles", {})
        unique_docs = []
        seen_titles = set()
        for doc in reversed(docs):
            if not doc: continue
            desired_title = f"אישור מלון · {hotel}" if hotel != "המלון" else "אישור מלון"
            if desired_title in seen_titles: continue
            seen_titles.add(desired_title); unique_docs.append(doc); titles[doc] = desired_title
        docs = list(reversed(unique_docs))
        if trip.get("documents") != docs: trip["documents"] = docs; trips_changed = True
        if trip.get("document_titles") != titles: trip["document_titles"] = titles; trips_changed = True
        for event in events:
            if len(event) >= 3 and event[2].startswith("צ׳ק-") and "המלון" in event[2]:
                event[2] = event[2].replace("המלון", hotel); changed = True
        if trip.get("start"):
            key = ("צ׳ק-אין · " + hotel, trip["start"])
            if key not in seen:
                events.insert(0, [trip.get("checkin_time", "14:00"), ICONS["מלון"], key[0], f"{trip['start']} · שעה: {trip.get('checkin_time', '14:00')}", "מלון", "PDF", trip["start"]]); seen.add(key); changed = True
        if trip.get("end") and trip.get("end") != trip.get("start"):
            key = ("צ׳ק-אאוט · " + hotel, trip["end"])
            if key not in seen:
                events.insert(0, [trip.get("checkout_time", "11:00"), ICONS["מלון"], key[0], f"{trip['end']} · שעה: {trip.get('checkout_time', '11:00')}", "מלון", "PDF", trip["end"]]); seen.add(key); changed = True
    if trips_changed: save_trips(trips)
    if changed: save_events(events)
    return events


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
            if trip.get("_ingested_type", "hotel") == "hotel" and trip.get("start"):
                add_event("צ׳ק-אין · " + (trip.get("hotel") or title), f"{trip['start']} · שעה: {trip.get('checkin_time', '14:00')}", "מלון", event_time=trip.get("checkin_time", "14:00"))
            if trip.get("_ingested_type", "hotel") == "hotel" and trip.get("end") and trip.get("end") != trip.get("start"):
                add_event("צ׳ק-אאוט · " + (trip.get("hotel") or title), f"{trip['end']} · שעה: {trip.get('checkout_time', '11:00')}", "מלון", event_time=trip.get("checkout_time", "11:00"))
            send(chat, f"קיבלתי את {name} ✅\\nעודכן הטיול: {title}")
        except Exception as exc:
            print("Document processing error:", exc, flush=True)
            send(chat, f"יש בעיה בזיהוי האישור ❌\nלא עודכן הלוז. צריך לזהות בוודאות שם מלון, יעד ותאריכים.")
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
    def do_OPTIONS(self): self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers()
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/events": self._json(200, {"events": ensure_hotel_events()}); return
        if path == "/api/trips": ensure_hotel_events(); self._json(200, {"trips": load_trips()}); return
        if path.startswith("/api/documents/"):
            filename = Path(urllib.parse.unquote(path[len("/api/documents/"):])).name
            file_path = UPLOADS / filename
            if file_path.exists() and file_path.is_file():
                raw = file_path.read_bytes(); self.send_response(200); self.send_header("Content-Type", "application/pdf"); self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(filename)); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            self._json(404, {"error": "document_not_found"}); return
        if path in ("/", "/index.html"):
            raw = (ROOT / "index.html").read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        static = {"/tripy-icon.png": (ROOT / "tripy-icon.png", "image/png"), "/manifest.webmanifest": (ROOT / "manifest.webmanifest", "application/manifest+json")}
        if path in static:
            file_path, content_type = static[path]
            if file_path.exists():
                raw = file_path.read_bytes(); self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        self._json(404, {"error": "not_found"})
    def do_POST(self):
        if self.path == "/api/events":
            try:
                length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}"); index = int(payload.get("index")); event = payload.get("event")
                events = load_events()
                if index < 0 or index >= len(events) or not isinstance(event, list): self._json(400, {"error": "invalid_event"}); return
                events[index] = event[:6]; save_events(events); self._json(200, {"event": events[index]})
            except (ValueError, TypeError, json.JSONDecodeError): self._json(400, {"error": "invalid_json"})
            return
        if self.path != "/api/trips": self._json(404, {"error": "not_found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
            trip = {"id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"), "title": str(payload.get("title") or "טיול חדש")[:120], "start": str(payload.get("start") or ""), "end": str(payload.get("end") or ""), "days": int(payload.get("days") or 0), "source": "Web"}
            trips = load_trips(); trips.insert(0, trip); save_trips(trips); self._json(201, {"trip": trip})
        except (ValueError, TypeError, json.JSONDecodeError): self._json(400, {"error": "invalid_json"})
    def do_PATCH(self):
        prefix = "/api/trips/"
        if not self.path.startswith(prefix): self._json(404, {"error": "not_found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
            trip_id = urllib.parse.unquote(self.path[len(prefix):]); trips = load_trips()
            for trip in trips:
                if str(trip.get("id")) == trip_id:
                    for key in ("hotel", "title", "start", "end", "days"):
                        if key in payload: trip[key] = payload[key]
                    save_trips(trips); self._json(200, {"trip": trip}); return
            self._json(404, {"error": "trip_not_found"})
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
