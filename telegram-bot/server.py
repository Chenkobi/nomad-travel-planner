#!/usr/bin/env python3
"""Small dependency-free TRIPY Telegram bridge for the working travel version."""
import base64, hashlib, hmac, io, json, os, re, secrets, shutil, subprocess, tempfile, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.message import Message
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mimetypes
try:
    from pywebpush import webpush as _webpush
except ImportError:
    _webpush = None
try:
    from webauthn import (generate_authentication_options, generate_registration_options,
                          options_to_json, verify_authentication_response,
                          verify_registration_response)
    from webauthn.helpers.cose import COSEAlgorithmIdentifier
    from webauthn.helpers.structs import AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, UserVerificationRequirement
except ImportError:
    generate_authentication_options = generate_registration_options = None
    options_to_json = verify_authentication_response = verify_registration_response = None
    COSEAlgorithmIdentifier = AuthenticatorSelectionCriteria = PublicKeyCredentialDescriptor = UserVerificationRequirement = None
try:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
except ImportError:
    ec = None

from booking_lifecycle import (
    BookingDateError,
    cancel_booking,
    enrich_booking_lifecycle,
    find_booking_matches,
    validate_date_range,
    deduplicate_flights,
    flight_identity,
    replace_booking,
)
from booking_types import infer_extended_type, normalize_insurance
from booking_extraction import extract_booking_facts
from booking_facts import merge_normalized_booking_facts, normalize_booking_facts
from semantic_hotel import understand_hotel_reservation
from event_contract import event_as_legacy_list, event_identity
from email_rules import _strong_confirmation_subject, classify_email_intent, looks_like_travel_email
from provider_rules import enrich_ai_with_provider, identify_email_provider

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("TRIPY_DATA_FILE", ROOT / "trip-data.json"))
TRIPS = DATA.with_name("trips.json")
UPLOADS = Path(os.getenv("TRIPY_UPLOAD_DIR", ROOT / "trip-uploads"))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_CHAT = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "8787"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
EMAIL_INTAKE_SECRET = os.getenv("TRIPY_INTAKE_SECRET", "").strip()
API_AUTH_TOKEN = os.getenv("TRIPY_API_AUTH_TOKEN", "").strip()
API_ALLOWED_ORIGIN = os.getenv("TRIPY_API_ALLOWED_ORIGIN", "https://trip.atlasim.co.il").strip()
WEBAUTHN_RP_ID = os.getenv("TRIPY_WEBAUTHN_RP_ID", "trip.atlasim.co.il").strip()
WEBAUTHN_RP_NAME = os.getenv("TRIPY_WEBAUTHN_RP_NAME", "TRIPY").strip()
WEBAUTHN_ORIGIN = os.getenv("TRIPY_WEBAUTHN_ORIGIN", "https://trip.atlasim.co.il").strip()
EMAIL_DIR = Path(os.getenv("TRIPY_EMAIL_DIR", ROOT / "trip-email"))
EMAIL_INDEX = EMAIL_DIR / "index.json"
AUTH_STORE = EMAIL_DIR / "auth.json"
SHARE_STORE = EMAIL_DIR / "share-tokens.json"
TRIPY_SETUP_TOKEN = os.getenv("TRIPY_SETUP_TOKEN", "").strip()
SESSION_COOKIE = "tripy_session"
SHARE_COOKIE = "tripy_share"
PASSWORD_ITERATIONS = 310_000
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
SHARE_TTL_SECONDS = 7 * 24 * 60 * 60
TELEGRAM_INTAKE = Path(os.getenv("TRIPY_TELEGRAM_INTAKE_FILE", ROOT / "telegram-intake.json"))
NOTIFICATION_STATE = EMAIL_DIR / "notification-state.json"
PUSH_SUBSCRIPTIONS = Path(os.getenv("TRIPY_PUSH_SUBSCRIPTIONS_FILE", EMAIL_DIR / "push-subscriptions.json"))
VAPID_KEYS = Path(os.getenv("TRIPY_VAPID_KEYS_FILE", EMAIL_DIR / "vapid-keys.json"))
VAPID_PRIVATE_KEY_FILE = Path(os.getenv("TRIPY_VAPID_PRIVATE_KEY_FILE", EMAIL_DIR / "vapid-private.pem"))
VAPID_SUBJECT = os.getenv("TRIPY_VAPID_SUBJECT", "mailto:trippy@atlasim.co.il").strip()
EMAIL_PROCESSING_LEASE_SECONDS = int(os.getenv("TRIPY_EMAIL_PROCESSING_LEASE_SECONDS", "900"))
TELEGRAM_STARTUP_RETRIES = max(1, int(os.getenv("TRIPY_TELEGRAM_STARTUP_RETRIES", "3")))
TELEGRAM_RETRY_DELAY_SECONDS = max(0, float(os.getenv("TRIPY_TELEGRAM_RETRY_DELAY_SECONDS", "5")))
MAX_PROCESSING_STAGE = 64
MAX_PROCESSING_ERROR = 256
MAX_STAGE_HISTORY = 12
MAX_EXTRACTION_ATTEMPTS = 8
MAX_TELEMETRY_STRING = 180
MAX_DOCUMENT_SIZE = int(os.getenv("TRIPY_MAX_DOCUMENT_SIZE", str(10 * 1024 * 1024)))
MAX_UPLOAD_REQUEST_SIZE = MAX_DOCUMENT_SIZE + (1024 * 1024)
MAX_EMAIL_SIZE = 30 * 1024 * 1024
OFFSET = 0
LOCK = threading.Lock()
TELEGRAM_STATE_LOCK = threading.Lock()
TELEGRAM_STATE = {"status": "disabled", "attempts": 0, "last_error": None, "last_success_at": None}
HTTP_READY = False
EMAIL_READY = False

ICONS = {"טיסה": "✈️", "רכבת": "🚆", "מלון": "🏨", "אטרקציה": "🎟️", "מסעדה": "🍽️"}


def document_content_type(path):
    """Return a safe response MIME type for a stored document."""
    suffix = Path(path).suffix.lower()
    known = {
        ".txt": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    return known.get(suffix, "application/octet-stream")


def safe_document_filename(value, fallback="document.bin"):
    name = Path(str(value or fallback)).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name[:180] or fallback


def resolve_document_path(root, requested):
    root = Path(root).resolve()
    requested = str(requested or "")
    if not requested or Path(requested).name != requested:
        return None
    candidate = (root / requested).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def bounded_document_bytes(value, max_size=MAX_DOCUMENT_SIZE):
    if len(value) > max_size:
        raise ValueError("document_too_large")
    return value


def read_bounded_stream(stream, max_size=MAX_DOCUMENT_SIZE):
    chunks = []
    total = 0
    while True:
        chunk = stream.read(min(64 * 1024, max_size - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise ValueError("document_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def read_bounded_file(path, max_size=MAX_DOCUMENT_SIZE):
    with Path(path).open("rb") as stream:
        return read_bounded_stream(stream, max_size=max_size)


def is_supported_upload_filename(filename):
    return Path(str(filename or "")).suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}


def waze_destination(name, address):
    """Return an address only when it is present and not name-only."""
    destination = str(address or "").replace("&amp;", "&").strip()
    label = str(name or "").strip().casefold()
    if not destination or (label and destination.casefold() == label):
        return ""
    return destination


def api_requires_auth(path):
    """Protect private API data while leaving health, auth, rates and intake public."""
    return path.startswith("/api/") and path not in (
        "/api/rates", "/api/intake/email", "/api/auth/setup", "/api/auth/login",
        "/api/auth/me", "/api/auth/logout", "/api/auth/passkey/authenticate",
        "/api/auth/passkey/authenticate/options", "/api/share/me",
    ) and not path.startswith("/api/share/")


def _auth_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_auth_json(path, value):
    _save_json_private(path, value)


def _password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return {"salt": base64.urlsafe_b64encode(salt).decode("ascii"), "hash": base64.urlsafe_b64encode(digest).decode("ascii"), "iterations": PASSWORD_ITERATIONS}


def _password_matches(password, record):
    try:
        salt = base64.urlsafe_b64decode(str(record["salt"]).encode("ascii"))
        expected = base64.urlsafe_b64decode(str(record["hash"]).encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, int(record.get("iterations", PASSWORD_ITERATIONS)))
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError):
        return False


def _username_matches(username, record):
    expected = str(record.get("username", "")).strip().casefold()
    supplied = str(username or "").strip().casefold()
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def _webauthn_available():
    return bool(generate_registration_options and verify_registration_response)


def _b64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value):
    text = str(value or "")
    return base64.urlsafe_b64decode(text.encode("ascii") + b"=" * (-len(text) % 4))


def _webauthn_challenges(store):
    return store.setdefault("webauthn_challenges", {})


def _passkey_unavailable(handler):
    if not _webauthn_available():
        handler._json(503, {"error": "passkeys_unavailable"})
        return True
    return False


def _cookie_value(headers, name):
    cookie = SimpleCookie()
    try:
        cookie.load(str(headers.get("Cookie", "")))
    except (ValueError, TypeError):
        return ""
    item = cookie.get(name)
    return item.value if item else ""


def _auth_enabled():
    return bool(TRIPY_SETUP_TOKEN or _auth_json(AUTH_STORE).get("user"))


def _session_user(headers):
    value = _cookie_value(headers, SESSION_COOKIE)
    if not value:
        return None
    store = _auth_json(AUTH_STORE)
    record = (store.get("sessions") or {}).get(hashlib.sha256(value.encode()).hexdigest())
    if not record or float(record.get("expires_at", 0)) <= time.time():
        return None
    return {"id": "owner"}


def api_request_authorized(headers):
    """Accept the legacy API token or a persistent authenticated session."""
    if API_AUTH_TOKEN:
        authorization = str(headers.get("Authorization", ""))
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        supplied = supplied or str(headers.get("X-TRIPY-API-TOKEN", ""))
        if supplied and hmac.compare_digest(supplied, API_AUTH_TOKEN):
            return True
        if _cookie_value(headers, SESSION_COOKIE) == API_AUTH_TOKEN:
            return True
    if _session_user(headers):
        return True
    # Preserve the old local no-token behavior until a setup token/account exists.
    return not API_AUTH_TOKEN and not _auth_enabled()


def _new_session():
    value = secrets.token_urlsafe(32)
    store = _auth_json(AUTH_STORE)
    sessions = store.setdefault("sessions", {})
    sessions[hashlib.sha256(value.encode()).hexdigest()] = {"created_at": time.time(), "expires_at": time.time() + SESSION_TTL_SECONDS}
    _save_auth_json(AUTH_STORE, store)
    return value


def _session_header(value, *, share=False, clear=False):
    name = SHARE_COOKIE if share else SESSION_COOKIE
    path = "/api/share" if share else "/"
    max_age = 0 if clear else (SHARE_TTL_SECONDS if share else SESSION_TTL_SECONDS)
    cookie_value = "" if clear else value
    return f"{name}={cookie_value}; Path={path}; Max-Age={max_age}; HttpOnly; SameSite=Lax; Secure"


def _share_records():
    value = _auth_json(SHARE_STORE)
    records = value.get("tokens")
    return records if isinstance(records, list) else []


def _save_share_records(records):
    _save_auth_json(SHARE_STORE, {"tokens": records})


def _scoped_trip(trip_id):
    return next((trip for trip in load_trips() if str(trip.get("id")) == str(trip_id)), None)


def _share_trip(headers):
    value = _cookie_value(headers, SHARE_COOKIE)
    if not value:
        return None
    digest = hashlib.sha256(value.encode()).hexdigest()
    now = time.time()
    for record in _share_records():
        if record.get("session_hash") == digest and not record.get("revoked") and float(record.get("session_expires_at", 0)) > now:
            return _scoped_trip(record.get("trip_id"))
    return None


def cors_origin(origin):
    """Return the permitted CORS origin; wildcard is local-only fallback."""
    if not API_AUTH_TOKEN:
        return "*"
    if origin and API_ALLOWED_ORIGIN and hmac.compare_digest(origin, API_ALLOWED_ORIGIN):
        return origin
    return None

def load_events():
    if not DATA.exists(): return []
    try: return json.loads(DATA.read_text(encoding="utf-8"))
    except (ValueError, OSError): return []

def save_events(events):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    seen_flights = set()
    cleaned = []
    for event in events or []:
        if len(event) > 6 and str(event[4]) == "טיסה":
            key = (str(event[2]).strip().casefold(), str(event[6]), str(event[3]).strip().casefold())
            if key in seen_flights:
                continue
            seen_flights.add(key)
        cleaned.append(event)
    with tempfile.NamedTemporaryFile("w", dir=DATA.parent, prefix=f".{DATA.name}.", suffix=".tmp", encoding="utf-8", delete=False) as handle:
        handle.write(json.dumps(cleaned, ensure_ascii=False, indent=2))
        tmp = Path(handle.name)
    tmp.replace(DATA)

def load_trips():
    if not TRIPS.exists(): return []
    try:
        trips = json.loads(TRIPS.read_text(encoding="utf-8"))
        if not isinstance(trips, list):
            return []
        changed = False
        for trip in trips:
            if isinstance(trip, dict) and isinstance(trip.get("flights"), list):
                flights = deduplicate_flights(trip["flights"])
                if len(flights) != len(trip["flights"]):
                    trip["flights"] = flights
                    changed = True
        if changed:
            save_trips(trips)
        return trips
    except (ValueError, OSError):
        return []

def save_trips(trips):
    TRIPS.parent.mkdir(parents=True, exist_ok=True)
    for trip in trips or []:
        if isinstance(trip, dict) and isinstance(trip.get("flights"), list):
            trip["flights"] = deduplicate_flights(trip["flights"])
    with tempfile.NamedTemporaryFile("w", dir=TRIPS.parent, prefix=f".{TRIPS.name}.", suffix=".tmp", encoding="utf-8", delete=False) as handle:
        handle.write(json.dumps(trips, ensure_ascii=False, indent=2))
        tmp = Path(handle.name)
    tmp.replace(TRIPS)


def load_email_index():
    if not EMAIL_INDEX.exists(): return []
    try:
        value = json.loads(EMAIL_INDEX.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (ValueError, OSError):
        return []


def _email_raw_source(record):
    email_id = str(record.get("id") or "").strip()
    if not email_id:
        return ""
    for candidate in sorted(UPLOADS.glob(f"email_{email_id}_*.txt")):
        resolved = resolve_document_path(UPLOADS, candidate.name)
        if resolved is not None and resolved.exists() and resolved.is_file():
            return f"/api/documents/{urllib.parse.quote(candidate.name)}"
    return ""


def _cleared_notification_ids():
    try:
        value = json.loads(NOTIFICATION_STATE.read_text(encoding="utf-8")) if NOTIFICATION_STATE.exists() else []
        return set(str(item) for item in value) if isinstance(value, list) else set()
    except (OSError, ValueError, TypeError):
        return set()


def clear_notifications():
    ids = {str(record.get("id") or "") for record in load_email_index() if record.get("id")}
    _save_json_private(NOTIFICATION_STATE, sorted(ids))
    return len(ids)


def load_notifications(limit=50):
    """Return bounded, non-sensitive in-app notifications from intake outcomes."""
    cleared = _cleared_notification_ids()
    notifications = []
    for record in load_email_index():
        if str(record.get("id") or "") in cleared:
            continue
        status = str(record.get("status") or "").strip()
        if status not in {"processed", "needs_review", "failed"}:
            continue
        notifications.append({
            "id": str(record.get("id") or ""),
            "status": status,
            "intent": str(record.get("intent") or "unknown"),
            "subject": str(record.get("subject") or "מייל נסיעות")[:180],
            "received_at": record.get("received_at") or "",
            "processed_at": record.get("processed_at") or "",
            "review_reason": str(record.get("review_reason") or "")[:180],
            "provider": str(record.get("provider") or "")[:100],
            "body_preview": re.sub(r"\s+", " ", email_body_text(record)).strip()[:280],
            "raw_source": _email_raw_source(record),
            "trip_id": record.get("trip_id"),
        })
        if len(notifications) >= max(1, min(int(limit), 100)):
            break
    return notifications
def _load_json_list(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return value if isinstance(value, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _save_json_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", encoding="utf-8", delete=False) as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2))
        tmp = Path(handle.name)
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def vapid_public_key():
    if ec is None:
        return ""
    if not VAPID_KEYS.exists():
        private = ec.generate_private_key(ec.SECP256R1())
        public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        private_pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
        _save_json_private(VAPID_KEYS, {"private_key": private_pem, "public_key": base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")})
    try:
        value = json.loads(VAPID_KEYS.read_text(encoding="utf-8"))
        private_pem = str(value.get("private_key") or "")
        if private_pem and not VAPID_PRIVATE_KEY_FILE.exists():
            VAPID_PRIVATE_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            VAPID_PRIVATE_KEY_FILE.write_text(private_pem, encoding="ascii")
            VAPID_PRIVATE_KEY_FILE.chmod(0o600)
        return str(value.get("public_key") or "")
    except (OSError, ValueError, TypeError):
        return ""


def save_push_subscription(subscription):
    if not isinstance(subscription, dict):
        raise ValueError("invalid_push_payload")
    # Accept both PushSubscription.toJSON() and the explicit browser payload.
    nested = subscription.get("subscription")
    if isinstance(nested, dict):
        subscription = nested
    endpoint = str(subscription.get("endpoint") or "").strip()
    if not endpoint or len(endpoint) > 4096:
        raise ValueError("invalid_push_endpoint")
    keys = subscription.get("keys")
    if not isinstance(keys, dict):
        raise ValueError("invalid_push_keys_object")
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not p256dh or not auth:
        raise ValueError("invalid_push_keys")
    items = _load_json_list(PUSH_SUBSCRIPTIONS)
    items = [item for item in items if str(item.get("endpoint") or "") != endpoint]
    items.append({"endpoint": endpoint, "keys": {"p256dh": str(keys["p256dh"]), "auth": str(keys["auth"])}})
    _save_json_private(PUSH_SUBSCRIPTIONS, items[-20:])
    return len(items[-20:])


def _push_failure_code(exc):
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in {404, 410} or "404" in str(exc) or "410" in str(exc):
        return "subscription_expired"
    if status in {401, 403}:
        return "vapid_or_authorization"
    if status:
        return f"provider_http_{status}"
    detail = " ".join(str(exc).split())[:180].lower()
    if isinstance(exc, ValueError) and detail:
        return f"valueerror_{detail}"
    return type(exc).__name__.lower() or "push_delivery_failed"


def send_push_notification(title, body, tag="tripy-booking", details=False):
    result = {"sent": 0, "subscriptions": 0, "failures": [], "stale_removed": 0}
    if _webpush is None:
        result["failures"] = ["webpush_unavailable"]
        return result if details else 0
    if not VAPID_KEYS.exists():
        result["failures"] = ["vapid_keys_missing"]
        return result if details else 0
    # Materialize the PEM file from the persistent key store if needed.
    vapid_public_key()
    if not VAPID_PRIVATE_KEY_FILE.exists():
        result["failures"] = ["vapid_private_key_missing"]
        return result if details else 0
    try:
        keys = json.loads(VAPID_KEYS.read_text(encoding="utf-8"))
        if not keys.get("private_key"):
            raise ValueError("vapid_private_key_missing")
        private_key = str(VAPID_PRIVATE_KEY_FILE)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        result["failures"] = [_push_failure_code(exc)]
        return result if details else 0
    subscriptions = _load_json_list(PUSH_SUBSCRIPTIONS)
    result["subscriptions"] = len(subscriptions)
    delivered = 0
    stale = []
    failures = []
    payload = json.dumps({"title": str(title)[:80], "body": str(body)[:240], "tag": tag, "url": "/"}, ensure_ascii=False)
    for subscription in subscriptions:
        try:
            _webpush(subscription_info=subscription, data=payload, vapid_private_key=private_key, vapid_claims={"sub": VAPID_SUBJECT})
            delivered += 1
        except Exception as exc:
            code = _push_failure_code(exc)
            failures.append(code)
            if code == "subscription_expired":
                stale.append(subscription.get("endpoint"))
    if stale:
        _save_json_private(PUSH_SUBSCRIPTIONS, [item for item in _load_json_list(PUSH_SUBSCRIPTIONS) if item.get("endpoint") not in stale])
    result.update({"sent": delivered, "failures": sorted(set(failures)), "stale_removed": len(stale)})
    return result if details else delivered
def save_email_index(items):
    EMAIL_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=EMAIL_DIR, prefix=".index.", suffix=".tmp", encoding="utf-8", delete=False) as handle:
        handle.write(json.dumps(items, ensure_ascii=False, indent=2))
        tmp = Path(handle.name)
    tmp.replace(EMAIL_INDEX)


def clear_email_intake_permanently():
    with LOCK:
        records = load_email_index()
        removed = len(records)
        for record in records:
            folder = email_record_folder(record)
            if folder.exists() and folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
        save_email_index([])
        if UPLOADS.exists():
            for candidate in UPLOADS.glob("email_*_*.txt"):
                try:
                    candidate.unlink()
                except OSError:
                    pass
        _save_json_private(NOTIFICATION_STATE, [])
        return removed


def load_telegram_intake():
    if not TELEGRAM_INTAKE.exists(): return []
    try:
        value = json.loads(TELEGRAM_INTAKE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, ValueError, TypeError):
        return []


def save_telegram_intake(items):
    TELEGRAM_INTAKE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=TELEGRAM_INTAKE.parent, prefix=f".{TELEGRAM_INTAKE.name}.", suffix=".tmp", encoding="utf-8", delete=False) as handle:
        handle.write(json.dumps(items, ensure_ascii=False, indent=2))
        tmp = Path(handle.name)
    tmp.replace(TELEGRAM_INTAKE)


def update_telegram_intake(intake_id, **changes):
    with LOCK:
        items = load_telegram_intake()
        for record in items:
            if record.get("id") == intake_id:
                record.update(changes)
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_telegram_intake(items)
                return record
    return None


def telegram_media_descriptor(message):
    item = message.get("document")
    if item:
        return item, str(item.get("file_name") or "telegram-document"), str(item.get("mime_type") or "application/octet-stream")
    photos = message.get("photo") or []
    if photos:
        return photos[-1], "telegram-photo.jpg", "image/jpeg"
    return None, "", ""


def filename_for_mime(filename, mime_type, remote_path=""):
    filename = Path(str(filename or "")).name
    if not Path(filename).suffix:
        remote_suffix = Path(str(remote_path or "")).suffix
        suffix = remote_suffix or {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "text/html": ".html", "text/plain": ".txt"}.get(mime_type, ".bin")
        filename += suffix
    return filename or "telegram-document.bin"


def decode_email_header(value):
    if not value: return ""
    try: return str(make_header(decode_header(str(value))))
    except (ValueError, TypeError): return str(value)


def safe_email_filename(value, fallback):
    name = Path(str(value or fallback)).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name[:180] or fallback


def store_incoming_email(raw):
    if not raw: raise ValueError("empty_email")
    if len(raw) > MAX_EMAIL_SIZE: raise ValueError("email_too_large")
    digest = hashlib.sha256(raw).hexdigest()
    message = BytesParser(policy=policy.default).parsebytes(raw)
    message_id = str(message.get("Message-ID") or "").strip()
    dedupe_key = "message-id:" + message_id if message_id else "sha256:" + digest
    with LOCK:
        existing = next((item for item in load_email_index() if item.get("dedupe_key") == dedupe_key), None)
        if existing: return existing, True
        intake_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + "-" + digest[:12]
        folder = EMAIL_DIR / intake_id
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "source.eml").write_bytes(raw)
        text_body = []
        html_body = []
        attachments = []
        for part in message.walk():
            if part.is_multipart(): continue
            content = part.get_payload(decode=True) or b""
            disposition = part.get_content_disposition()
            filename = part.get_filename()
            if disposition == "attachment" or filename:
                bounded_document_bytes(content)
                stored_name = safe_email_filename(decode_email_header(filename), "attachment.bin")
                target = folder / (f"{len(attachments)+1}_" + stored_name)
                target.write_bytes(content)
                attachments.append({"filename": stored_name, "path": target.name, "content_type": part.get_content_type(), "size": len(content)})
                continue
            if part.get_content_type() == "text/plain":
                try: text_body.append(part.get_content())
                except (LookupError, TypeError): text_body.append(content.decode(part.get_content_charset() or "utf-8", errors="replace"))
            elif part.get_content_type() == "text/html":
                try: html_body.append(part.get_content())
                except (LookupError, TypeError): html_body.append(content.decode(part.get_content_charset() or "utf-8", errors="replace"))
        body_text = "\n\n".join(text_body)
        body_html = "\n\n".join(html_body)
        (folder / "body.txt").write_text(body_text, encoding="utf-8")
        (folder / "body.html").write_text(body_html, encoding="utf-8")
        sender = decode_email_header(message.get("From"))
        reply_to = decode_email_header(message.get("Reply-To"))
        subject = decode_email_header(message.get("Subject"))
        attachment_text = []
        for item in attachments:
            suffix = Path(str(item.get("filename") or "")).suffix.lower()
            if item.get("content_type") == "text/html" or suffix in {".html", ".htm"}:
                try:
                    attachment_text.append(html_to_text(decode_html_bytes((folder / str(item["path"])).read_bytes())))
                except (OSError, UnicodeError):
                    pass
        provider_source = "\n".join((
            body_text,
            html_to_text(body_html),
            " ".join(str(item.get("filename") or "") for item in attachments),
            "\n".join(attachment_text),
            "\n".join(message.get_all("Received", [])),
        ))
        provider = identify_email_provider(sender=sender, reply_to=reply_to, subject=subject, body=provider_source)
        record = {"id": intake_id, "status": "received", "source": "Email", "dedupe_key": dedupe_key, "sha256": digest, "message_id": message_id, "from": sender, "to": decode_email_header(message.get("To")), "reply_to": reply_to, "subject": subject, "date": decode_email_header(message.get("Date")), "received_at": datetime.now(timezone.utc).isoformat(), "source_file": f"{intake_id}/source.eml", "body_file": f"{intake_id}/body.txt", "attachments": attachments, "provider": provider["provider"], "provider_category_hint": provider["category_hint"], "provider_confidence": provider["confidence"], "provider_evidence": provider["evidence"]}
        items = load_email_index(); items.insert(0, record); save_email_index(items)
        return record, False


def _utc_now():
    return datetime.now(timezone.utc)


def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _redacted_error(error):
    text = re.sub(r"(?i)(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", str(error or ""))
    text = re.sub(r"(?i)bearer\s+[^\s]+", "Bearer [REDACTED]", text)
    return text[:MAX_PROCESSING_ERROR]


_TELEMETRY_TYPES = {"hotel", "flight", "train", "attraction", "car_rental", "restaurant", "insurance", "other"}
_TELEMETRY_STATUSES = {"confirmed", "modified", "cancelled", "refunded", "unknown"}
_TELEMETRY_RECORD_STATUSES = {"processing", "processed", "needs_review", "ignored", "failed"}
_TELEMETRY_SOURCE_KINDS = {"attachment", "email_body"}
_TELEMETRY_OUTCOMES = {"success", "failure", "skipped"}
_TELEMETRY_PERSISTENCE = {"created", "updated", "not_persisted", "failed", "ignored"}
_TELEMETRY_ERROR_CODES = {
    "", "source_unavailable", "provider_unavailable", "invalid_provider_response",
    "unsupported_type", "unknown_status", "validation_failed", "persistence_failed",
    "no_unique_match", "not_travel_email", "no_source",
}


def _bounded_telemetry_text(value):
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value or ""))[:MAX_TELEMETRY_STRING]


def _telemetry_choice(value, allowed, fallback):
    value = str(value or "").strip().lower()
    return value if value in allowed else fallback


def _telemetry_nonnegative_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _telemetry_source_filename(value, source_kind):
    suffix = Path(str(value or "")).suffix.lower()
    allowed_suffixes = {".pdf", ".html", ".htm", ".txt", ".jpg", ".jpeg", ".png", ".webp"}
    if suffix not in allowed_suffixes:
        suffix = ".bin"
    prefix = "attachment" if source_kind == "attachment" else "email-body"
    return f"{prefix}{suffix}"


def record_extraction_attempt(intake_id, *, source_filename="", source_kind="", mime_type="", byte_size=0,
                              extracted_text_length=0, provider="gemini", model=None, outcome="failure",
                              status="needs_review", retry_count=0, normalized_type="other",
                              normalized_status="unknown", validation_error_code="",
                              validation_error_field="", persistence_outcome="not_persisted"):
    """Persist only bounded, allow-listed extraction telemetry; never provider payloads."""
    source_kind = _telemetry_choice(source_kind, _TELEMETRY_SOURCE_KINDS, "email_body")
    telemetry = {
        "at": _utc_now().isoformat(),
        "source_filename": _telemetry_source_filename(source_filename, source_kind),
        "source_kind": source_kind,
        "mime_type": _bounded_telemetry_text(mime_type).lower() or "application/octet-stream",
        "byte_size": _telemetry_nonnegative_int(byte_size),
        "extracted_text_length": _telemetry_nonnegative_int(extracted_text_length),
        "provider": _bounded_telemetry_text(provider).lower() or "unknown",
        "model": _bounded_telemetry_text(model or GEMINI_MODEL),
        "outcome": _telemetry_choice(outcome, _TELEMETRY_OUTCOMES, "failure"),
        "status": _telemetry_choice(status, _TELEMETRY_RECORD_STATUSES, "needs_review"),
        "retry_count": _telemetry_nonnegative_int(retry_count),
        "normalized_type": _telemetry_choice(normalized_type, _TELEMETRY_TYPES, "other"),
        "normalized_status": _telemetry_choice(normalized_status, _TELEMETRY_STATUSES, "unknown"),
        "validation_error_code": _telemetry_choice(validation_error_code, _TELEMETRY_ERROR_CODES, "validation_failed") if validation_error_code else "",
        "validation_error_field": _bounded_telemetry_text(validation_error_field).lower(),
        "persistence_outcome": _telemetry_choice(persistence_outcome, _TELEMETRY_PERSISTENCE, "failed"),
    }
    with LOCK:
        items = load_email_index()
        for record in items:
            if record.get("id") != intake_id:
                continue
            history = list(record.get("extraction_telemetry") or [])
            history.append(telemetry)
            record["extraction_telemetry"] = history[-MAX_EXTRACTION_ATTEMPTS:]
            record["updated_at"] = _utc_now().isoformat()
            save_email_index(items)
            return dict(record)
    return None


def claim_email_for_processing(intake_id, now=None, lease_seconds=None):
    """Atomically acquire a processing lease, recovering only stale workers."""
    now = now or _utc_now()
    lease_seconds = EMAIL_PROCESSING_LEASE_SECONDS if lease_seconds is None else lease_seconds
    with LOCK:
        items = load_email_index()
        for record in items:
            if record.get("id") != intake_id:
                continue
            status = record.get("status")
            started = _parse_timestamp(record.get("processing_started_at"))
            stale = status == "processing" and started and (now - started).total_seconds() >= lease_seconds
            if status not in {"received", "processing"} or (status == "processing" and not stale):
                return None
            record.update({
                "status": "processing",
                "processing_started_at": now.isoformat(),
                "processing_attempts": int(record.get("processing_attempts") or 0) + 1,
            })
            save_email_index(items)
            return dict(record)
    return None


def update_email_stage(intake_id, stage, error=None):
    stage = str(stage or "unknown")[:MAX_PROCESSING_STAGE]
    with LOCK:
        items = load_email_index()
        for record in items:
            if record.get("id") != intake_id:
                continue
            now = _utc_now().isoformat()
            history = list(record.get("processing_stage_history") or [])
            history.append({"stage": stage, "at": now})
            record["processing_stage_history"] = history[-MAX_STAGE_HISTORY:]
            record["processing_stage"] = stage
            record["processing_stage_updated_at"] = now
            if error is not None:
                record["processing_error"] = _redacted_error(error)
            save_email_index(items)
            return dict(record)
    return None


def update_email_record(intake_id, **changes):
    if "review_reason" in changes:
        changes["review_reason"] = _redacted_error(changes["review_reason"])
    if "processing_error" in changes:
        changes["processing_error"] = _redacted_error(changes["processing_error"])
    with LOCK:
        items = load_email_index()
        for record in items:
            if record.get("id") == intake_id:
                record.update(changes)
                save_email_index(items)
                return record
    return None


def ingested_type_matches_booking(expected_type, trip):
    expected = str(expected_type or "").strip().casefold()
    trip = trip or {}
    actual = str(trip.get("_ingested_type") or "").strip().casefold()
    if not actual and trip.get("hotels"):
        actual = "hotel"
    return bool(expected and actual and expected == actual)


def apply_review_decision(intake_id, decision, overrides=None):
    """Apply an explicit human review without deleting the source booking."""
    overrides = overrides if isinstance(overrides, dict) else {}
    allowed = {key: str(overrides.get(key) or "").strip()[:160] for key in ("type", "date", "time", "from", "to", "supplier") if str(overrides.get(key) or "").strip()}
    with LOCK:
        items = load_email_index()
        record = next((item for item in items if str(item.get("id")) == str(intake_id)), None)
        if not record:
            return None
        record["review_decision"] = "approved" if decision == "approve" else "saved"
        record["review_overrides"] = allowed
        if decision == "approve":
            record["status"] = "processed"
            record["review_reason"] = ""
            record["processed_at"] = _utc_now().isoformat()
        save_email_index(items)
    if decision != "approve":
        return record
    trips = load_trips()
    trip = next((item for item in trips if str(item.get("id")) == str(record.get("trip_id"))), None)
    kind = allowed.get("type") or str(record.get("ingested_type") or "").strip().lower()
    collection = {"flight": "flights", "hotel": "hotels", "train": "trains", "attraction": "attractions", "car_rental": "rentals", "restaurant": "restaurants"}.get(kind)
    applied = False
    if trip and collection:
        candidates = trip.get(collection, []) or []
        exact = [item for item in candidates if str(item.get("source_email_id")) == str(intake_id) or item.get("document") == record.get("document")]
        if len(exact) == 1:
            target = exact[0]
        else:
            supplier = str(allowed.get("supplier") or record.get("provider") or "").strip().casefold()
            date_key = {"flight": "departure_date", "hotel": "start", "train": "date", "attraction": "date", "car_rental": "pickup_date", "restaurant": "date"}.get(kind)
            fallback = [item for item in candidates if supplier and str(item.get("supplier") or item.get("airline") or "").strip().casefold() == supplier]
            if allowed.get("date") and date_key:
                fallback = [item for item in fallback if str(item.get(date_key) or "") == allowed["date"]]
            target = fallback[0] if len(fallback) == 1 else None
        if target:
            replacement = dict(target)
            if allowed.get("date"):
                replacement[{"flight": "departure_date", "hotel": "start", "train": "date", "attraction": "date", "car_rental": "pickup_date", "restaurant": "date"}[kind]] = allowed["date"]
            if allowed.get("time"):
                replacement[{"flight": "departure_time", "hotel": "checkin_time", "train": "time", "attraction": "time", "car_rental": "pickup_time", "restaurant": "time"}[kind]] = allowed["time"]
            if kind == "flight":
                if allowed.get("from"): replacement["origin"] = allowed["from"]
                if allowed.get("to"): replacement["destination"] = allowed["to"]
                if allowed.get("supplier"): replacement["airline"] = allowed["supplier"]
            elif allowed.get("supplier"):
                replacement["supplier"] = allowed["supplier"]
            applied = replace_booking(trips, {"trip": trip, "record": target}, kind, replacement, str(intake_id))
            if applied:
                save_trips(trips)
    if applied:
        record = update_email_record(intake_id, status="processed", review_decision="approved", review_reason="", processed_at=_utc_now().isoformat()) or record
    else:
        record = update_email_record(intake_id, status="needs_review", review_decision="saved", review_reason="אישור לא בוצע: אין התאמה חד-משמעית.") or record
    return record


def email_record_folder(record):
    return EMAIL_DIR / str(record.get("id"))


def email_attachment_candidates(record):
    supported = {"application/pdf", "image/jpeg", "image/png", "image/webp", "text/html"}
    folder = email_record_folder(record)
    candidates = []
    for index, attachment in enumerate(record.get("attachments", [])):
        suffix = Path(str(attachment.get("filename") or "")).suffix.lower()
        if attachment.get("content_type") not in supported and suffix not in {".pdf", ".html", ".htm", ".jpg", ".jpeg", ".png", ".webp"}:
            continue
        source = folder / str(attachment.get("path") or "")
        if not source.is_file():
            continue
        name = str(attachment.get("filename") or "").casefold()
        score = {".pdf": 5, ".html": 4, ".htm": 4, ".jpg": 2, ".jpeg": 2, ".png": 1, ".webp": 1}.get(suffix, 0)
        score += sum(3 for marker in ("confirmation", "voucher", "itinerary", "booking", "reservation", "ticket", "receipt", "invoice", "policy", "rental") if marker in name)
        score -= sum(5 for marker in ("logo", "signature", "tracking", "pixel", "footer") if marker in name)
        candidates.append((score, index, source, attachment))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [(source, attachment) for _, _, source, attachment in candidates]


class _EmailHTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "head", "svg"}:
            self.skip_depth += 1
        elif self.skip_depth == 0 and tag.lower() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "head", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        elif self.skip_depth == 0 and tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.parts.append(data)


def decode_html_bytes(value):
    """Decode HTML attachments using their declared charset, with safe fallbacks."""
    raw = bytes(value or b"")
    head = raw[:8192].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*[\"']?\s*([A-Za-z0-9._:-]+)", head, re.I)
    declared = (match.group(1).lower() if match else "")
    aliases = {"windows-1255": "cp1255", "iso-8859-8": "cp1255", "hebrew": "cp1255"}
    candidates = [aliases.get(declared, declared), "utf-8", "cp1255", "latin-1"]
    seen = set()
    for encoding in candidates:
        if not encoding or encoding in seen:
            continue
        seen.add(encoding)
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("latin-1", errors="replace")


def html_to_text(value):
    parser = _EmailHTMLText()
    try:
        parser.feed(str(value or ""))
        parser.close()
    except (TypeError, ValueError):
        return ""
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", "".join(parser.parts))).strip()


def normalize_date_value(value):
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        try:
            return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return ""
    numeric = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", text)
    if numeric:
        first, second, year = map(int, numeric.groups())
        if first > 12 and second <= 12:
            fmt = "%d/%m/%Y"
        elif second > 12 and first <= 12:
            fmt = "%m/%d/%Y"
        else:
            return ""
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            return ""
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%d%b%Y", "%d%B%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


HEBREW_MONTHS = {
    "ינואר": "January", "פברואר": "February", "מרץ": "March", "אפריל": "April",
    "מאי": "May", "יוני": "June", "יולי": "July", "אוגוסט": "August",
    "ספטמבר": "September", "אוקטובר": "October", "נובמבר": "November", "דצמבר": "December",
}


def _unique_values(values, normalizer=lambda value: value):
    found = {}
    for value in values:
        value = str(value or "").strip()
        if value:
            found.setdefault(normalizer(value), value)
    return list(found.values())


def _label_values(source, labels):
    pattern = r"(?im)^\s*(?:" + "|".join(labels) + r")\s*[:：-]\s*(.+?)\s*$"
    return _unique_values((match.group(1) for match in re.finditer(pattern, source)))


def _normalize_flight_number(value):
    value = re.sub(r"\s+", " ", str(value or "").strip().upper())
    compact = re.fullmatch(r"W6\s?(\d{4})", value)
    if compact:
        return f"W6 {compact.group(1)}"
    return value


def _explicit_date_tokens(source):
    tokens = re.findall(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/20\d{2}|\d{1,2}(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)20\d{2}|(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+\d{1,2},?\s+20\d{2}|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+20\d{2})\b", source, re.I)
    for hebrew, english in HEBREW_MONTHS.items():
        tokens.extend(re.findall(rf"\b\d{{1,2}}\s+{hebrew}\s+20\d{{2}}\b", source))
    return tokens


def _normalize_explicit_date(value):
    value = re.sub(r"\s+", " ", str(value or "").strip())
    for hebrew, english in HEBREW_MONTHS.items():
        value = re.sub(rf"(\d{{1,2}})\s+ב?{hebrew}\s+(20\d{{2}})", rf"\1 {english} \2", value)
    return normalize_date_value(value)


def normalize_flight_fields(ai, text=""):
    """Normalize one flight without selecting between conflicting explicit facts."""
    result = dict(ai or {})
    raw_source = str(text or "").replace("\u00a0", " ")
    raw_lines = [line.strip() for line in raw_source.splitlines() if line.strip()]
    lines = [line.strip() for line in raw_lines if line.strip()]
    deduped_lines = list(dict.fromkeys(re.sub(r"\s+", " ", line) for line in lines))
    source = "\n".join(deduped_lines)
    lower = source.casefold()

    # Arkia's legacy HTML ticket uses a flattened table, a two-digit year,
    # and HHMM times. Parse that exact row before generic fallbacks lose context.
    arkia_row = re.search(
        r"(?im)^([A-Z][A-Z0-9 ()-]{2,40})\n(?:Terminal:\s*\d+\n)?"
        r"([A-Z][A-Z0-9 ()-]{2,40})\n(IZ\s?\d{1,4})\n[A-Z]\n"
        r"(\d{1,2}\s+[A-Z]{3}\s+\d{2})\n(\d{4})\nOK(?:\n|$)",
        source,
    )
    if "arkia" in lower and arkia_row:
        arkia_origin, arkia_destination, arkia_number, arkia_date, arkia_time = arkia_row.groups()
        result.setdefault("airline", "Arkia")
        result.setdefault("flight_number", _normalize_flight_number(arkia_number))
        result.setdefault("origin", arkia_origin.strip())
        result.setdefault("destination", arkia_destination.strip())
        try:
            result.setdefault("departure_date", datetime.strptime(arkia_date, "%d %b %y").date().isoformat())
        except ValueError:
            pass
        result.setdefault("departure_time", f"{arkia_time[:2]}:{arkia_time[2:]}")

    def set_if_missing(key, values, normalizer=lambda value: value):
        if str(result.get(key) or "").strip():
            return
        unique = _unique_values(values, normalizer)
        if len(unique) == 1:
            result[key] = normalizer(unique[0])

    set_if_missing("airline", _label_values(source, ("airline", "carrier", "חברת תעופה")))
    if not str(result.get("airline") or "").strip():
        if "wizz air" in lower or re.search(r"\bwizz\b", lower):
            result["airline"] = "Wizz Air"
        elif "arkia" in lower:
            result["airline"] = "Arkia"
        elif "sundor" in lower or "sun d'or" in lower or "el al group" in lower:
            result["airline"] = "Sundor"

    set_if_missing("flight_number", _label_values(source, ("flight number", "flight no", "מספר טיסה")), _normalize_flight_number)
    if not str(result.get("flight_number") or "").strip():
        codes = re.findall(r"(?<![A-Z0-9])W6\s?\d{4}(?!\d)|(?<![A-Z0-9])[A-Z]{2,3}\s?\d{1,4}(?!\d)", source, re.I)
        codes = _unique_values(codes, _normalize_flight_number)
        if len(codes) == 1:
            result["flight_number"] = codes[0]

    origin_values = _label_values(source, ("from", "origin", "departure airport", "departs from", "מוצא"))
    destination_values = _label_values(source, ("to", "destination", "arrival airport", "arrives to", "יעד"))
    set_if_missing("origin", origin_values)
    set_if_missing("destination", destination_values)
    if len(origin_values) > 1 or len(destination_values) > 1:
        result["origin"] = ""
        result["destination"] = ""
    route_source = "\n".join(line for line in raw_lines if not all(marker in line.casefold() for marker in ("from", "to", "flight", "departure", "arrival")))
    route_pairs = re.findall(r"(?im)^\s*([A-Za-z][A-Za-z .'-]{1,30}?)\s+(?:to|→)\s+([A-Za-z][A-Za-z .'-]{1,30}?)\s*$", route_source)
    route_pairs = _unique_values((f"{a.strip()} → {b.strip()}" for a, b in route_pairs), str.casefold)
    if len(route_pairs) == 1 and (not result.get("origin") or not result.get("destination")):
        origin, destination = route_pairs[0].split(" → ", 1)
        result.setdefault("origin", origin)
        result.setdefault("destination", destination)
    elif len(route_pairs) > 1:
        result["origin"] = ""
        result["destination"] = ""

    # Wizz itinerary layout: two headers followed by two airport blocks, then one schedule line.
    for index, line in enumerate(raw_lines[:-1]):
        if line.casefold().rstrip(":") != "departs from" or raw_lines[index + 1].casefold().rstrip(":") != "arrives to":
            continue
        blocks = []
        current = []
        for candidate in raw_lines[index + 2:index + 12]:
            if re.search(r"\b\d{1,2}/\d{1,2}/20\d{2}\b", candidate):
                break
            if candidate.casefold() in {"you are protected", "flight details"}:
                continue
            current.append(candidate)
            if re.search(r"\([A-Z]{3}\)", candidate, re.I):
                blocks.append(" ".join(current).strip())
                current = []
            if len(blocks) == 2:
                break
        if len(blocks) == 2:
            result["origin"] = result.get("origin") or blocks[0]
            result["destination"] = result.get("destination") or blocks[1]
        break
    def normalize_schedule_date(value):
        if "wizz air" in lower and re.fullmatch(r"\d{1,2}/\d{1,2}/20\d{2}", str(value).strip()):
            try:
                return datetime.strptime(str(value).strip(), "%d/%m/%Y").date().isoformat()
            except ValueError:
                return ""
        return _normalize_explicit_date(value)

    schedule = re.search(
        r"(\d{1,2}/\d{1,2}/20\d{2})\s+(\d{1,2}:\d{2})\s*/?\s*(\d{1,2}/\d{1,2}/20\d{2})\s+(\d{1,2}:\d{2})",
        source,
    )
    if schedule:
        departure_date, departure_time, arrival_date, arrival_time = schedule.groups()
        result["departure_date"] = normalize_schedule_date(departure_date)
        result["arrival_date"] = normalize_schedule_date(arrival_date)
        result["departure_time"] = departure_time.zfill(5)
        result["arrival_time"] = arrival_time.zfill(5)

    table_header = any(all(marker in line.casefold() for marker in ("from", "to", "flight", "departure", "arrival")) for line in raw_lines)
    if table_header:
        for line in raw_lines:
            columns = [part.strip() for part in re.split(r"\s{2,}", line) if part.strip()]
            if len(columns) < 5:
                continue
            code_match = re.fullmatch(r"[A-Z]{2,3}\s?\d{1,4}", columns[2], re.I)
            if not code_match or not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", columns[3]) or not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", columns[4]):
                continue
            result["origin"] = columns[0]
            result["destination"] = columns[1]
            result["flight_number"] = _normalize_flight_number(columns[2])
            result["departure_time"] = columns[3]
            result["arrival_time"] = columns[4]
            break

    flight_code = str(result.get("flight_number") or "").strip()
    compact_code = ""
    if flight_code:
        compact_code = re.escape(flight_code).replace(r"\ ", r"\s*")
        for line in raw_lines:
            times_in_line = re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", line)
            code_match = re.search(rf"(?<![A-Z0-9]){compact_code}(?!\d)", line, re.I)
            if not code_match or len(times_in_line) < 2:
                continue
            if not result.get("departure_time"):
                result["departure_time"] = times_in_line[-2].zfill(5)
            if not result.get("arrival_time"):
                result["arrival_time"] = times_in_line[-1].zfill(5)
            prefix = line[:code_match.start()].strip()
            origin = str(result.get("origin") or "").strip()
            if origin and not result.get("destination") and prefix.casefold().startswith(origin.casefold() + " "):
                result["destination"] = prefix[len(origin):].strip()
            break

    if not result.get("origin"):
        for index, line in enumerate(raw_lines[:-1]):
            if line.casefold() == "departure" and raw_lines[index + 1].casefold() not in {"arrival", "from", "to"}:
                result["origin"] = raw_lines[index + 1]
                break

    if result.get("origin") and not result.get("destination") and flight_code:
        for line in raw_lines:
            code_match = re.search(rf"(?<![A-Z0-9]){compact_code}(?!\\d)", line, re.I)
            if not code_match:
                continue
            prefix = line[:code_match.start()].strip()
            if not prefix:
                continue
            origin = str(result["origin"]).strip()
            if prefix.casefold().startswith(origin.casefold() + " "):
                result["destination"] = prefix[len(origin):].strip()
            break

    set_if_missing("departure_date", _label_values(source, ("departure date", "date", "תאריך יציאה")), _normalize_explicit_date)
    if not normalize_date_value(result.get("departure_date")):
        dates = [_normalize_explicit_date(value) for value in _explicit_date_tokens(source)]
        if len(set(filter(None, dates))) == 1:
            result["departure_date"] = next(iter(set(filter(None, dates))))
    else:
        result["departure_date"] = normalize_date_value(result["departure_date"])
    set_if_missing("arrival_date", _label_values(source, ("arrival date", "תאריך נחיתה")), _normalize_explicit_date)
    if result.get("arrival_date"):
        result["arrival_date"] = normalize_date_value(result["arrival_date"]) or result["arrival_date"]

    times = re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", source)
    set_if_missing("departure_time", _label_values(source, ("departure time", "departure", "שעת יציאה")), lambda value: value.zfill(5))
    set_if_missing("arrival_time", _label_values(source, ("arrival time", "arrival", "שעת נחיתה", "הגעה")), lambda value: value.zfill(5))
    if not result.get("departure_time") and times:
        result["departure_time"] = times[0].zfill(5)
    if not result.get("arrival_time") and len(times) == 2:
        result["arrival_time"] = times[1].zfill(5)
    compact_schedule = re.search(
        r"(\d{1,2}/\d{1,2}/20\d{2})\s+(\d{1,2}:\d{2})(\d{1,2}/\d{1,2}/20\d{2})\s+(\d{1,2}:\d{2})",
        source,
    )
    if compact_schedule:
        departure_date, departure_time, arrival_date, arrival_time = compact_schedule.groups()
        result["departure_date"] = normalize_schedule_date(departure_date)
        result["arrival_date"] = normalize_schedule_date(arrival_date)
        result["departure_time"] = departure_time.zfill(5)
        result["arrival_time"] = arrival_time.zfill(5)
    for line in raw_lines:
        numeric_dates = re.findall(r"\d{1,2}/\d{1,2}/20\d{2}", line)
        line_times = re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", line)
        if len(numeric_dates) >= 2 and len(line_times) >= 2:
            result["departure_date"] = normalize_schedule_date(numeric_dates[0])
            result["arrival_date"] = normalize_schedule_date(numeric_dates[1])
            result["departure_time"] = line_times[0].zfill(5)
            result["arrival_time"] = line_times[1].zfill(5)
            break
    return result


def normalize_car_rental_fields(ai, text=""):
    """Normalize explicit rental facts from model output and source labels."""
    result = dict(ai or {})
    source = str(text or "").replace("\u00a0", " ")

    def set_field(key, labels, normalizer=lambda value: value.strip()):
        if str(result.get(key) or "").strip():
            return
        values = _label_values(source, labels)
        if len(values) == 1:
            result[key] = normalizer(values[0])

    set_field("pickup_date", ("pickup date", "pick-up date", "collection date", "תאריך איסוף"), lambda value: normalize_date_value(value) or value.strip())
    set_field("dropoff_date", ("dropoff date", "drop-off date", "return date", "תאריך החזרה"), lambda value: normalize_date_value(value) or value.strip())
    set_field("pickup_time", ("pickup time", "pick-up time", "collection time", "שעת איסוף"), lambda value: value.strip().zfill(5))
    set_field("dropoff_time", ("dropoff time", "drop-off time", "return time", "שעת החזרה"), lambda value: value.strip().zfill(5))
    set_field("pickup_location", ("pickup location", "pick-up location", "collection location", "איסוף רכב"))
    set_field("dropoff_location", ("dropoff location", "drop-off location", "return location", "החזרת רכב"))
    set_field("vehicle_type", ("vehicle type", "car type", "rental car", "סוג רכב"))

    dates = [_normalize_explicit_date(value) for value in _explicit_date_tokens(source)]
    dates = [value for value in dates if value]
    if not result.get("pickup_date") and dates:
        result["pickup_date"] = dates[0]
    if not result.get("dropoff_date") and len(dates) >= 2:
        result["dropoff_date"] = dates[1]
    times = re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", source)
    if not result.get("pickup_time") and times:
        result["pickup_time"] = times[0].zfill(5)
    if not result.get("dropoff_time") and len(times) >= 2:
        result["dropoff_time"] = times[1].zfill(5)
    return result


def normalize_flight_segments(ai, text=""):
    """Return explicit flight segments separately; never collapse conflicting segments."""
    supplied = (ai or {}).get("segments") if isinstance(ai, dict) else None
    if isinstance(supplied, list) and supplied:
        normalized = []
        for segment in supplied:
            if not isinstance(segment, dict):
                continue
            if len(supplied) == 1:
                inherited = {key: value for key, value in (ai or {}).items() if key != "segments"}
                inherited.update(segment)
                item = normalize_flight_fields(inherited, text)
            else:
                item = normalize_flight_fields(segment, "")
            if item.get("flight_number"):
                item["flight_number"] = _normalize_flight_number(item["flight_number"])
            if item.get("departure_date"):
                item["departure_date"] = normalize_date_value(item["departure_date"]) or item["departure_date"]
            if item.get("arrival_date"):
                item["arrival_date"] = normalize_date_value(item["arrival_date"]) or item["arrival_date"]
            for key in ("departure_time", "arrival_time"):
                if item.get(key):
                    item[key] = str(item[key]).zfill(5)
            normalized.append(item)
        return normalized
    blocks = re.split(r"(?im)(?=^\s*(?:flight|segment|טיסה)\s*(?:#|מספר)?\s*\d+\b)", str(text or ""))
    blocks = [block.strip() for block in blocks if block.strip()]
    if len(blocks) > 1:
        return [normalize_flight_fields({}, block) for block in blocks]
    return [normalize_flight_fields(ai or {}, text)]


def combine_email_body_parts(text, html=""):
    plain = str(text or "").strip()
    rendered = html_to_text(str(html or "")).strip() if str(html or "").strip() else ""
    if plain and rendered and rendered != plain:
        return f"{plain}\n\n--- HTML body ---\n{rendered}"
    return plain or rendered


def email_body_text(record):
    folder = email_record_folder(record)
    text_path = folder / "body.txt"
    text = text_path.read_text(encoding="utf-8", errors="replace") if text_path.is_file() else ""
    html_path = folder / "body.html"
    html = html_path.read_text(encoding="utf-8", errors="replace") if html_path.is_file() else ""
    return combine_email_body_parts(text, html)


def prepare_email_source(record):
    candidates = email_attachment_candidates(record)
    if candidates:
        source, attachment = candidates[0]
        original_name = safe_email_filename(attachment.get("filename"), "email-attachment.bin")
        if Path(original_name).suffix.lower() in {".html", ".htm"}:
            filename = f"email-{record.get('id', 'message')}-{Path(original_name).stem}.txt"
            target = UPLOADS / f"email_{record.get('id', 'message')}_{filename}"
            target.parent.mkdir(parents=True, exist_ok=True)
            html = source.read_text(encoding="utf-8", errors="replace")
            target.write_text(html_to_text(html), encoding="utf-8")
            return target.name, target
        filename = original_name
    else:
        source = email_record_folder(record) / "body.txt"
        filename = f"email-{record.get('id', 'message')}.txt"
        body = email_body_text(record)
        if not body:
            return None
        if not source.is_file() or source.read_text(encoding="utf-8", errors="replace") != body:
            source.write_text(body, encoding="utf-8")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    stored = f"email_{record.get('id', 'message')}_{filename}"
    target = UPLOADS / stored
    if not target.exists():
        shutil.copyfile(source, target)
    return stored, target


def email_attachment_text_context(record, max_chars=30000, exclude_path=None):
    """Collect bounded text from readable non-primary attachments for cross-file extraction."""
    parts = []
    remaining = max(0, int(max_chars))
    excluded = Path(exclude_path).resolve() if exclude_path else None
    for source, attachment in email_attachment_candidates(record):
        if remaining <= 0:
            break
        if excluded and source.resolve() == excluded:
            continue
        suffix = Path(str(attachment.get("filename") or source.name)).suffix.lower()
        try:
            if suffix in {".html", ".htm"} or attachment.get("content_type") == "text/html":
                text = html_to_text(decode_html_bytes(source.read_bytes()))
            elif suffix in {".pdf", ".txt"} or attachment.get("content_type") == "application/pdf":
                text = extract_pdf_text(source)
            else:
                text = ""
        except (OSError, UnicodeError):
            text = ""
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if text:
            chunk = text[:remaining]
            parts.append(chunk)
            remaining -= len(chunk)
    return "\n".join(parts)
def email_notification(intent, record, trip=None, review=False):
    subject = str(record.get("subject") or "מייל נסיעות").strip()
    if review:
        return f"TRIPY: מייל נסיעות ממתין לבדיקה\n{subject}"
    action = {"confirmed": "נוספה הזמנה", "modified": "התקבל שינוי", "cancelled": "התקבל ביטול"}.get(intent, "התקבל מייל")
    return f"TRIPY: {action}\n{subject}"


def intent_from_ai(ai, subject="", body=""):
    status = str((ai or {}).get("status") or "").strip().lower()
    # An explicit refund remains distinct from a cancellation.
    if status == "refunded" and re.search(r"\b(?:refund(?:ed)?|refund issued|refund processed)\b", " ".join((str(subject or ""), str(body or ""))).casefold()):
        return "refunded"
    # Explicit lifecycle language in the source outranks a contradictory model status.
    # Generic policy/footer language is filtered by classify_email_intent.
    explicit_intent = classify_email_intent(subject, body)
    if explicit_intent == "confirmed" and (
        _strong_confirmation_subject(subject)
        or re.search(r"\b(?:reservation|booking)\s+(?:is\s+)?confirmed\b|\bconfirmed\s+(?:reservation|booking)\b", str(body or "").casefold())
    ):
        return "confirmed"
    if explicit_intent in {"cancelled", "modified"}:
        return explicit_intent
    status_map = {"confirmed": "confirmed", "modified": "modified", "cancelled": "cancelled", "refunded": "refunded"}
    if status in status_map:
        return status_map[status]

    # Gemini may extract the booking facts but omit lifecycle status. Recover
    # only from explicit message language; generic policy/footer words remain
    # review-only rather than becoming a guessed confirmation.
    text = " ".join((str(subject or ""), str(body or ""))).casefold()
    if re.search(r"\b(?:cancelled|canceled|voided|refund(?:ed)?|rescheduled|rebooked|reservation (?:was )?changed|booking (?:was )?changed)\b", text):
        return classify_email_intent(subject, body)
    if re.search(r"\b(?:reservation|booking)\s+(?:is\s+)?confirmed\b|\bconfirmed\s+(?:reservation|booking)\b|\bconfirmation\s+(?:number|code)\b", text):
        return "confirmed"
    return "unknown"


def lifecycle_facts(ai):
    facts = dict(ai or {})
    facts["start"] = facts.get("start") or facts.get("check_in") or facts.get("departure_date") or facts.get("date") or facts.get("pickup_date")
    facts["end"] = facts.get("end") or facts.get("check_out") or facts.get("arrival_date") or facts.get("date") or facts.get("dropoff_date") or facts.get("start")
    facts["name"] = facts.get("name") or facts.get("hotel") or facts.get("attraction") or facts.get("vehicle_type") or facts.get("flight_number") or facts.get("train_number")
    return facts


def preserve_booking_metadata(trip, source, status="confirmed", source_email_id=None):
    for collection in ("hotels", "flights", "trains", "attractions", "rentals", "restaurants"):
        for booking in trip.get(collection, []) or []:
            if "lifecycle_history" not in booking:
                enrich_booking_lifecycle(booking, source, status=status, source_email_id=source_email_id)
            else:
                booking.setdefault("source", source)
                booking.setdefault("status", status)
                booking.setdefault("lifecycle_status", booking["status"])
                if source_email_id:
                    booking["source_email_id"] = source_email_id
    insurance = trip.get("insurance")
    if isinstance(insurance, dict):
        if "lifecycle_history" not in insurance:
            enrich_booking_lifecycle(insurance, source, status=status, source_email_id=source_email_id)
        else:
            insurance.setdefault("source", source)
            insurance.setdefault("status", status)
            insurance.setdefault("lifecycle_status", insurance["status"])
    return trip


def decorate_trip_booking(trip, ai, filename, intake_id):
    kind = str(ai.get("type") or "").strip().lower()
    collection = {"hotel": "hotels", "flight": "flights", "train": "trains", "attraction": "attractions", "car_rental": "rentals", "restaurant": "restaurants"}.get(kind)
    if not collection:
        return False
    changed = False
    for booking in trip.get(collection, []) or []:
        if booking.get("document") != filename:
            continue
        for key in ("supplier", "confirmation_number", "traveler", "phone", "address"):
            value = str(ai.get(key) or "").strip()
            if value:
                booking[key] = value; changed = True
        booking["status"] = "confirmed"
        booking["source_email_id"] = intake_id
        booking.setdefault("lifecycle_history", []).append({"status": "confirmed", "email_id": intake_id, "at": datetime.now(timezone.utc).isoformat()})
        changed = True
        break
    return changed


def dated_trip_target(trips, date_value):
    date_value = str(date_value or "").strip()
    if not date_value:
        return None
    return next((trip for trip in trips if str(trip.get("start") or "") <= date_value <= str(trip.get("end") or "")), None)


CITY_DISPLAY_NAMES = {
    "milan": "מילאנו",
    "rome": "רומא",
    "paris": "פריז",
    "london": "לונדון",
    "munich": "מינכן",
    "budapest": "בודפשט",
    "batumi": "באטומי",
    "tbilisi": "טביליסי",
}
AIRPORT_CITY_NAMES = {
    "mxp": "מילאנו",
    "lin": "מילאנו",
    "fco": "רומא",
    "cia": "רומא",
    "cdg": "פריז",
    "lhr": "לונדון",
    "muc": "מינכן",
}


def city_display_name(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    lowered = text.casefold()
    for code, city in AIRPORT_CITY_NAMES.items():
        if re.search(rf"\b{re.escape(code)}\b", lowered):
            return city
    for city, display in CITY_DISPLAY_NAMES.items():
        if re.search(rf"\b{re.escape(city)}\b", lowered):
            return display
    return ""


CITY_COUNTRIES = {
    "מילאנו": "איטליה",
    "רומא": "איטליה",
    "פריז": "צרפת",
    "לונדון": "בריטניה",
    "מינכן": "גרמניה",
    "בודפשט": "הונגריה",
    "באטומי": "גיאורגיה",
    "טביליסי": "גיאורגיה",
}


def trip_title_from_location(value, fallback="טיול חדש"):
    city = city_display_name(value)
    return f"{CITY_COUNTRIES[city]} {city}" if city in CITY_COUNTRIES else fallback
def create_dated_trip_shell(trips, start, end, source, filename, title="טיול חדש"):
    start, end = validate_date_range(start, end)
    days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1
    trip = {"id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"), "title": title or "טיול חדש", "start": start, "end": end, "days": days, "source": source, "document": filename, "documents": [filename], "image": "https://images.unsplash.com/photo-1527668752968-14dc70a27c95?auto=format&fit=crop&w=1200&q=80", "images": ["https://images.unsplash.com/photo-1527668752968-14dc70a27c95?auto=format&fit=crop&w=1200&q=80"], "destinations": [], "hotels": [], "flights": [], "trains": [], "attractions": [], "rentals": [], "restaurants": [], "document_titles": {filename: "אישור הזמנה"}}
    trips.append(trip)
    return trip


def find_single_provider_booking(trips, facts):
    """Use provider + type only for an explicit cancellation when exactly one active booking matches."""
    type_to_collection = {"flight": "flights", "car_rental": "rentals", "hotel": "hotels", "train": "trains", "attraction": "attractions", "restaurant": "restaurants"}
    collection = type_to_collection.get(str(facts.get("type") or "").strip().lower())
    provider = str(facts.get("supplier") or "").strip().casefold().replace("_", " ")
    if not collection or not provider:
        return []
    matches = []
    for trip in trips:
        for record in trip.get(collection, []) or []:
            if str(record.get("status", "confirmed")).casefold() in {"cancelled", "refunded", "superseded"}:
                continue
            candidates = [str(record.get("supplier") or ""), str(record.get("airline") or "")]
            normalized = " ".join(candidates).casefold().replace("_", " ")
            if provider in normalized or (provider == "wizz air" and "wizz" in normalized) or (provider == "sixt" and "sixt" in normalized):
                matches.append({"trip_id": trip.get("id"), "trip": trip, "kind": next((kind for kind, items in {"flight":"flights","car_rental":"rentals","hotel":"hotels","train":"trains","attraction":"attractions","restaurant":"restaurants"}.items() if items == collection), collection), "record": record, "match": "provider_type_unique"})
    return matches


def apply_email_cancellation(ai, record, status="cancelled"):
    trips = load_trips()
    intake_id = record.get("id")
    for trip in trips:
        for collection in ("hotels", "flights", "trains", "attractions", "rentals", "restaurants"):
            for booking in trip.get(collection, []) or []:
                if booking.get("cancelled_by_email") == intake_id:
                    return True, "already_cancelled"
    matches = find_booking_matches(trips, lifecycle_facts(ai))
    if len(matches) != 1:
        provider_matches = find_single_provider_booking(trips, ai)
        matches = provider_matches if len(provider_matches) == 1 else []
    if len(matches) != 1:
        return False, "no_unique_match"
    match = matches[0]
    cancel_booking(trips, match, intake_id, status=status)
    save_trips(trips)
    return True, match.get("kind")


def find_email_trip(filename):
    for trip in load_trips():
        if trip.get("document") == filename or filename in (trip.get("documents") or []):
            return trip
        for collection in ("hotels", "flights", "trains", "attractions", "rentals", "restaurants"):
            if any(item.get("document") == filename for item in trip.get(collection, []) or []):
                return trip
        if (trip.get("insurance") or {}).get("document") == filename:
            return trip
    return None


def process_incoming_email(record):
    """Process one persisted MIME record under a crash-recoverable lease."""
    intake_id = record.get("id")
    claimed = claim_email_for_processing(intake_id)
    if not claimed:
        return None
    record = claimed
    source_filename = ""
    source_kind = "email_body"
    mime_type = "text/plain"
    byte_size = 0
    extracted_text_length = 0
    telemetry_outcome = "failure"
    telemetry_type = "other"
    telemetry_status = "unknown"
    validation_error_code = ""
    validation_error_field = ""
    persistence_outcome = "not_persisted"
    final_record_status = "processing"
    try:
        update_email_stage(intake_id, "reading")
        subject = str(record.get("subject") or "")
        body = email_body_text(record)
        attachment_names = " ".join(str(item.get("filename") or "") for item in record.get("attachments") or [])
        attachment_context_for_provider = email_attachment_text_context(record)
        provider_info = identify_email_provider(
            sender=str(record.get("from") or ""),
            reply_to=str(record.get("reply_to") or ""),
            subject=subject,
            body="\n".join((body, attachment_names, attachment_context_for_provider)),
        )
        update_email_record(
            intake_id,
            provider=provider_info["provider"],
            provider_category_hint=provider_info["category_hint"],
            provider_confidence=provider_info["confidence"],
            provider_evidence=provider_info["evidence"],
        )
        update_email_stage(intake_id, "preparing_source")
        prepared = prepare_email_source(record)
        if not prepared:
            source_filename = f"email-{intake_id}.txt"
            extracted_text_length = len(body)
            if not looks_like_travel_email(subject, body):
                final_record_status = "ignored"
                telemetry_outcome = "skipped"
                persistence_outcome = "ignored"
                validation_error_code = "not_travel_email"
                update_email_record(intake_id, status="ignored", intent="unknown", processed_at=_utc_now().isoformat())
                return record
            final_record_status = "needs_review"
            validation_error_code = "no_source"
            update_email_record(intake_id, status="needs_review", intent="unknown", review_reason="no_supported_attachment_or_text", processed_at=_utc_now().isoformat())
            if TOKEN and ALLOWED_CHAT:
                send(ALLOWED_CHAT, email_notification("unknown", record, review=True))
            return record
        filename, path = prepared
        source_filename = filename
        source_kind = "attachment" if email_attachment_candidates(record) else "email_body"
        attachment = next((item for candidate, item in email_attachment_candidates(record) if candidate.name == path.name), None)
        mime_type = (attachment or {}).get("content_type") or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        try:
            byte_size = path.stat().st_size
        except OSError:
            byte_size = 0
        extracted_text_length = len(extract_pdf_text(path) or "")
        update_email_stage(intake_id, "extracting")
        attachment_context = email_attachment_text_context(record, exclude_path=next(
            (candidate for candidate, item in email_attachment_candidates(record) if item.get("filename") == Path(filename).name),
            path,
        ))
        supplemental_context = "\n".join(part for part in (body[:30000], attachment_context) if part)
        ai = gemini_extract(path, mime_type=mime_type, supplemental_text=supplemental_context) or {}
        if ai:
            ai = enrich_ai_with_provider(ai, provider_info)
            ai = guard_marketplace_category(ai, provider_info, "\n".join((body, attachment_context_for_provider)))
            # Keep extraction evidence behind one stable seam before validation/storage.
            booking_contract = normalize_booking_facts(
                ai,
                subject=subject,
                body="\n".join((body, attachment_context_for_provider)),
                provider=provider_info,
            )
            ai = merge_normalized_booking_facts(ai, booking_contract)
        if not ai:
            final_record_status = "needs_review"
            validation_error_code = "provider_unavailable"
            update_email_record(intake_id, status="needs_review", intent="unknown", review_reason="ai_extraction_failed", processed_at=_utc_now().isoformat())
            if TOKEN and ALLOWED_CHAT:
                send(ALLOWED_CHAT, email_notification("unknown", record, review=True))
            return record
        provider_hint = str(provider_info.get("category_hint") or "").strip()
        ai_type = str(ai.get("type") or "").strip().lower()
        if provider_info.get("confidence", 0) >= 0.8 and provider_hint in _TELEMETRY_TYPES and provider_hint != "marketplace":
            # Direct provider classification outranks generic model type inference.
            ai["type"] = provider_hint
        intent = intent_from_ai(ai, subject, body)
        telemetry_type = _telemetry_choice(ai.get("type"), _TELEMETRY_TYPES, "other")
        telemetry_status = _telemetry_choice(ai.get("status"), _TELEMETRY_STATUSES, "unknown")
        update_email_stage(intake_id, "applying", error=None)
        if intent == "unknown":
            final_record_status = "needs_review"
            validation_error_code = "unknown_status"
            validation_error_field = "status"
            update_email_record(intake_id, status="needs_review", intent="unknown", review_reason="ai_status_missing", processed_at=_utc_now().isoformat())
            if TOKEN and ALLOWED_CHAT:
                send(ALLOWED_CHAT, email_notification("unknown", record, review=True))
            return record
        if intent in {"cancelled", "refunded"}:
            applied, reason = apply_email_cancellation(ai, record, status=intent)
            final_record_status = "processed" if applied else "needs_review"
            telemetry_outcome = "success" if applied else "failure"
            persistence_outcome = "updated" if applied else "not_persisted"
            if not applied:
                validation_error_code = "no_unique_match"
            update_email_record(intake_id, status=final_record_status, intent=intent, lifecycle_action=intent if applied else "review", review_reason="" if applied else reason, processed_at=datetime.now(timezone.utc).isoformat())
            if TOKEN and ALLOWED_CHAT:
                send(ALLOWED_CHAT, email_notification(intent, record, review=not applied))
            return record
        if intent == "modified":
            final_record_status = "needs_review"
            validation_error_code = "validation_failed"
            validation_error_field = "status"
            update_email_record(intake_id, status="needs_review", intent=intent, review_reason="replacement_requires_review", processed_at=_utc_now().isoformat())
            if TOKEN and ALLOWED_CHAT:
                send(ALLOWED_CHAT, email_notification(intent, record, review=True))
            return record
        try:
            persisted_trip = find_email_trip(filename)
            had_persisted_trip = bool(persisted_trip)
            context_text = "\n".join((
                f"Provider: {provider_info.get('provider', 'unknown')}",
                f"Provider category hint: {provider_info.get('category_hint', 'unknown')}",
                subject,
                str(record.get("from") or ""),
                str(record.get("to") or ""),
                body,
            ))
            trip = persisted_trip or create_trip_from_document(filename, path, "Email", ai_override=ai, context_text=context_text)
            expected_type = provider_hint if provider_info.get("confidence", 0) >= 0.8 and provider_hint in _TELEMETRY_TYPES and provider_hint != "marketplace" else str(ai.get("type") or "").strip().lower()
            if expected_type and not ingested_type_matches_booking(expected_type, trip):
                raise ValueError("provider/category contradiction requires review")
            persisted_trips = load_trips()
            persisted_trip = next((item for item in persisted_trips if str(item.get("id")) == str(trip.get("id"))), trip)
            decorate_trip_booking(persisted_trip, ai, filename, intake_id)
            save_trips(persisted_trips)
            trip = persisted_trip
            final_record_status = "processed"
            telemetry_outcome = "success"
            persistence_outcome = "updated" if had_persisted_trip else "created"
            update_email_record(intake_id, status="processed", intent=intent, processed_at=_utc_now().isoformat(), ingested_type=ai.get("type") or trip.get("_ingested_type") or "other", trip_id=trip.get("id"), document=filename, processing_error="")
            if TOKEN and ALLOWED_CHAT:
                send(ALLOWED_CHAT, email_notification(intent, record, trip=trip))
            return record
        except Exception as exc:
            final_record_status = "needs_review"
            validation_error_code = "validation_failed"
            validation_error_field = "type"
            persistence_outcome = "failed"
            update_email_record(intake_id, status="needs_review", intent=intent, review_reason=f"{type(exc).__name__}: {str(exc)[:160]}", processed_at=_utc_now().isoformat())
            update_email_stage(intake_id, "failed", error=exc)
            if TOKEN and ALLOWED_CHAT:
                send(ALLOWED_CHAT, email_notification(intent, record, review=True))
            return record
    except Exception as exc:
        final_record_status = "needs_review"
        validation_error_code = "persistence_failed"
        persistence_outcome = "failed"
        update_email_record(intake_id, status="needs_review", processed_at=_utc_now().isoformat())
        update_email_stage(intake_id, "failed", error=exc)
        return record
    finally:
        record_extraction_attempt(
            intake_id,
            source_filename=source_filename,
            source_kind=source_kind,
            mime_type=mime_type,
            byte_size=byte_size,
            extracted_text_length=extracted_text_length,
            provider="gemini",
            model=GEMINI_MODEL,
            outcome=telemetry_outcome,
            status=final_record_status,
            retry_count=record.get("processing_attempts", 0),
            normalized_type=telemetry_type,
            normalized_status=telemetry_status,
            validation_error_code=validation_error_code,
            validation_error_field=validation_error_field,
            persistence_outcome=persistence_outcome,
        )
        if final_record_status in {"processed", "needs_review", "failed"}:
            label = "TRIPY: הזמנה נקלטה" if final_record_status == "processed" else "TRIPY: נדרשת בדיקה"
            send_push_notification(label, str(record.get("subject") or "מייל נסיעות"), tag=f"tripy-{intake_id}")
def repair_trip_titles_from_bookings():
    trips = load_trips()
    changed = False
    for trip in trips:
        rentals = trip.get("rentals") or []
        if not rentals:
            continue
        location = str((rentals[0] or {}).get("pickup_location") or "")
        title = trip_title_from_location(location)
        if title != "טיול חדש" and str(trip.get("title") or "").startswith("טיול ·") and trip.get("title") != title:
            trip["title"] = title
            changed = True
    if changed:
        save_trips(trips)


SEMANTIC_RECOVERY_VERSION = "semantic-hotel-v1"


def resume_pending_email_processing():
    repair_trip_titles_from_bookings()
    retryable_errors = {
        "ValueError: flight document missing unambiguous flight facts",
        "ValueError: car rental missing pickup location, time, date, or vehicle type",
        "ValueError: document type not recognized; send a clearer screenshot or PDF",
    }
    for record in load_email_index():
        status = str(record.get("status") or "")
        reason = str(record.get("review_reason") or "")
        if status == "processed":
            expected_type = {"sixt": "car_rental", "wizz_air": "flight"}.get(str(record.get("provider") or "").casefold())
            changes = {"review_reason": ""} if record.get("review_reason") else {}
            if expected_type and record.get("ingested_type") != expected_type:
                changes["ingested_type"] = expected_type
            if changes:
                update_email_record(intake_id=record.get("id"), **changes)
        recent_lifecycle_retry = (
            status == "needs_review"
            and not record.get("lifecycle_recovery_attempted")
            and str(record.get("provider") or "").casefold() in {"wizz_air", "sixt"}
            and str(record.get("intent") or "").casefold() in {"modified", "cancelled"}
        )
        restaurant_recovery = (
            status == "needs_review"
            and reason == "ValueError: restaurant reservation missing name, date, or time"
            and not record.get("restaurant_recovery_attempted")
        )
        semantic_recovery = status == "needs_review" and record.get("semantic_recovery_version") != SEMANTIC_RECOVERY_VERSION
        retryable = status in {"received", "processing"} or (status == "needs_review" and reason in retryable_errors) or recent_lifecycle_retry or restaurant_recovery or semantic_recovery
        if retryable:
            if semantic_recovery:
                update_email_record(intake_id=record.get("id"), semantic_recovery_version=SEMANTIC_RECOVERY_VERSION)
            if recent_lifecycle_retry:
                update_email_record(intake_id=record.get("id"), lifecycle_recovery_attempted=True)
            if restaurant_recovery:
                update_email_record(intake_id=record.get("id"), restaurant_recovery_attempted=True)
            if status == "needs_review":
                update_email_record(intake_id=record.get("id"), status="received", previous_review_reason=reason, processing_error="")
            threading.Thread(target=process_incoming_email, args=(record,), daemon=True).start()

def extract_pdf_text(path):
    text = ""
    image_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    if path.suffix.lower() in {".html", ".htm"}:
        try: return html_to_text(decode_html_bytes(path.read_bytes()))
        except OSError: return ""
    if path.suffix.lower() in {".txt", ".text"}:
        try: return path.read_text(encoding="utf-8", errors="replace")
        except OSError: return ""
    try:
        if path.suffix.lower() in image_suffixes:
            result = subprocess.run(["tesseract", str(path), "stdout", "-l", "eng"], capture_output=True, text=True, timeout=45)
            return result.stdout if result.returncode == 0 else ""
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

def gemini_extract(path, mime_type=None, supplemental_text=""):
    if not GEMINI_API_KEY: return None
    schema = {"type":"object","properties":{"type":{"type":"string","enum":["hotel","flight","train","attraction","car_rental","restaurant","insurance","other"]},"hotel":{"type":"string"},"city":{"type":"string"},"country":{"type":"string"},"check_in":{"type":"string"},"check_out":{"type":"string"},"check_in_time":{"type":"string"},"check_out_time":{"type":"string"},"airline":{"type":"string"},"flight_number":{"type":"string"},"segments":{"type":"array","items":{"type":"object","properties":{"airline":{"type":"string"},"flight_number":{"type":"string"},"departure_date":{"type":"string"},"departure_time":{"type":"string"},"arrival_date":{"type":"string"},"arrival_time":{"type":"string"},"origin":{"type":"string"},"destination":{"type":"string"}}}},"departure_date":{"type":"string"},"departure_time":{"type":"string"},"arrival_date":{"type":"string"},"arrival_time":{"type":"string"},"origin":{"type":"string"},"destination":{"type":"string"},"train_number":{"type":"string"},"attraction":{"type":"string"},"restaurant_name":{"type":"string"},"date":{"type":"string"},"time":{"type":"string"},"location":{"type":"string"},"address":{"type":"string"},"phone":{"type":"string"},"pickup_date":{"type":"string"},"pickup_time":{"type":"string"},"pickup_location":{"type":"string"},"vehicle_type":{"type":"string"},"dropoff_date":{"type":"string"},"dropoff_time":{"type":"string"},"dropoff_location":{"type":"string"},"supplier":{"type":"string"},"confirmation_number":{"type":"string"},"traveler":{"type":"string"},"policy_number":{"type":"string"},"insurer":{"type":"string"},"insured_travelers":{"type":"array","items":{"type":"string"}},"valid_from":{"type":"string"},"valid_to":{"type":"string"},"coverage_summary":{"type":"string"},"covered_items":{"type":"array","items":{"type":"string"}},"exclusions":{"type":"array","items":{"type":"string"}},"what_to_do":{"type":"array","items":{"type":"string"}},"emergency_contacts":{"type":"array","items":{"type":"object","properties":{"label":{"type":"string"},"phone":{"type":"string"},"hours":{"type":"string"}}}},"source_language":{"type":"string"},"status":{"type":"string","enum":["confirmed","modified","cancelled","refunded","unknown"]}},"required":["type","status"]}
    prompt = "Classify this travel document, email or screenshot and extract only clearly present facts. Return JSON matching the schema. type must be hotel, flight, train, attraction, car_rental, restaurant, insurance, or other. For every date field, return ISO YYYY-MM-DD only; for every time field, return 24-hour HH:MM only; if the source does not state a fact unambiguously, return an empty string. status must describe the lifecycle of this booking message: confirmed for a new/active reservation, modified only when the reservation dates, route, room, passenger, or other booking facts were actually changed, cancelled only when the reservation itself was cancelled, and refunded only when a refund was actually issued. Do not infer modified or cancelled from generic footer text, a cancellation policy, refund policy, last-updated timestamps, account links, or instructions to update preferences. Never guess; use empty strings or empty arrays when a fact is absent. A flight requires airline, flight_number, origin, destination, departure_date, departure_time, and arrival_time when clearly present. A car rental requires pickup_date, pickup_time, pickup_location, and vehicle_type; preserve dropoff fields when clearly present. A restaurant reservation must include restaurant_name, date and time, and address/phone when clearly present. A booking confirmation for a museum, chocolate experience, tour, venue, or ticketed visit is an attraction, never a hotel. Insurance is one trip-level policy: preserve only source facts, translate concise coverage and emergency instructions into plain Hebrew when the source is not Hebrew, and keep the original document as the source of truth. Do not invent coverage, exclusions, phone numbers, or medical advice."
    mime_type = mime_type or {".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".png":"image/png", ".webp":"image/webp", ".txt":"text/plain", ".html":"text/html", ".htm":"text/html"}.get(path.suffix.lower(), "application/pdf")
    source_text = extract_pdf_text(path)
    supplemental_text = re.sub(r"\s+", " ", str(supplemental_text or "")).strip()
    if supplemental_text:
        source_text = (source_text + "\n" + supplemental_text).strip()
    parts = []
    parts.append({"text": prompt})
    if source_text.strip():
        parts.append({"text": "Bounded extracted source text:\n" + source_text[:30000]})
    parts.append({"inline_data":{"mime_type":mime_type,"data":base64.b64encode(path.read_bytes()).decode("ascii")}})
    payload = {"contents":[{"parts":parts}],"generationConfig":{"responseMimeType":"application/json","responseSchema":schema,"temperature":0}}
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


def gemini_extract_attraction(path):
    if not GEMINI_API_KEY: return None
    schema = {"type":"object","properties":{"attraction":{"type":"string"},"date":{"type":"string"},"time":{"type":"string"},"location":{"type":"string"}},"required":["attraction","date","time","location"]}
    prompt = "This is a travel attraction booking screenshot or document. Extract only facts clearly visible in the image. Return JSON with the attraction name, date in ISO YYYY-MM-DD, time in 24-hour HH:MM, and the complete visit or meeting location. This is not a hotel. For a Lindt Home of Chocolate booking, attraction is Lindt Home of Chocolate. Do not return empty fields and do not invent facts."
    payload = {"contents":[{"parts":[{"text":prompt},{"inline_data":{"mime_type":{".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".png":"image/png", ".webp":"image/webp"}.get(path.suffix.lower(), "application/pdf"),"data":base64.b64encode(path.read_bytes()).decode("ascii")}}]}],"generationConfig":{"responseMimeType":"application/json","responseSchema":schema,"temperature":0}}
    try:
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent", data=json.dumps(payload).encode(), headers={"Content-Type":"application/json","x-goog-api-key":GEMINI_API_KEY}, method="POST")
        with urllib.request.urlopen(req, timeout=90) as response: body = json.loads(response.read())
        result = json.loads(body["candidates"][0]["content"]["parts"][0]["text"])
        return result if isinstance(result, dict) else None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
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


def extract_attraction_facts(text, ai):
    raw = str(text or "")
    name = str(ai.get("attraction") or "").strip()
    if not name and "lindt home of chocolate" in raw.lower(): name = "Lindt Home of Chocolate"
    date = str(ai.get("date") or "").strip()
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date):
        match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b", raw)
        if match: date = f"{match.group(3)}-{int(match.group(2)):02d}-{int(match.group(1)):02d}"
    time_value = str(ai.get("time") or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", time_value):
        match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", raw)
        time_value = f"{int(match.group(1)):02d}:{match.group(2)}" if match else ""
    location = str(ai.get("location") or "").strip()
    if not location:
        match = re.search(r"Schokoladenplatz\s+1.*?8802\s+Kilchberg.*?Switzerland", raw, re.I)
        if match:
            location = re.sub(r"\s+", " ", match.group(0)).strip()
            if re.search(r"Schokoladenplatz\s+1.*?Seestrasse\s+204.*?8802\s+Kilchberg.*?Switzerland", location, re.I): location = "Schokoladenplatz 1, Seestrasse 204, 8802 Kilchberg, Switzerland"
    return {"name": name, "date": date, "time": time_value, "location": location}


def guard_marketplace_category(ai, provider_info, text=""):
    result = dict(ai or {})
    if str((provider_info or {}).get("provider") or "").strip().casefold() != "booking.com":
        return result
    if str(result.get("type") or "").strip().casefold() != "restaurant":
        return result
    lowered = str(text or "").casefold()
    lodging_markers = ("hotel", "resort", "property", "check-in", "check-out", "room", "accommodation")
    lodging_score = sum(marker in lowered for marker in lodging_markers)
    restaurant_fact_markers = ("table reservation", "restaurant name", "dining reservation", "party size", "reservation time")
    if lodging_score >= 2 and not any(marker in lowered for marker in restaurant_fact_markers):
        result["type"] = "hotel"
    elif not any(marker in lowered for marker in restaurant_fact_markers):
        result["type"] = "other"
    return result


def infer_document_type(ai, text="", context=""):
    doc_type = str(ai.get("type") or "").strip().lower()
    normalized = str(text or "").lower()
    context_normalized = str(context or "").lower()
    combined = "\n".join(value for value in (normalized, context_normalized) if value)
    semantic_hotel = understand_hotel_reservation(body=combined)
    if (semantic_hotel["intent"] == "reservation"
            and semantic_hotel["decision"] in {"accept", "review"}
            and semantic_hotel["facts"]["hotel"]["value"]):
        return "hotel"
    shared = extract_booking_facts(body=combined)
    if doc_type in ("", "other") and shared["type"] != "other":
        return shared["type"]
    hotel_markers = ("hotel", "resort", "check-in", "check in", "check-out", "check out", "room", "accommodation", "voco", "ibis", "emerald")
    restaurant_markers = ("restaurant", "dinner", "lunch", "table reservation", "מסעדה", "הזמנת שולחן")
    hotel_score = sum(1 for marker in hotel_markers if marker in combined)
    restaurant_score = sum(1 for marker in restaurant_markers if marker in combined)
    booking_lodging_labels = ("property", "check-in", "check-out", "room", "accommodation")
    lodging_label_count = sum(label in combined for label in booking_lodging_labels)
    restaurant_fact_markers = ("table reservation", "restaurant name", "dining reservation", "party size", "reservation time")
    if doc_type == "restaurant" and lodging_label_count >= 2 and not any(marker in combined for marker in restaurant_fact_markers):
        return "hotel"
    if doc_type == "restaurant" and hotel_score >= 2 and restaurant_score == 0:
        return "hotel"
    extended = infer_extended_type(ai, combined)
    if extended: return extended
    attraction_markers = ("museum", "chocolate museum", "lindt home of chocolate", "single ticket", "admission", "ticket", "guided tour", "visit", "directions", "kilchberg", "attraction")
    attraction_score = sum(1 for marker in attraction_markers if marker in normalized)
    if doc_type in ("", "other", "hotel") and attraction_score >= 2: return "attraction"
    attraction_fields = (str(ai.get("attraction") or "").strip(), str(ai.get("date") or "").strip(), str(ai.get("time") or "").strip(), str(ai.get("location") or "").strip())
    if doc_type in ("", "other") and all(attraction_fields): return "attraction"
    return doc_type or "other"


def create_trip_from_document(filename, path, source="Telegram", ai_override=None, mime_type=None, context_text=""):
    text = extract_pdf_text(path)
    ai = ai_override if ai_override is not None else (gemini_extract(path, mime_type=mime_type) or {})
    doc_type = infer_document_type(ai, text, context_text)
    if doc_type == "flight":
        segments = normalize_flight_segments(ai, text)
        ai = segments[0] if len(segments) == 1 else {**ai, "segments": segments}
    if doc_type == "attraction" and not all(str(ai.get(k) or "").strip() for k in ("attraction", "date", "time", "location")):
        focused = gemini_extract_attraction(path)
        if focused: ai.update(focused); ai["type"] = "attraction"
        doc_type = infer_document_type(ai, text)
    if doc_type == "other":
        raise ValueError("document type not recognized; send a clearer screenshot or PDF")
    if doc_type == "flight":
        flight_segments = normalize_flight_segments(ai, text)
        def valid_date(value):
            try:
                return bool(re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value)) and datetime.fromisoformat(value).date().isoformat() == value
            except ValueError:
                return False
        valid_time = lambda value: bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value))
        records = []
        for segment in flight_segments:
            departure = str(segment.get("departure_date") or "").strip()
            arrival = str(segment.get("arrival_date") or departure).strip()
            required = [str(segment.get(k) or "").strip() for k in ("airline", "flight_number", "origin", "destination", "departure_date", "departure_time")]
            arrival_time = str(segment.get("arrival_time") or "").strip()
            if not departure or not arrival or not all(required) or not valid_date(departure) or not valid_date(arrival) or not valid_time(str(segment.get("departure_time"))) or (arrival_time and not valid_time(arrival_time)):
                raise ValueError("flight document missing unambiguous flight facts")
            records.append({"airline":segment.get("airline"),"number":_normalize_flight_number(segment.get("flight_number")),"origin":segment.get("origin"),"destination":segment.get("destination"),"departure_date":departure,"departure_time":segment.get("departure_time"),"arrival_date":arrival,"arrival_time":str(segment.get("arrival_time") or ""),"document":filename})
        departure = min(record["departure_date"] for record in records)
        arrival = max(record["arrival_date"] for record in records)
        trips = load_trips()
        target = dated_trip_target(trips, departure)
        if not target:
            target = create_dated_trip_shell(trips, departure, arrival, source, filename, f"טיול · {records[0].get('destination')}")
        target.setdefault("flights", []).extend(records)
        target["flights"] = list({json.dumps(f, sort_keys=True, ensure_ascii=False): f for f in target["flights"]}.values())
        target.setdefault("documents", []).append(filename)
        target["documents"] = list(dict.fromkeys(target["documents"]))
        target.setdefault("document_titles", {})[filename] = f"כרטיס טיסה · {records[0].get('airline')} {records[0].get('number')}"
        preserve_booking_metadata(target, source, status=str(ai.get("status") or "confirmed").strip().lower())
        save_trips(trips)
        events = load_events()
        for record in records:
            event = [str(record["departure_time"]), "✈️", f"טיסה {record['airline']} {record['number']}", f"{record['origin']} → {record['destination']} · יציאה {record['departure_time']} · הגעה {record['arrival_time']}", "טיסה", source, record["departure_date"]]
            if not any(len(e) > 6 and e[2] == event[2] and e[6] == record["departure_date"] for e in events):
                events.insert(0, event)
        save_events(events)
        return {**target, "_ingested_type": "flight"}
    if doc_type == "insurance":
        trips = load_trips()
        policy_start = normalize_date_value(str(ai.get("valid_from") or ai.get("check_in") or ai.get("departure_date") or "").strip()) or str(ai.get("valid_from") or ai.get("check_in") or ai.get("departure_date") or "").strip()
        policy_end = normalize_date_value(str(ai.get("valid_to") or ai.get("check_out") or ai.get("arrival_date") or "").strip()) or str(ai.get("valid_to") or ai.get("check_out") or ai.get("arrival_date") or "").strip()
        target = dated_trip_target(trips, policy_start)
        if not target and policy_start:
            target = create_dated_trip_shell(trips, policy_start, policy_end or policy_start, source, filename, "טיול חדש · ביטוח")
        if not target and len(trips) == 1: target = trips[0]
        if not target: raise ValueError("insurance cannot be assigned to one dated trip")
        insurance = normalize_insurance(ai)
        insurance["document"] = filename
        insurance["source_language"] = insurance.get("source_language") or str(ai.get("source_language") or "").strip()
        target["insurance"] = insurance
        preserve_booking_metadata(target, source, status=str(ai.get("status") or "confirmed").strip().lower())
        target.setdefault("documents", []).append(filename); target["documents"] = list(dict.fromkeys(target["documents"])); target.setdefault("document_titles", {})[filename] = "ביטוח נסיעות לחו״ל"; save_trips(trips)
        return {**target, "_ingested_type": "insurance"}
    if doc_type == "restaurant":
        date = str(ai.get("date") or "").strip(); time_value = str(ai.get("time") or "").strip(); name = str(ai.get("restaurant_name") or ai.get("name") or "").strip()
        location = str(ai.get("address") or ai.get("location") or "").strip(); phone = str(ai.get("phone") or "").strip()
        if not all((date, time_value, name)): raise ValueError("restaurant reservation missing name, date, or time")
        trips = load_trips(); target = dated_trip_target(trips, date)
        if not target: target = create_dated_trip_shell(trips, date, date, source, filename, f"טיול · {location or name}")
        record = {"name": name, "date": date, "time": time_value, "address": location, "location": location, "phone": phone, "supplier": str(ai.get("supplier") or "").strip(), "confirmation_number": str(ai.get("confirmation_number") or "").strip(), "document": filename, "status": "confirmed"}
        target.setdefault("restaurants", []).append(record); target["restaurants"] = list({json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in target["restaurants"]}.values()); preserve_booking_metadata(target, source, status=str(ai.get("status") or "confirmed").strip().lower()); target.setdefault("documents", []).append(filename); target["documents"] = list(dict.fromkeys(target["documents"])); target.setdefault("document_titles", {})[filename] = f"הזמנת מסעדה · {name}"; save_trips(trips)
        events = load_events(); event = [time_value, "🍽️", name, location or "הזמנת מסעדה", "מסעדה", source, date, location]
        if not any(len(e) > 6 and e[2] == name and e[6] == date for e in events): events.insert(0, event); save_events(events)
        return {**target, "_ingested_type": "restaurant"}
    if doc_type == "car_rental":
        ai = normalize_car_rental_fields(ai, text)
        pickup_date = str(ai.get("pickup_date") or "").strip(); pickup_time = str(ai.get("pickup_time") or "").strip(); pickup_location = str(ai.get("pickup_location") or "").strip(); vehicle_type = str(ai.get("vehicle_type") or "").strip()
        pickup_date = normalize_date_value(pickup_date) or pickup_date
        dropoff_date = normalize_date_value(str(ai.get("dropoff_date") or "").strip()) or str(ai.get("dropoff_date") or "").strip()
        if not all((pickup_date, pickup_time, pickup_location, vehicle_type)): raise ValueError("car rental missing pickup location, time, date, or vehicle type")
        trips = load_trips(); target = dated_trip_target(trips, pickup_date)
        if not target: target = create_dated_trip_shell(trips, pickup_date, dropoff_date or pickup_date, source, filename, f"טיול · {trip_title_from_location(pickup_location, 'טיול חדש')}")
        record = {"pickup_date":pickup_date,"pickup_time":pickup_time,"pickup_location":pickup_location,"vehicle_type":vehicle_type,"dropoff_date":dropoff_date,"dropoff_time":str(ai.get("dropoff_time") or "").strip(),"dropoff_location":str(ai.get("dropoff_location") or "").strip(),"document":filename}
        target.setdefault("rentals", []).append(record); target["rentals"] = list({json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in target["rentals"]}.values()); preserve_booking_metadata(target, source, status=str(ai.get("status") or "confirmed").strip().lower()); target.setdefault("documents", []).append(filename); target["documents"] = list(dict.fromkeys(target["documents"])); target.setdefault("document_titles", {})[filename] = f"השכרת רכב · {vehicle_type}"; save_trips(trips)
        events = load_events(); title = f"איסוף רכב · {vehicle_type}"; details = f"{pickup_location} · שעה {pickup_time}"; event = [pickup_time, "🚗", title, details, "רכב", source, pickup_date, pickup_location]
        if not any(len(e) > 6 and e[2] == title and e[6] == pickup_date for e in events): events.insert(0, event); save_events(events)
        if record["dropoff_date"] and record["dropoff_time"] and record["dropoff_location"]:
            return_title = f"החזרת רכב · {vehicle_type}"; return_event = [record["dropoff_time"], "🚗", return_title, f"{record['dropoff_location']} · שעה {record['dropoff_time']}", "רכב", source, record["dropoff_date"], record["dropoff_location"]]
            if not any(len(e) > 6 and e[2] == return_title and e[6] == record["dropoff_date"] for e in events): events.insert(0, return_event); save_events(events)
        return {**target, "_ingested_type": "car_rental"}
    if doc_type in ("train", "attraction"):
        if doc_type == "train":
            date = normalize_date_value(str(ai.get("departure_date") or "").strip()) or str(ai.get("departure_date") or "").strip()
            time_value = str(ai.get("departure_time") or "").strip()
            if re.fullmatch(r"\d{1,2}:\d{2}", time_value):
                time_value = time_value.zfill(5)
            name = str(ai.get("train_number") or "").strip(); origin = str(ai.get("origin") or "").strip(); destination = str(ai.get("destination") or "").strip()
            if not all((date, time_value, name, origin, destination)): raise ValueError("train document missing unambiguous facts")
            title = f"רכבת {name}"; details = f"{origin} → {destination}"; record = {"number":name,"origin":origin,"destination":destination,"date":date,"time":time_value,"document":filename}; kind = "רכבת"; icon = "🚆"
        else:
            facts = extract_attraction_facts(text, ai); date = facts["date"]; time_value = facts["time"]; name = facts["name"]; location = facts["location"]
            if not all((date, time_value, name, location)): raise ValueError("attraction document missing unambiguous facts")
            title = name; details = location; record = {"name":name,"location":location,"date":date,"time":time_value,"document":filename}; kind = "אטרקציה"; icon = "🎟️"
        trips = load_trips(); target = dated_trip_target(trips, date)
        if not target: target = create_dated_trip_shell(trips, date, date, source, filename, f"טיול · {name}")
        key = "trains" if doc_type == "train" else "attractions"; target.setdefault(key, []).append(record); target[key] = list({json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in target[key]}.values()); preserve_booking_metadata(target, source, status=str(ai.get("status") or "confirmed").strip().lower()); target.setdefault("documents", []).append(filename); target["documents"] = list(dict.fromkeys(target["documents"])); target.setdefault("document_titles", {})[filename] = ((f"כרטיס רכבת · {name}") if doc_type == "train" else f"אטרקציה · {name}"); save_trips(trips)
        events = load_events(); event = [time_value, icon, title, details, kind, source, date, record.get("location", "")]
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
    ai_dates = [normalize_date_value(str(ai.get("check_in") or "").strip()), normalize_date_value(str(ai.get("check_out") or "").strip())]
    ambiguous_numeric_dates = re.findall(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if any(int(first) <= 12 and int(second) <= 12 for first, second, _ in ambiguous_numeric_dates) and not all(ai_dates):
        dates = []
    elif all(ai_dates): dates = [(value[:4], value[5:7], value[8:10]) for value in ai_dates]
    start = "-".join(dates[0]) if dates else ""
    end = "-".join(dates[-1]) if len(dates) > 1 else start
    if not start or not end:
        raise ValueError("hotel document missing unambiguous lodging facts")
    start, end = validate_date_range(start, end)
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
    address = str(ai.get("address") or ai.get("location") or "").strip()
    trip = {"id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"), "title": title, "start": start, "end": end, "days": 0, "source": source, "document": filename, "documents": [filename], "image": image, "images": images, "destinations": destinations, "hotel": hotel, "hotels": [{"name": hotel, "city": display_cities[0] if display_cities else ai_city, "country": ai_country or (cities[0][1] if cities else ""), "start": start, "end": end, "checkin_time": checkin_time, "checkout_time": checkout_time, "address": address, "document": filename}], "checkin_time": checkin_time, "checkout_time": checkout_time, "document_titles": {filename: document_title}}
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
    if not match and start:
        match = dated_trip_target(trips, start)
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
        booking = {"name": hotel, "city": display_cities[0] if display_cities else ai_city, "country": ai_country or (cities[0][1] if cities else ""), "start": start, "end": end, "checkin_time": checkin_time, "checkout_time": checkout_time, "address": address, "supplier": str(ai.get("supplier") or "").strip(), "confirmation_number": str(ai.get("confirmation_number") or "").strip(), "document": filename}
        existing_hotels = match.get("hotels") or ([{"name": match.get("hotel"), "start": match.get("start"), "end": match.get("end"), "checkin_time": match.get("checkin_time", "14:00"), "checkout_time": match.get("checkout_time", "11:00")} ] if match.get("hotel") else [])
        if same_booking:
            existing_hotels = [h for h in existing_hotels if not (h.get("start") == start and h.get("end") == end)] + [booking]
        elif hotel:
            existing_hotels = [h for h in existing_hotels if not (h.get("start") == start and h.get("end") == end)] + [booking]
        match["hotels"] = existing_hotels
        preserve_booking_metadata(match, source, status=str(ai.get("status") or "confirmed").strip().lower())
        if hotel and same_booking: match["hotel"] = hotel; match["checkin_time"] = checkin_time; match["checkout_time"] = checkout_time
        elif not match.get("hotel") and hotel: match["hotel"] = hotel
        save_trips(trips)
        return match
    trips.insert(0, preserve_booking_metadata(trip, source, status=str(ai.get("status") or "confirmed").strip().lower())); save_trips(trips)
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


def download_telegram_file(file_path):
    download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    with urllib.request.urlopen(download_url, timeout=60) as response:
        return read_bounded_stream(response)

def add_event(title, details, kind, source="Telegram", event_time=None):
    events = load_events()
    now = event_time or datetime.now().strftime("%H:%M")
    event = [now, ICONS.get(kind, "📌"), title, details, kind, source]
    events.insert(0, event)
    save_events(events)
    return event

def ensure_hotel_events():
    events = [event_as_legacy_list(event, event[9] if isinstance(event, list) and len(event) > 9 else "") for event in load_events()]
    trips = load_trips()
    before = len(events)
    demo_markers = ("LX 162", "Ruby Mimi", "רכבת לציריך", "אישור חדש")
    events = [e for e in events if len(e) < 5 or (e[2] not in demo_markers and e[4] != "מסמך" and not (e[4] == "מלון" and not str(e[6] if len(e) > 6 else "").strip()))]
    events = [e for e in events if len(e) < 5 or (e[2] not in demo_markers and e[4] != "מסמך" and e[4] != "מלון" and not str(e[2]).strip().startswith(("http://", "https://")))]
    seen = set()
    cleaned = []
    for event in events:
        event_date = str(event[6] if len(event) > 6 else "")
        trip_id = str(event[9]) if len(event) > 9 else ""
        key = (trip_id, str(event[2]), event_date, str(event[4] if len(event) > 4 else ""))
        if key not in seen:
            seen.add(key)
            cleaned.append(event)
    events = cleaned
    changed = len(events) != before
    trips_changed = False
    for trip in trips:
        label_map = {"Budapest":"בודפשט", "Munich":"מינכן", "München":"מינכן", "Frankfurt":"פרנקפורט"}
        country_map = {"Germany":"גרמניה", "Hungary":"הונגריה", "Switzerland":"שוויץ", "Austria":"אוסטריה", "France":"צרפת", "Italy":"איטליה"}
        for destination in trip.get("destinations", []):
            if destination.get("city") in label_map: destination["city"] = label_map[destination["city"]]; trips_changed = True
            if destination.get("country") in country_map: destination["country"] = country_map[destination["country"]]; trips_changed = True
        if trip.get("hotels"):
            for booking in trip["hotels"]:
                if booking.get("country") in country_map: booking["country"] = country_map[booking["country"]]; trips_changed = True
            coalesced = []
            for booking in sorted(trip["hotels"], key=lambda h: (str(h.get("start") or "9999-99-99"), str(h.get("end") or "9999-99-99"))):
                same = next((existing for existing in coalesced if str(existing.get("name") or "").strip().casefold() == str(booking.get("name") or "").strip().casefold() and str(existing.get("document") or "") == str(booking.get("document") or "") and str(existing.get("start") or "") <= str(booking.get("end") or "") and str(booking.get("start") or "") <= str(existing.get("end") or "")), None)
                if same:
                    same["start"] = min(str(same.get("start") or booking.get("start") or ""), str(booking.get("start") or same.get("start") or ""))
                    same["end"] = max(str(same.get("end") or booking.get("end") or ""), str(booking.get("end") or same.get("end") or ""))
                    if not same.get("city") and booking.get("city"): same["city"] = booking["city"]
                    trips_changed = True
                else: coalesced.append(booking)
            if len(coalesced) != len(trip["hotels"]): trip["hotels"] = coalesced; trips_changed = True
        ordered_hotels = sorted(trip.get("hotels", []), key=lambda h: str(h.get("start") or "9999-99-99"))
        countries = list(dict.fromkeys((h.get("country") or "") for h in ordered_hotels if h.get("country")))
        if not countries:
            countries = list(dict.fromkeys(d.get("country", "") for d in trip.get("destinations", []) if d.get("country")))
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
        if not trip.get("hotels") and hotel != "המלון" and trip.get("start") and trip.get("end"):
            city = (trip.get("destinations") or [{}])[0].get("city", "")
            country = (trip.get("destinations") or [{}])[0].get("country", "")
            trip["hotels"] = [{"name": hotel, "city": city, "country": country, "start": trip["start"], "end": trip["end"], "checkin_time": trip.get("checkin_time", "14:00"), "checkout_time": trip.get("checkout_time", "11:00"), "document": trip.get("document")}]
            trips_changed = True
        for collection in ("flights", "trains", "attractions", "rentals", "restaurants"):
            if collection not in trip: trip[collection] = []; trips_changed = True
        docs = list(dict.fromkeys(trip.get("documents") or ([trip.get("document")] if trip.get("document") else [])))
        titles = trip.get("document_titles", {})
        for record in trip.get("hotels", []):
            doc = record.get("document")
            if doc: docs.append(doc); titles[doc] = f"אישור מלון · {record.get('name', 'המלון')}"
        for record in trip.get("flights", []):
            doc = record.get("document")
            if doc: docs.append(doc); titles[doc] = f"כרטיס טיסה · {record.get('airline', '')} {record.get('number', '')}".strip()
        for record in trip.get("trains", []):
            doc = record.get("document")
            if doc: docs.append(doc); titles[doc] = f"כרטיס רכבת · {record.get('number', '')}".strip()
        for record in trip.get("rentals", []):
            doc = record.get("document")
            if doc: docs.append(doc); titles[doc] = f"השכרת רכב · {record.get('vehicle_type', 'רכב')}"
        for record in trip.get("attractions", []):
            doc = record.get("document")
            if doc: docs.append(doc); titles[doc] = f"אטרקציה · {record.get('name', 'אטרקציה')}"
        for record in trip.get("restaurants", []):
            doc = record.get("document")
            if doc: docs.append(doc); titles[doc] = f"הזמנת מסעדה · {record.get('name', 'מסעדה')}"
        insurance = trip.get("insurance") or {}
        if insurance.get("document"):
            docs.append(insurance["document"]); titles[insurance["document"]] = "ביטוח נסיעות לחו״ל"
        docs = list(dict.fromkeys(docs))
        unique_docs = []
        seen_titles = set()
        for doc in reversed(docs):
            if not doc: continue
            source_title = str(titles.get(doc, "")).strip()
            typed_prefixes = ("כרטיס טיסה", "כרטיס רכבת", "אטרקציה", "השכרת רכב", "הזמנת מסעדה", "ביטוח נסיעות")
            desired_title = source_title if (source_title.startswith(typed_prefixes) or source_title.startswith("אישור מלון · ")) else (f"אישור מלון · {hotel}" if hotel != "המלון" else "אישור מלון")
            dedupe_key = desired_title if desired_title.startswith(typed_prefixes) else ("hotel", desired_title)
            if dedupe_key in seen_titles: continue
            seen_titles.add(dedupe_key); unique_docs.append(doc); titles[doc] = desired_title
        docs = list(reversed(unique_docs))
        if trip.get("documents") != docs: trip["documents"] = docs; trips_changed = True
        if trip.get("document_titles") != titles: trip["document_titles"] = titles; trips_changed = True
        event_trip_id = str(trip.get("id") or "")
        for booking in trip.get("hotels", []):
            if booking.get("status") in {"cancelled", "refunded", "superseded"}: continue
            booking_hotel = booking.get("name") or hotel
            booking_start = booking.get("start")
            booking_end = booking.get("end")
            if booking_start:
                key = ("צ׳ק-אין · " + booking_hotel, booking_start, event_trip_id)
                if key not in seen:
                    base_event = [booking.get("checkin_time", "14:00"), ICONS["מלון"], key[0], f"{booking_start} · שעה: {booking.get('checkin_time', '14:00')}", "מלון", "PDF", booking_start, booking.get("address") or booking.get("location") or ""]
                    events.insert(0, event_as_legacy_list(base_event, event_trip_id)); seen.add(key); changed = True
            if booking_end and booking_end != booking_start:
                key = ("צ׳ק-אאוט · " + booking_hotel, booking_end, event_trip_id)
                if key not in seen:
                    base_event = [booking.get("checkout_time", "11:00"), ICONS["מלון"], key[0], f"{booking_end} · שעה: {booking.get('checkout_time', '11:00')}", "מלון", "PDF", booking_end, booking.get("address") or booking.get("location") or ""]
                    events.insert(0, event_as_legacy_list(base_event, event_trip_id)); seen.add(key); changed = True
        for rental in trip.get("rentals", []):
            if rental.get("pickup_date") and rental.get("pickup_time") and rental.get("pickup_location"):
                key = (event_trip_id, "איסוף רכב · " + str(rental.get("vehicle_type") or "רכב"), str(rental["pickup_date"]), "רכב")
                if key not in seen:
                    events.insert(0, event_as_legacy_list([rental["pickup_time"], "🚗", key[1], f"{rental['pickup_location']} · שעה {rental['pickup_time']}", "רכב", "Telegram", rental["pickup_date"], rental["pickup_location"]], event_trip_id)); seen.add(key); changed = True
            if rental.get("dropoff_date") and rental.get("dropoff_time") and rental.get("dropoff_location"):
                key = (event_trip_id, "החזרת רכב · " + str(rental.get("vehicle_type") or "רכב"), str(rental["dropoff_date"]), "רכב")
                if key not in seen:
                    events.insert(0, event_as_legacy_list([rental["dropoff_time"], "🚗", key[1], f"{rental['dropoff_location']} · שעה {rental['dropoff_time']}", "רכב", "Telegram", rental["dropoff_date"], rental["dropoff_location"]], event_trip_id)); seen.add(key); changed = True
        for restaurant in trip.get("restaurants", []):
            if restaurant.get("date") and restaurant.get("time"):
                location = restaurant.get("address") or restaurant.get("location") or ""
                key = (event_trip_id, "מסעדה · " + str(restaurant.get("name") or "מסעדה"), str(restaurant["date"]), "מסעדה")
                if key not in seen:
                    events.insert(0, event_as_legacy_list([restaurant["time"], "🍽️", key[1], location or "הזמנת מסעדה", "מסעדה", "Email", restaurant["date"], location], event_trip_id)); seen.add(key); changed = True
    if trips_changed: save_trips(trips)
    if changed: save_events(events)
    return events


def parse_upload_multipart(body, content_type):
    message = Message()
    message["Content-Type"] = content_type
    message.set_payload(body)
    parsed = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    for part in parsed.walk():
        if part.get_content_disposition() == "form-data" and part.get_param("name", header="content-disposition") == "file":
            return Path(part.get_filename() or "").name, part.get_payload(decode=True) or b""
    return "", b""


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
        item, declared_name, declared_mime = telegram_media_descriptor(message)
        name = declared_name
        intake_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        record = {"id": intake_id, "chat_id": chat, "file_id": item.get("file_id"), "filename": name, "declared_mime_type": declared_mime, "status": "received", "stage": "received", "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
        items = load_telegram_intake(); items.insert(0, record); save_telegram_intake(items)
        UPLOADS.mkdir(parents=True, exist_ok=True)
        try:
            update_telegram_intake(intake_id, status="processing", stage="metadata")
            file_info = api("getFile", {"file_id": item["file_id"]})
            remote_name = Path(file_info.get("file_path", "")).name
            name = filename_for_mime(name, declared_mime, remote_name)
            local = UPLOADS / (datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S_") + Path(name).name)
            update_telegram_intake(intake_id, filename=name, remote_path=file_info.get("file_path", ""), stage="download")
            local.write_bytes(bounded_document_bytes(download_telegram_file(file_info["file_path"])))
            update_telegram_intake(intake_id, local_filename=local.name, stage="extract")
            record["local_filename"] = local.name
            record["stage"] = "extract"
            trip = create_trip_from_document(local.name, local, "Telegram", mime_type=declared_mime)
            title = trip["title"]
            if trip.get("_ingested_type", "hotel") == "hotel" and trip.get("start"):
                add_event("צ׳ק-אין · " + (trip.get("hotel") or title), f"{trip['start']} · שעה: {trip.get('checkin_time', '14:00')}", "מלון", event_time=trip.get("checkin_time", "14:00"))
            if trip.get("_ingested_type", "hotel") == "hotel" and trip.get("end") and trip.get("end") != trip.get("start"):
                add_event("צ׳ק-אאוט · " + (trip.get("hotel") or title), f"{trip['end']} · שעה: {trip.get('checkout_time', '11:00')}", "מלון", event_time=trip.get("checkout_time", "11:00"))
            update_telegram_intake(intake_id, status="processed", stage="persisted", trip_id=trip.get("id"), ingested_type=trip.get("_ingested_type", "hotel"))
            try:
                send(chat, f"קיבלתי את {name} ✅\\nעודכן הטיול: {title}")
                update_telegram_intake(intake_id, stage="acknowledged")
            except Exception as ack_exc:
                update_telegram_intake(intake_id, stage="acknowledgement_failed", error=f"acknowledge: {type(ack_exc).__name__}: {str(ack_exc)[:160]}")
        except Exception as exc:
            print("Document processing error:", exc, flush=True)
            stage = "download" if not record.get("local_filename") else ("extract" if record.get("stage") != "persisted" else "persist")
            update_telegram_intake(intake_id, status="failed", stage=stage, error=f"{stage}: {type(exc).__name__}: {str(exc)[:160]}")
            try:
                send(chat, f"יש בעיה בזיהוי האישור ❌\nלא עודכן הלוז. צריך לזהות בוודאות שם מלון, יעד ותאריכים.")
            except Exception as ack_exc:
                update_telegram_intake(intake_id, error=f"{stage}: {type(exc).__name__}; acknowledge: {type(ack_exc).__name__}")
        return
    if not text:
        send(chat, "שלח טקסט עם פרטי הזמנה או קובץ.")
        return
    urls = re.findall(r"https?://[^\s]+", text)
    if urls:
        try:
            request = urllib.request.Request(urls[0], headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=30) as response: page = response.read(500000).decode("utf-8", errors="ignore")
            page = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", page)
            page = re.sub(r"\s+", " ", page).strip()
            with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
                tmp.write(text + "\n" + page); shared_path = Path(tmp.name)
            trip = create_trip_from_document("shared-booking.txt", shared_path)
            send(chat, f"קיבלתי את הקישור ✅\nזוהה כ-{trip.get('_ingested_type', 'הזמנה')} ונוסף לטיול: {trip['title']}")
        except Exception as exc:
            print("Shared link processing error:", type(exc).__name__, flush=True)
            send(chat, "קיבלתי קישור, אבל לא הצלחתי לזהות ממנו כרטיס טיסה. שלח צילום מסך או PDF של הכרטיס.")
        return
    kind = next((k for k in ICONS if k in text), "עדכון")
    title = text.split("\n", 1)[0][:90]
    add_event(title, "נוסף מהבוט · " + (text[:180]), kind)
    send(chat, f"נוסף ל-TRIPY ✅\n{title}")

def _safe_telegram_error(exc):
    message = f"{type(exc).__name__}: {exc}"
    return message.replace(TOKEN, "[redacted]") if TOKEN else message


def telegram_status():
    with TELEGRAM_STATE_LOCK:
        return dict(TELEGRAM_STATE)


def _set_telegram_status(**changes):
    with TELEGRAM_STATE_LOCK:
        TELEGRAM_STATE.update(changes)


def start_telegram_polling():
    """Start Telegram independently; never block HTTP/email startup."""
    if not TOKEN:
        _set_telegram_status(status="disabled", attempts=0, last_error=None)
        return
    for attempt in range(1, TELEGRAM_STARTUP_RETRIES + 1):
        _set_telegram_status(status="starting", attempts=attempt)
        try:
            api("deleteWebhook")
            _set_telegram_status(status="polling", last_error=None, last_success_at=datetime.now(timezone.utc).isoformat())
            poll()
            _set_telegram_status(status="stopped")
            return
        except Exception as exc:
            _set_telegram_status(status="unavailable", last_error=_safe_telegram_error(exc))
            if attempt < TELEGRAM_STARTUP_RETRIES:
                time.sleep(TELEGRAM_RETRY_DELAY_SECONDS)
    print("Telegram startup unavailable; HTTP/email remain available", flush=True)


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
            _set_telegram_status(status="degraded", last_error=_safe_telegram_error(exc))
            print("Telegram polling error:", _safe_telegram_error(exc), flush=True)
            time.sleep(5)

class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload, cookies=None):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = cors_origin(self.headers.get("Origin", ""))
        if origin: self.send_header("Access-Control-Allow-Origin", origin)
        if API_AUTH_TOKEN and origin: self.send_header("Access-Control-Allow-Credentials", "true")
        if origin and origin != "*": self.send_header("Vary", "Origin")
        for cookie in cookies or []: self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _authorized(self, path):
        if api_requires_auth(path) and not api_request_authorized(self.headers):
            self._json(401, {"error": "unauthorized"})
            return False
        return True
    def do_OPTIONS(self):
        origin = cors_origin(self.headers.get("Origin", ""))
        self.send_response(204)
        if origin: self.send_header("Access-Control-Allow-Origin", origin)
        if API_AUTH_TOKEN and origin: self.send_header("Access-Control-Allow-Credentials", "true")
        if origin and origin != "*": self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-TRIPY-API-TOKEN")
        self.end_headers()
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if not self._authorized(path): return
        if path in ("/health", "/healthz"):
            self._json(200, {"status": "ok", "service": "trypy"}); return
        if path in ("/ready", "/readyz"):
            ready = HTTP_READY and EMAIL_READY
            self._json(200 if ready else 503, {"status": "ready" if ready else "starting", "http": HTTP_READY, "email": EMAIL_READY, "telegram": telegram_status()}); return
        if path == "/api/auth/me":
            self._json(200, {"authenticated": bool(_session_user(self.headers) or (API_AUTH_TOKEN and api_request_authorized(self.headers)) or not _auth_enabled()), "setup_required": bool(TRIPY_SETUP_TOKEN and not _auth_json(AUTH_STORE).get("user"))}); return
        if path == "/api/share/me":
            trip = _share_trip(self.headers)
            self._json(200 if trip else 401, {"trip": trip} if trip else {"error": "share_unauthorized"}); return
        if path.startswith("/api/share/"):
            token = path[len("/api/share/"):]
            if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", token):
                self._json(404, {"error": "share_not_found"}); return
            digest = hashlib.sha256(token.encode()).hexdigest()
            records = _share_records(); record = next((item for item in records if item.get("token_hash") == digest), None)
            if not record or record.get("used") or record.get("revoked") or float(record.get("expires_at", 0)) <= time.time():
                self._json(404, {"error": "share_not_found"}); return
            trip = _scoped_trip(record.get("trip_id"))
            if not trip:
                self._json(404, {"error": "trip_not_found"}); return
            session = secrets.token_urlsafe(32)
            record.update({"used": True, "used_at": time.time(), "session_hash": hashlib.sha256(session.encode()).hexdigest(), "session_expires_at": time.time() + SHARE_TTL_SECONDS})
            _save_share_records(records)
            self._json(200, {"trip": trip}, cookies=[_session_header(session, share=True)]); return
        if path == "/api/rates":
            try:
                request = urllib.request.Request("https://api.frankfurter.dev/v2/rates?base=ILS&quotes=USD,GBP,EUR,CHF,CAD,AUD,JPY,HUF,INR,PLN,CZK,TRY,RON,BGN,AED,GEL", headers={"Accept":"application/json", "User-Agent":"TRIPY/1.0"})
                with urllib.request.urlopen(request, timeout=15) as response: raw_rates = json.loads(response.read())
                if not isinstance(raw_rates, list) or not raw_rates: raise ValueError("empty_rates")
                normalized = {str(row["quote"]): 1 / float(row["rate"]) for row in raw_rates if row.get("quote") and float(row.get("rate", 0)) > 0}
                self._json(200, {"base":"ILS", "date":str(raw_rates[0].get("date") or ""), "rates":normalized, "source":"Frankfurter"}); return
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                self._json(502, {"error":"rates_unavailable"}); return
        if path == "/api/events": self._json(200, {"events": ensure_hotel_events()}); return
        if path == "/api/trips": ensure_hotel_events(); self._json(200, {"trips": load_trips()}); return
        if path == "/api/push/vapid-public-key": self._json(200, {"public_key": vapid_public_key()}); return
        if path == "/api/notifications": self._json(200, {"notifications": load_notifications(), "unread": len(load_notifications())}); return
        if path == "/api/intake/email":
            fields = ("id", "status", "intent", "subject", "from", "to", "reply_to", "date", "received_at", "processed_at", "processing_started_at", "processing_attempts", "processing_stage", "processing_stage_updated_at", "processing_stage_history", "processing_error", "extraction_telemetry", "provider", "provider_category_hint", "provider_confidence", "provider_evidence", "ingested_type", "trip_id", "review_reason", "attachments")
            emails = [{**{key: record.get(key) for key in fields}, "body_preview": re.sub(r"\\s+", " ", email_body_text(record)).strip()[:280], "raw_source": _email_raw_source(record)} for record in load_email_index()]
            self._json(200, {"emails": emails}); return
        if path == "/api/intake/telegram":
            self._json(200, {"intake": load_telegram_intake()}); return
        if path.startswith("/api/documents/"):
            requested = urllib.parse.unquote(path[len("/api/documents/"):])
            filename = safe_document_filename(requested)
            file_path = resolve_document_path(UPLOADS, requested)
            if file_path is None or not file_path.exists():
                candidates = sorted((candidate for candidate in UPLOADS.glob("*_" + filename) if resolve_document_path(UPLOADS, candidate.name) == candidate.resolve()), key=lambda p: p.stat().st_mtime, reverse=True)
                file_path = candidates[0] if candidates else file_path
            if file_path and file_path.exists() and file_path.is_file():
                if file_path.stat().st_size > MAX_DOCUMENT_SIZE:
                    self._json(413, {"error": "document_too_large"}); return
                raw = read_bounded_file(file_path); self.send_response(200); self.send_header("Content-Type", document_content_type(file_path)); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(filename)); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            self._json(404, {"error": "document_not_found"}); return
        if path in ("/", "/index.html"):
            raw = (ROOT / "index.html").read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Cache-Control", "no-store")
            if API_AUTH_TOKEN and not _auth_enabled():
                self.send_header("Set-Cookie", _session_header(_new_session()))
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        static = {"/tripy-icon.png": (ROOT / "tripy-icon.png", "image/png"), "/manifest.webmanifest": (ROOT / "manifest.webmanifest", "application/manifest+json"), "/service-worker.js": (ROOT / "service-worker.js", "application/javascript; charset=utf-8")}
        if path in static:
            file_path, content_type = static[path]
            if file_path.exists():
                raw = file_path.read_bytes(); self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        self._json(404, {"error": "not_found"})
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._authorized(path): return
        if path == "/api/notifications/clear":
            count = clear_notifications()
            self._json(200, {"cleared": count}); return
        if path == "/api/intake/clear":
            removed = clear_email_intake_permanently()
            self._json(200, {"deleted": removed, "intake_empty": not load_email_index()}); return
        if path in ("/api/auth/passkey/register/options", "/api/auth/passkey/register"):
            if _passkey_unavailable(self): return
            if not _session_user(self.headers):
                self._json(401, {"error": "unauthorized"}); return
            try:
                length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
                store = _auth_json(AUTH_STORE); user = store.get("user") or {}
                if path.endswith("/options"):
                    existing = [PublicKeyCredentialDescriptor(id=_unb64(item["credential_id"])) for item in user.get("passkeys", [])]
                    options = generate_registration_options(
                        rp_id=WEBAUTHN_RP_ID, rp_name=WEBAUTHN_RP_NAME,
                        user_name=str(user.get("username") or "owner"),
                        user_display_name=str(user.get("username") or "owner"),
                        user_id=hashlib.sha256(str(user.get("username")).encode()).digest(),
                        challenge=secrets.token_bytes(32),
                        exclude_credentials=existing,
                    )
                    serialized = json.loads(options_to_json(options))
                    session_value = _cookie_value(self.headers, SESSION_COOKIE)
                    _webauthn_challenges(store)["registration"] = {"challenge": serialized["challenge"], "expires_at": time.time() + 300, "session_hash": hashlib.sha256(session_value.encode()).hexdigest()}
                    _save_auth_json(AUTH_STORE, store)
                    self._json(200, serialized); return
                challenge = _webauthn_challenges(store).pop("registration", None)
                current_session = _cookie_value(self.headers, SESSION_COOKIE)
                if not challenge or float(challenge.get("expires_at", 0)) <= time.time() or not hmac.compare_digest(str(challenge.get("session_hash", "")), hashlib.sha256(current_session.encode()).hexdigest()):
                    _save_auth_json(AUTH_STORE, store); self._json(400, {"error": "challenge_expired"}); return
                verified = verify_registration_response(
                    credential=payload.get("credential"), expected_challenge=_unb64(challenge["challenge"]),
                    expected_rp_id=WEBAUTHN_RP_ID, expected_origin=WEBAUTHN_ORIGIN,
                )
                user.setdefault("passkeys", []).append({"credential_id": _b64(verified.credential_id), "public_key": _b64(verified.credential_public_key), "sign_count": verified.sign_count, "created_at": time.time()})
                _save_auth_json(AUTH_STORE, store)
                self._json(201, {"registered": True}); return
            except Exception:
                self._json(400, {"error": "invalid_passkey_registration"}); return
        if path in ("/api/auth/passkey/authenticate/options", "/api/auth/passkey/authenticate"):
            if _passkey_unavailable(self): return
            try:
                length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
                store = _auth_json(AUTH_STORE); user = store.get("user") or {}
                username = str(payload.get("username") or user.get("username") or "").strip()
                if not _username_matches(username, user): self._json(401, {"error": "invalid_credentials"}); return
                passkeys = user.get("passkeys") or []
                if path.endswith("/options"):
                    if not passkeys: self._json(404, {"error": "no_passkeys"}); return
                    options = generate_authentication_options(
                        rp_id=WEBAUTHN_RP_ID, challenge=secrets.token_bytes(32),
                        allow_credentials=[PublicKeyCredentialDescriptor(id=_unb64(x["credential_id"])) for x in passkeys],
                        user_verification=UserVerificationRequirement.PREFERRED,
                    )
                    serialized = json.loads(options_to_json(options))
                    _webauthn_challenges(store)["authentication"] = {"challenge": serialized["challenge"], "username": username, "expires_at": time.time() + 300}
                    _save_auth_json(AUTH_STORE, store); self._json(200, serialized); return
                challenge = _webauthn_challenges(store).pop("authentication", None)
                credential = payload.get("credential") or {}
                credential_id = str(credential.get("id") or "")
                record = next((x for x in passkeys if hmac.compare_digest(str(x.get("credential_id")), credential_id)), None)
                if not challenge or float(challenge.get("expires_at", 0)) <= time.time() or not hmac.compare_digest(str(challenge.get("username", "")), username) or not record:
                    _save_auth_json(AUTH_STORE, store); self._json(401, {"error": "invalid_passkey"}); return
                verified = verify_authentication_response(
                    credential=credential, expected_challenge=_unb64(challenge["challenge"]),
                    expected_rp_id=WEBAUTHN_RP_ID, expected_origin=WEBAUTHN_ORIGIN,
                    credential_public_key=_unb64(record["public_key"]), credential_current_sign_count=int(record.get("sign_count", 0)),
                )
                record["sign_count"] = verified.new_sign_count; record["last_used_at"] = time.time(); _save_auth_json(AUTH_STORE, store)
                session = _new_session(); self._json(200, {"authenticated": True}, cookies=[_session_header(session)]); return
            except Exception:
                self._json(401, {"error": "invalid_passkey"}); return
        if path == "/api/auth/setup":
            try:
                length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
                store = _auth_json(AUTH_STORE)
                username = str(payload.get("username") or "").strip()
                password = str(payload.get("password") or "")
                if not TRIPY_SETUP_TOKEN or not hmac.compare_digest(str(payload.get("setup_token") or ""), TRIPY_SETUP_TOKEN): self._json(401, {"error": "invalid_setup_token"}); return
                if store.get("user"): self._json(409, {"error": "already_setup"}); return
                if len(username) < 3 or len(username) > 64: self._json(400, {"error": "invalid_username"}); return
                if len(password) < 12: self._json(400, {"error": "password_too_short"}); return
                store["user"] = {"username": username, "password": _password_hash(password)}; store["created_at"] = time.time(); store.setdefault("sessions", {})
                _save_auth_json(AUTH_STORE, store); session = _new_session()
                self._json(201, {"authenticated": True}, cookies=[_session_header(session)]); return
            except (OSError, ValueError, TypeError, json.JSONDecodeError): self._json(400, {"error": "invalid_setup"}); return
        if path == "/api/auth/login":
            try:
                length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
                store = _auth_json(AUTH_STORE)
                user = store.get("user") or {}
                if not _username_matches(payload.get("username"), user) or not _password_matches(payload.get("password", ""), user.get("password", {})): self._json(401, {"error": "invalid_credentials"}); return
                session = _new_session(); self._json(200, {"authenticated": True}, cookies=[_session_header(session)]); return
            except (OSError, ValueError, TypeError, json.JSONDecodeError): self._json(400, {"error": "invalid_login"}); return
        if path == "/api/auth/logout":
            value = _cookie_value(self.headers, SESSION_COOKIE); store = _auth_json(AUTH_STORE)
            if value: (store.setdefault("sessions", {})).pop(hashlib.sha256(value.encode()).hexdigest(), None); _save_auth_json(AUTH_STORE, store)
            self._json(200, {"authenticated": False}, cookies=[_session_header("", clear=True)]); return
        share_match = re.fullmatch(r"/api/trips/([^/]+)/share", path)
        if share_match:
            if not _session_user(self.headers) and not api_request_authorized(self.headers): self._json(401, {"error": "unauthorized"}); return
            trip_id = urllib.parse.unquote(share_match.group(1)); trip = _scoped_trip(trip_id)
            if not trip: self._json(404, {"error": "trip_not_found"}); return
            token = secrets.token_urlsafe(32); now = time.time(); records = _share_records()
            records[:] = [r for r in records if not (r.get("trip_id") == trip_id and (r.get("used") or r.get("revoked") or float(r.get("expires_at", 0)) <= now))]
            records.append({"token_hash": hashlib.sha256(token.encode()).hexdigest(), "trip_id": trip_id, "created_at": now, "expires_at": now + SHARE_TTL_SECONDS, "used": False, "revoked": False})
            _save_share_records(records); self._json(201, {"token": token, "trip_id": trip_id, "expires_in": SHARE_TTL_SECONDS}); return
        if path == "/api/intake/review":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                decision = str(payload.get("decision") or "save").strip().lower()
                if decision not in {"approve", "save"} or not payload.get("id"):
                    self._json(400, {"error": "invalid_review"}); return
                updated = apply_review_decision(str(payload["id"]), decision, payload.get("overrides"))
                if not updated:
                    self._json(404, {"error": "intake_not_found"}); return
                self._json(200, {"reviewed": True, "record": {"id": updated.get("id"), "status": updated.get("status"), "review_decision": updated.get("review_decision")}}); return
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_review"}); return
        if self.path == "/api/push/test":
            result = send_push_notification("TRIPY · בדיקת התראות", "אם אתה רואה את ההודעה הזו, התראות הטלפון עובדות.", tag="tripy-push-test", details=True)
            details = result if isinstance(result, dict) else {"sent": int(result), "subscriptions": 0, "failures": [], "stale_removed": 0}
            self._json(200, {"sent": details.get("sent", 0), "subscriptions": details.get("subscriptions", 0), "failures": details.get("failures", []), "stale_removed": details.get("stale_removed", 0), "webpush_available": _webpush is not None})
            return

        if self.path == "/api/push/subscribe":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                count = save_push_subscription(payload)
                self._json(201, {"subscribed": True, "subscriptions": count}); return
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": "invalid_push_subscription", "reason": str(exc)[:120]}); return
        if self.path == "/api/trips":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict) or not str(payload.get("title") or "").strip():
                    self._json(400, {"error": "trip_title_required"}); return
                trip_id = str(payload.get("id") or f"web-{int(time.time() * 1000)}").strip()[:120]
                allowed = {"id": trip_id, "title": str(payload.get("title") or "").strip()[:180], "start": str(payload.get("start") or "")[:10], "end": str(payload.get("end") or "")[:10], "days": payload.get("days") or 0, "source": str(payload.get("source") or "Web")[:40]}
                for key in ("hotels", "flights", "trains", "attractions", "rentals"):
                    if isinstance(payload.get(key), list): allowed[key] = payload[key]
                trips = load_trips()
                existing = next((trip for trip in trips if str(trip.get("id")) == trip_id), None)
                if existing:
                    existing.update(allowed); saved = existing; status = 200
                else:
                    trips.append(allowed); saved = allowed; status = 201
                save_trips(trips); self._json(status, {"trip": saved}); return
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_trip"}); return
        if self.path == "/api/intake/email":
            if not EMAIL_INTAKE_SECRET:
                self._json(503, {"error": "email_intake_not_configured"}); return
            supplied = self.headers.get("X-TRIPY-INTAKE-SECRET", "")
            if not hmac.compare_digest(supplied, EMAIL_INTAKE_SECRET):
                self._json(401, {"error": "unauthorized"}); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_EMAIL_SIZE:
                    self._json(413, {"error": "email_size_invalid"}); return
                raw = self.rfile.read(length)
                record, duplicate = store_incoming_email(raw)
                if not duplicate:
                    threading.Thread(target=process_incoming_email, args=(record,), daemon=True).start()
                self._json(202 if not duplicate else 200, {"intake": record, "duplicate": duplicate}); return
            except (OSError, ValueError, TypeError):
                self._json(400, {"error": "invalid_email"}); return
        if self.path == "/api/upload":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_UPLOAD_REQUEST_SIZE:
                    self._json(413, {"error": "upload_size_invalid"}); return
                body = self.rfile.read(length)
                filename, file_bytes = parse_upload_multipart(body, self.headers.get("Content-Type", ""))
                if not filename:
                    self._json(400, {"error":"file_required"}); return
                filename = safe_document_filename(filename)
                if not is_supported_upload_filename(filename):
                    self._json(400, {"error":"unsupported_file_type"}); return
                bounded_document_bytes(file_bytes)
                UPLOADS.mkdir(parents=True, exist_ok=True)
                stored = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{filename}"
                path = UPLOADS / stored; path.write_bytes(file_bytes)
                trip = create_trip_from_document(stored, path, "Web")
                self._json(201, {"trip":trip, "filename":filename}); return
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                print("Web upload error:", type(exc).__name__, flush=True); self._json(400, {"error":"document_processing_failed"}); return
        if self.path == "/api/manual":
            try:
                length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}"); title = str(payload.get("title") or "").strip(); date = str(payload.get("date") or "").strip(); time_value = str(payload.get("time") or "").strip(); kind = str(payload.get("kind") or "אחר").strip(); location = str(payload.get("location") or "").strip()
                if not title or not date or not re.fullmatch(r"\d{2}:\d{2}", time_value): self._json(400, {"error":"title_date_time_required"}); return
                event = [time_value, ICONS.get(kind, "📌"), title, location, kind, "Web", date, location]
                events = load_events()
                key = (title, date, kind)
                existing = next((e for e in events if len(e) > 6 and (str(e[2]), str(e[6]), str(e[4])) == key), None)
                if existing:
                    self._json(200, {"event": existing, "duplicate": True}); return
                events.insert(0, event); save_events(events); self._json(201, {"event":event}); return
            except (ValueError, TypeError, json.JSONDecodeError): self._json(400, {"error":"invalid_json"}); return
        if self.path == "/api/events":
            try:
                length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}"); index = int(payload.get("index")); event = payload.get("event")
                events = load_events()
                if index < 0 or index >= len(events) or not isinstance(event, list): self._json(400, {"error": "invalid_event"}); return
                events[index] = event[:8]
                if len(events[index]) > 6 and events[index][4] == "מלון":
                    title = str(events[index][2]).replace("צ׳ק-אין · ", "").replace("צ׳ק-אאוט · ", "").strip()
                    date = str(events[index][6])
                    trips_for_update = load_trips()
                    for trip in trips_for_update:
                        for booking in trip.get("hotels", []):
                            if booking.get("name") == title and booking.get("start" if "צ׳ק-אין" in str(events[index][2]) else "end") == date:
                                booking["checkin_time" if "צ׳ק-אין" in str(events[index][2]) else "checkout_time"] = events[index][0]
                    save_trips(trips_for_update)
                save_events(events); self._json(200, {"event": events[index]})
            except (ValueError, TypeError, json.JSONDecodeError): self._json(400, {"error": "invalid_json"})
            return
        if self.path != "/api/trips": self._json(404, {"error": "not_found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
            trip = {"id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"), "title": str(payload.get("title") or "טיול חדש")[:120], "start": str(payload.get("start") or ""), "end": str(payload.get("end") or ""), "days": int(payload.get("days") or 0), "source": "Web"}
            trips = load_trips(); trips.insert(0, trip); save_trips(trips); self._json(201, {"trip": trip})
        except (ValueError, TypeError, json.JSONDecodeError): self._json(400, {"error": "invalid_json"})
    def do_PATCH(self):
        path = self.path.split("?", 1)[0]
        if not self._authorized(path): return
        prefix = "/api/trips/"
        if not self.path.startswith(prefix): self._json(404, {"error": "not_found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
            trip_id = urllib.parse.unquote(self.path[len(prefix):]); trips = load_trips()
            for trip in trips:
                if str(trip.get("id")) == trip_id:
                    for key in ("hotel", "title", "start", "end", "days", "hotels", "flights", "trains", "attractions", "rentals"):
                        if key in payload: trip[key] = payload[key]
                    save_trips(trips); self._json(200, {"trip": trip}); return
            self._json(404, {"error": "trip_not_found"})
        except (ValueError, TypeError, json.JSONDecodeError): self._json(400, {"error": "invalid_json"})

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        if not self._authorized(path): return
        share_match = re.fullmatch(r"/api/trips/([^/]+)/share", path)
        if share_match:
            trip_id = urllib.parse.unquote(share_match.group(1)); records = _share_records(); changed = False
            for record in records:
                if record.get("trip_id") == trip_id and not record.get("revoked"):
                    record["revoked"] = True; record["revoked_at"] = time.time(); changed = True
            if changed: _save_share_records(records)
            self._json(200, {"revoked": changed, "trip_id": trip_id}); return
        flight_match = re.fullmatch(r"/api/trips/([^/]+)/flights/(\d+)", path)
        if flight_match:
            trip_id = urllib.parse.unquote(flight_match.group(1)); index = int(flight_match.group(2)); trips = load_trips()
            trip = next((item for item in trips if str(item.get("id")) == trip_id), None)
            if not trip:
                self._json(404, {"error": "trip_not_found"}); return
            flights = trip.get("flights") if isinstance(trip.get("flights"), list) else []
            if index < 0 or index >= len(flights):
                self._json(404, {"error": "flight_not_found"}); return
            deleted = flights.pop(index); trip["flights"] = flights; save_trips(trips)
            self._json(200, {"deleted": deleted, "trip": trip}); return
        if self.path.startswith("/api/events/"):
            try:
                index = int(urllib.parse.unquote(self.path[len("/api/events/"):]))
                events = load_events()
                if index < 0 or index >= len(events): self._json(404, {"error": "event_not_found"}); return
                deleted = events.pop(index); save_events(events); self._json(200, {"event": deleted})
            except ValueError: self._json(400, {"error": "invalid_event"})
            return
        prefix = "/api/trips/"
        if not self.path.startswith(prefix): self._json(404, {"error": "not_found"}); return
        self._json(200, {"trips": delete_trip(self.path[len(prefix):])})

def main():
    global HTTP_READY, EMAIL_READY
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    HTTP_READY = True
    EMAIL_READY = bool(EMAIL_INTAKE_SECRET)
    resume_pending_email_processing()
    threading.Thread(target=start_telegram_polling, daemon=True).start()
    print(f"TRIPY bot/API listening on http://127.0.0.1:{PORT}", flush=True)
    httpd.serve_forever()

if __name__ == "__main__": main()
