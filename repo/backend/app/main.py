import base64
import hashlib
import hmac
import json
import os
import smtplib
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import bcrypt
import pymysql
from fastapi import FastAPI, HTTPException, Request as FastAPIRequest, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field
from pymysql.err import IntegrityError

from app.core.config import settings

app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

origins = settings.cors_allow_origins_list
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class ContactPayload(BaseModel):
    name: str
    email: str
    message: str
    phone: str | None = None
    captcha_token: str


class CreateUserPayload(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=255)
    role: str | None = None


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=255)
    recaptchaToken: str | None = None


class ReminderSlotSavePayload(BaseModel):
    id: int | None = Field(default=None, ge=1)
    weekday: int = Field(ge=1, le=7)
    start_min: int = Field(ge=0, le=1439)
    end_min: int = Field(ge=1, le=1440)
    title: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    audio_id: int | None = Field(default=None, ge=1)
    color: str | None = Field(default=None, max_length=20)
    is_enabled: bool = True
    sort_order: int | None = Field(default=None, ge=0, le=65535)


class ReminderSlotDeletePayload(BaseModel):
    id: int = Field(ge=1)


class ReminderPresetSavePayload(BaseModel):
    id: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=120)
    duration_min: int = Field(ge=1, le=1439)
    audio_id: int | None = Field(default=None, ge=1)
    color: str | None = Field(default=None, max_length=20)
    is_enabled: bool = True
    sort_order: int | None = Field(default=None, ge=0, le=65535)


class ReminderPresetDeletePayload(BaseModel):
    id: int = Field(ge=1)


class ReminderAudioSavePayload(BaseModel):
    id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, max_length=120)
    gcs_url: str = Field(min_length=1, max_length=1024)
    mime_type: str | None = Field(default=None, max_length=64)
    duration_seconds: int | None = Field(default=None, ge=0, le=65535)
    is_active: bool = True


class ReminderAudioDeletePayload(BaseModel):
    id: int = Field(ge=1)


EMAIL_TO = "edricding0108@gmail.com"
SESSION_COOKIE_NAME = "edricd_session"
SESSION_TTL_SECONDS = 30 * 60
WEEK_MINUTES = 7 * 24 * 60
DEVICE_LAST_EVENT_KEY: dict[str, str] = {}
DEVICE_LAST_EVENT_LOCK = Lock()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_contact_html(name: str, email: str, phone: str, message: str) -> str | None:
    try:
        template = jinja_env.get_template("contact_email.html")
        return template.render(name=name, email=email, phone=phone, message=message)
    except Exception:
        return None


def verify_recaptcha(token: str, remoteip: str | None = None) -> None:
    secret = os.getenv("RECAPTCHA_SECRET_KEY", "").strip()
    if not secret:
        raise HTTPException(status_code=500, detail="RECAPTCHA_SECRET_KEY not configured")
    if not token:
        raise HTTPException(status_code=400, detail="Captcha token missing")

    payload = {"secret": secret, "response": token}
    if remoteip:
        payload["remoteip"] = remoteip

    verify_urls = [
        "https://www.google.com/recaptcha/api/siteverify",
        "https://www.recaptcha.net/recaptcha/api/siteverify",
    ]
    result = None
    last_error = None

    for verify_url in verify_urls:
        req = Request(
            verify_url,
            data=urlencode(payload).encode("utf-8"),
            method="POST",
        )
        try:
            with urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:
            last_error = exc
            continue

    if result is None:
        raise HTTPException(status_code=502, detail=f"reCAPTCHA verify failed: {last_error}")

    if not result.get("success"):
        raise HTTPException(status_code=400, detail="Captcha verification failed")


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_login_recaptcha_required() -> bool:
    # Keep login available in environments where reCAPTCHA domains are blocked.
    return _bool_env("LOGIN_RECAPTCHA_REQUIRED", False)


def get_db_connection():
    db_host = os.getenv("DB_HOST", "mysql")
    db_port = int(os.getenv("DB_PORT", "3306"))
    db_name = os.getenv("DB_NAME", "edricd")
    db_user = os.getenv("DB_USER", "edricd")
    db_password = os.getenv("DB_PASSWORD", "")

    return pymysql.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        database=db_name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(
        plain_password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


def verify_password(plain_password: str, stored_password: str) -> bool:
    if not stored_password:
        return False

    # Compatible with bcrypt hashes and legacy plain-text records.
    if stored_password.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                stored_password.encode("utf-8"),
            )
        except ValueError:
            return False

    return plain_password == stored_password


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(text: str) -> bytes:
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _session_secret() -> str:
    return os.getenv("SESSION_SECRET_KEY", "change-this-session-secret")


def _session_cookie_secure(request: FastAPIRequest) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def build_session_token(username: str, expires_at: int) -> str:
    payload = {"username": username, "exp": expires_at}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64_encode(payload_bytes)
    signature = hmac.new(
        _session_secret().encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{payload_b64}.{_b64_encode(signature)}"


def parse_session_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None

    payload_b64, sig_b64 = token.split(".", 1)

    try:
        expected_sig = hmac.new(
            _session_secret().encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        provided_sig = _b64_decode(sig_b64)
    except Exception:
        return None

    if not hmac.compare_digest(expected_sig, provided_sig):
        return None

    try:
        payload = json.loads(_b64_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    exp = payload.get("exp")
    username = payload.get("username")
    if not isinstance(exp, int) or not isinstance(username, str):
        return None
    if exp <= int(time.time()):
        return None

    return payload


def set_session_cookie(response: Response, request: FastAPIRequest, username: str, expires_at: int) -> None:
    token = build_session_token(username=username, expires_at=expires_at)
    max_age = max(0, expires_at - int(time.time()))
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=_session_cookie_secure(request),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        samesite="lax",
    )


def get_session_payload(request: FastAPIRequest) -> dict | None:
    return parse_session_token(request.cookies.get(SESSION_COOKIE_NAME))


def unauthorized_response() -> JSONResponse:
    return JSONResponse({"success": False, "message": "unauthorized"}, status_code=401)


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def load_reminder_timezone(cursor) -> str:
    cursor.execute(
        """
        SELECT `timezone_name`
        FROM `reminder_schedule_config`
        WHERE `id` = 1
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    timezone_name = normalize_optional_text(row.get("timezone_name") if row else None)
    if timezone_name:
        return timezone_name

    timezone_name = "Asia/Shanghai"
    cursor.execute(
        """
        INSERT INTO `reminder_schedule_config` (`id`, `timezone_name`)
        VALUES (1, %s)
        ON DUPLICATE KEY UPDATE `timezone_name` = VALUES(`timezone_name`)
        """,
        (timezone_name,),
    )
    return timezone_name


def resolve_timezone(timezone_name: str) -> tuple[str, object]:
    normalized = normalize_optional_text(timezone_name) or "Asia/Shanghai"
    try:
        return normalized, ZoneInfo(normalized)
    except Exception:
        return "UTC", dt_timezone.utc


def reminder_slot_select_sql() -> str:
    return """
        SELECT
            s.`id`,
            s.`weekday`,
            s.`start_min`,
            s.`end_min`,
            s.`title`,
            s.`note`,
            s.`audio_id`,
            s.`color`,
            s.`is_enabled`,
            s.`sort_order`,
            a.`id` AS `audio_lib_id`,
            a.`name` AS `audio_name`,
            a.`gcs_url` AS `audio_url`,
            a.`mime_type` AS `audio_mime_type`,
            a.`duration_seconds` AS `audio_duration_seconds`
        FROM `reminder_schedule_slot` s
        LEFT JOIN `reminder_audio_library` a ON a.`id` = s.`audio_id`
    """


def reminder_audio_from_joined_row(row: dict) -> dict | None:
    if row.get("audio_lib_id") is None:
        return None
    return {
        "id": int(row["audio_lib_id"]),
        "name": row.get("audio_name"),
        "gcs_url": row.get("audio_url"),
        "mime_type": row.get("audio_mime_type"),
        "duration_seconds": row.get("audio_duration_seconds"),
    }


def reminder_audio_row_to_dict(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row.get("name") or "",
        "gcs_url": row.get("gcs_url"),
        "mime_type": row.get("mime_type"),
        "duration_seconds": row.get("duration_seconds"),
        "is_active": bool(row.get("is_active")),
    }


def derive_reminder_audio_name(name: str | None, gcs_url: str) -> str:
    normalized_name = normalize_optional_text(name)
    if normalized_name:
        return normalized_name[:120]

    parsed_path = ""
    try:
        parsed_path = urlparse(gcs_url).path or ""
    except Exception:
        parsed_path = ""

    candidate = parsed_path.rsplit("/", 1)[-1] if parsed_path else ""
    try:
        candidate = unquote(candidate)
    except Exception:
        pass
    candidate = normalize_optional_text(candidate)
    if candidate:
        return candidate[:120]

    return "Untitled Audio"


def fetch_reminder_audio_by_id(cursor, audio_id: int) -> dict | None:
    cursor.execute(
        """
        SELECT
            `id`,
            `name`,
            `gcs_url`,
            `mime_type`,
            `duration_seconds`,
            `is_active`
        FROM `reminder_audio_library`
        WHERE `id` = %s
        LIMIT 1
        """,
        (audio_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return reminder_audio_row_to_dict(row)


def reminder_slot_row_to_dict(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "weekday": int(row["weekday"]),
        "start_min": int(row["start_min"]),
        "end_min": int(row["end_min"]),
        "title": row.get("title") or "",
        "note": row.get("note"),
        "audio_id": row.get("audio_id"),
        "color": row.get("color"),
        "is_enabled": bool(row.get("is_enabled")),
        "sort_order": int(row.get("sort_order") or 0),
        "audio": reminder_audio_from_joined_row(row),
    }


def reminder_preset_select_sql() -> str:
    return """
        SELECT
            p.`id`,
            p.`name`,
            p.`duration_min`,
            p.`audio_id`,
            p.`color`,
            p.`is_enabled`,
            p.`sort_order`,
            a.`id` AS `audio_lib_id`,
            a.`name` AS `audio_name`,
            a.`gcs_url` AS `audio_url`,
            a.`mime_type` AS `audio_mime_type`,
            a.`duration_seconds` AS `audio_duration_seconds`
        FROM `reminder_preset` p
        LEFT JOIN `reminder_audio_library` a ON a.`id` = p.`audio_id`
    """


def reminder_preset_row_to_dict(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row.get("name") or "",
        "duration_min": int(row.get("duration_min") or 0),
        "audio_id": row.get("audio_id"),
        "color": row.get("color"),
        "is_enabled": bool(row.get("is_enabled")),
        "sort_order": int(row.get("sort_order") or 0),
        "audio": reminder_audio_from_joined_row(row),
    }


def fetch_reminder_preset_by_id(cursor, preset_id: int) -> dict | None:
    cursor.execute(
        reminder_preset_select_sql()
        + """
          WHERE p.`id` = %s
          LIMIT 1
        """,
        (preset_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return reminder_preset_row_to_dict(row)


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SHOW TABLES LIKE %s
        """,
        (table_name,),
    )
    return cursor.fetchone() is not None


def build_fallback_presets_from_slots(slot_rows: list[dict]) -> list[dict]:
    presets = []
    for row in slot_rows:
        start_min = int(row.get("start_min") or 0)
        end_min = int(row.get("end_min") or 0)
        duration_min = end_min - start_min
        if duration_min <= 0:
            continue
        if not bool(row.get("is_enabled")):
            continue

        slot_id = int(row["id"])
        presets.append(
            {
                "id": f"slot-{slot_id}",
                "name": row.get("title") or "",
                "duration_min": duration_min,
                "audio_id": row.get("audio_id"),
                "color": row.get("color"),
                "is_enabled": True,
                "sort_order": int(row.get("sort_order") or start_min),
                "audio": reminder_audio_from_joined_row(row),
                "source_slot_id": slot_id,
                "is_fallback": True,
            }
        )
    return presets


def fetch_reminder_slot_by_id(cursor, slot_id: int) -> dict | None:
    cursor.execute(
        reminder_slot_select_sql()
        + """
          WHERE s.`id` = %s
          LIMIT 1
        """,
        (slot_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return reminder_slot_row_to_dict(row)


def has_reminder_slot_overlap(
    cursor,
    weekday: int,
    start_min: int,
    end_min: int,
    exclude_id: int | None = None,
) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM `reminder_schedule_slot`
        WHERE `weekday` = %s
          AND `is_enabled` = 1
          AND NOT (%s <= `start_min` OR %s >= `end_min`)
          AND (%s IS NULL OR `id` <> %s)
        LIMIT 1
        """,
        (
            weekday,
            end_min,
            start_min,
            exclude_id,
            exclude_id,
        ),
    )
    return cursor.fetchone() is not None


def minute_to_hhmm(minute_of_day: int) -> str:
    hour = minute_of_day // 60
    minute = minute_of_day % 60
    return f"{hour:02d}:{minute:02d}"


def resolve_current_and_next_slot(
    slots: list[dict],
    weekday: int,
    minute_of_day: int,
) -> tuple[dict | None, dict | None, int | None]:
    now_week_minute = (weekday - 1) * 1440 + minute_of_day
    current_slot = None
    next_slot = None
    min_delta = None

    for slot in slots:
        if slot["weekday"] == weekday and slot["start_min"] <= minute_of_day < slot["end_min"]:
            current_slot = slot

        slot_week_minute = (slot["weekday"] - 1) * 1440 + slot["start_min"]
        delta = slot_week_minute - now_week_minute
        if delta <= 0:
            delta += WEEK_MINUTES
        if min_delta is None or delta < min_delta:
            min_delta = delta
            next_slot = slot

    return current_slot, next_slot, min_delta


def build_event_occurrence_key(slot: dict, now_dt: datetime) -> str:
    week_start_date = now_dt.date() - timedelta(days=now_dt.isoweekday() - 1)
    event_date = week_start_date + timedelta(days=int(slot["weekday"]) - 1)
    return (
        f"{int(slot['id'])}:{event_date.isoformat()}:"
        f"{int(slot['start_min'])}:{int(slot['end_min'])}"
    )


def is_first_time_for_device_event(device_id: str, event_key: str) -> bool:
    with DEVICE_LAST_EVENT_LOCK:
        last_event_key = DEVICE_LAST_EVENT_KEY.get(device_id)
        is_first_time = last_event_key != event_key
        if is_first_time:
            DEVICE_LAST_EVENT_KEY[device_id] = event_key
        return is_first_time


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/recaptcha-sitekey")
def recaptcha_sitekey():
    site_key = os.getenv("RECAPTCHA_SITE_KEY", "").strip()
    return {
        "site_key": site_key,
        "login_required": is_login_recaptcha_required(),
    }


@app.post("/api/AuthLogin")
def auth_login(payload: LoginPayload, request: FastAPIRequest):
    username = payload.username.strip()
    plain_password = payload.password
    recaptcha_token = (payload.recaptchaToken or "").strip()
    recaptcha_required = is_login_recaptcha_required()

    if not username:
        return {"success": False, "message": "username is required"}
    if not plain_password or not plain_password.strip():
        return {"success": False, "message": "password is required"}

    client_ip = request.client.host if request.client else None
    if recaptcha_required and not recaptcha_token:
        return {"success": False, "message": "Captcha token missing"}

    if recaptcha_token:
        try:
            verify_recaptcha(recaptcha_token, client_ip)
        except HTTPException as exc:
            if recaptcha_required:
                return {"success": False, "message": str(exc.detail)}

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT `id`, `username`, `password`
                    FROM `user`
                    WHERE `username` = %s
                    LIMIT 1
                    """,
                    (username,),
                )
                user = cursor.fetchone()

                if not user:
                    return {"success": False, "message": "Invalid username or password"}

                if not verify_password(plain_password, user.get("password", "")):
                    return {"success": False, "message": "Invalid username or password"}

                cursor.execute(
                    """
                    UPDATE `user`
                    SET `last_login_time` = NOW()
                    WHERE `id` = %s
                    """,
                    (user["id"],),
                )
    except Exception as exc:
        return {"success": False, "message": f"login failed: {exc}"}

    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    response = JSONResponse(
        {
            "success": True,
            "message": "Login success",
            "username": username,
            "expiresAt": expires_at,
        }
    )
    set_session_cookie(response, request, username=username, expires_at=expires_at)
    return response


@app.post("/api/AuthLogout")
def auth_logout():
    response = JSONResponse({"success": True, "message": "Logout success"})
    clear_session_cookie(response)
    return response


@app.get("/api/session/status")
def session_status(request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return {"loggedIn": False}

    username = session_payload["username"]
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    response = JSONResponse(
        {
            "loggedIn": True,
            "username": username,
            "expiresAt": expires_at,
        }
    )
    set_session_cookie(response, request, username=username, expires_at=expires_at)
    return response


@app.get("/api/session/require")
def session_require(request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return Response(status_code=401)

    username = session_payload["username"]
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    response = Response(status_code=204)
    set_session_cookie(response, request, username=username, expires_at=expires_at)
    return response


@app.get("/api/reminder/schedule")
def reminder_schedule(request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                timezone_name = load_reminder_timezone(cursor)

                cursor.execute(
                    reminder_slot_select_sql()
                    + """
                    ORDER BY s.`weekday` ASC, s.`start_min` ASC, s.`sort_order` ASC, s.`id` ASC
                    """
                )
                slot_rows = cursor.fetchall()

                if table_exists(cursor, "reminder_audio_library"):
                    cursor.execute(
                        """
                        SELECT
                            `id`,
                            `name`,
                            `gcs_url`,
                            `mime_type`,
                            `duration_seconds`,
                            `is_active`
                        FROM `reminder_audio_library`
                        ORDER BY `is_active` DESC, `id` DESC
                        """
                    )
                    audio_rows = cursor.fetchall()
                else:
                    audio_rows = []

                if table_exists(cursor, "reminder_preset"):
                    cursor.execute(
                        reminder_preset_select_sql()
                        + """
                        ORDER BY p.`is_enabled` DESC, p.`sort_order` ASC, p.`id` ASC
                        """
                    )
                    preset_rows = cursor.fetchall()
                    presets = [reminder_preset_row_to_dict(row) for row in preset_rows]
                else:
                    presets = build_fallback_presets_from_slots(slot_rows)
    except Exception as exc:
        return {"success": False, "message": f"query reminder schedule failed: {exc}"}

    return {
        "success": True,
        "message": "ok",
        "data": {
            "timezone": timezone_name,
            "slots": [reminder_slot_row_to_dict(row) for row in slot_rows],
            "audios": [reminder_audio_row_to_dict(row) for row in audio_rows],
            "presets": presets,
        },
    }


@app.post("/api/reminder/slot/save")
def reminder_slot_save(payload: ReminderSlotSavePayload, request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    title = payload.title.strip()
    if not title:
        return {"success": False, "message": "title is required"}
    if payload.start_min >= payload.end_min:
        return {"success": False, "message": "start_min must be less than end_min"}

    note = normalize_optional_text(payload.note)
    color = normalize_optional_text(payload.color)
    audio_id = payload.audio_id
    is_enabled = 1 if payload.is_enabled else 0

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if audio_id is not None:
                    cursor.execute(
                        """
                        SELECT `id`
                        FROM `reminder_audio_library`
                        WHERE `id` = %s
                        LIMIT 1
                        """,
                        (audio_id,),
                    )
                    if not cursor.fetchone():
                        return {"success": False, "message": "audio_id not found"}

                if payload.id is not None:
                    cursor.execute(
                        """
                        SELECT `id`, `sort_order`
                        FROM `reminder_schedule_slot`
                        WHERE `id` = %s
                        LIMIT 1
                        """,
                        (payload.id,),
                    )
                    current_slot = cursor.fetchone()
                    if not current_slot:
                        return {"success": False, "message": "slot not found"}

                    if is_enabled == 1 and has_reminder_slot_overlap(
                        cursor,
                        payload.weekday,
                        payload.start_min,
                        payload.end_min,
                        payload.id,
                    ):
                        return {
                            "success": False,
                            "message": "time slot overlaps with existing slot",
                        }

                    sort_order = (
                        payload.sort_order
                        if payload.sort_order is not None
                        else int(current_slot.get("sort_order") or payload.start_min)
                    )
                    cursor.execute(
                        """
                        UPDATE `reminder_schedule_slot`
                        SET
                            `weekday` = %s,
                            `start_min` = %s,
                            `end_min` = %s,
                            `title` = %s,
                            `note` = %s,
                            `audio_id` = %s,
                            `color` = %s,
                            `is_enabled` = %s,
                            `sort_order` = %s
                        WHERE `id` = %s
                        """,
                        (
                            payload.weekday,
                            payload.start_min,
                            payload.end_min,
                            title,
                            note,
                            audio_id,
                            color,
                            is_enabled,
                            sort_order,
                            payload.id,
                        ),
                    )
                    slot_id = payload.id
                else:
                    if is_enabled == 1 and has_reminder_slot_overlap(
                        cursor,
                        payload.weekday,
                        payload.start_min,
                        payload.end_min,
                    ):
                        return {
                            "success": False,
                            "message": "time slot overlaps with existing slot",
                        }

                    sort_order = (
                        payload.sort_order
                        if payload.sort_order is not None
                        else payload.start_min
                    )
                    cursor.execute(
                        """
                        INSERT INTO `reminder_schedule_slot`
                            (`weekday`, `start_min`, `end_min`, `title`, `note`, `audio_id`, `color`, `is_enabled`, `sort_order`)
                        VALUES
                            (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            payload.weekday,
                            payload.start_min,
                            payload.end_min,
                            title,
                            note,
                            audio_id,
                            color,
                            is_enabled,
                            sort_order,
                        ),
                    )
                    slot_id = int(cursor.lastrowid)

                slot = fetch_reminder_slot_by_id(cursor, int(slot_id))
    except Exception as exc:
        return {"success": False, "message": f"save reminder slot failed: {exc}"}

    if not slot:
        return {"success": False, "message": "slot not found after save"}

    return {
        "success": True,
        "message": "slot saved",
        "data": slot,
    }


@app.post("/api/reminder/slot/delete")
def reminder_slot_delete(payload: ReminderSlotDeletePayload, request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM `reminder_schedule_slot`
                    WHERE `id` = %s
                    LIMIT 1
                    """,
                    (payload.id,),
                )
                deleted_count = cursor.rowcount
    except Exception as exc:
        return {"success": False, "message": f"delete reminder slot failed: {exc}"}

    if deleted_count <= 0:
        return {"success": False, "message": "slot not found"}

    return {"success": True, "message": "slot deleted"}


@app.get("/api/reminder/preset/list")
def reminder_preset_list(request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if not table_exists(cursor, "reminder_preset"):
                    return {
                        "success": True,
                        "message": "preset table not ready",
                        "data": [],
                    }

                cursor.execute(
                    reminder_preset_select_sql()
                    + """
                    ORDER BY p.`is_enabled` DESC, p.`sort_order` ASC, p.`id` ASC
                    """
                )
                rows = cursor.fetchall()
    except Exception as exc:
        return {"success": False, "message": f"query reminder presets failed: {exc}"}

    return {
        "success": True,
        "message": "ok",
        "data": [reminder_preset_row_to_dict(row) for row in rows],
    }


@app.post("/api/reminder/preset/save")
def reminder_preset_save(payload: ReminderPresetSavePayload, request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    name = payload.name.strip()
    if not name:
        return {"success": False, "message": "name is required"}

    color = normalize_optional_text(payload.color)
    audio_id = payload.audio_id
    is_enabled = 1 if payload.is_enabled else 0

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if not table_exists(cursor, "reminder_preset"):
                    return {"success": False, "message": "reminder_preset table not found"}

                if audio_id is not None:
                    cursor.execute(
                        """
                        SELECT `id`
                        FROM `reminder_audio_library`
                        WHERE `id` = %s
                        LIMIT 1
                        """,
                        (audio_id,),
                    )
                    if not cursor.fetchone():
                        return {"success": False, "message": "audio_id not found"}

                if payload.id is not None:
                    cursor.execute(
                        """
                        SELECT `id`, `sort_order`
                        FROM `reminder_preset`
                        WHERE `id` = %s
                        LIMIT 1
                        """,
                        (payload.id,),
                    )
                    current_preset = cursor.fetchone()
                    if not current_preset:
                        return {"success": False, "message": "preset not found"}

                    sort_order = (
                        payload.sort_order
                        if payload.sort_order is not None
                        else int(current_preset.get("sort_order") or 0)
                    )
                    cursor.execute(
                        """
                        UPDATE `reminder_preset`
                        SET
                            `name` = %s,
                            `duration_min` = %s,
                            `audio_id` = %s,
                            `color` = %s,
                            `is_enabled` = %s,
                            `sort_order` = %s
                        WHERE `id` = %s
                        """,
                        (
                            name,
                            payload.duration_min,
                            audio_id,
                            color,
                            is_enabled,
                            sort_order,
                            payload.id,
                        ),
                    )
                    preset_id = payload.id
                else:
                    if payload.sort_order is not None:
                        sort_order = payload.sort_order
                    else:
                        cursor.execute(
                            """
                            SELECT COALESCE(MAX(`sort_order`), -1) + 1 AS `next_sort_order`
                            FROM `reminder_preset`
                            """
                        )
                        row = cursor.fetchone() or {}
                        sort_order = int(row.get("next_sort_order") or 0)

                    cursor.execute(
                        """
                        INSERT INTO `reminder_preset`
                            (`name`, `duration_min`, `audio_id`, `color`, `is_enabled`, `sort_order`)
                        VALUES
                            (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            name,
                            payload.duration_min,
                            audio_id,
                            color,
                            is_enabled,
                            sort_order,
                        ),
                    )
                    preset_id = int(cursor.lastrowid)

                preset = fetch_reminder_preset_by_id(cursor, int(preset_id))
    except Exception as exc:
        return {"success": False, "message": f"save reminder preset failed: {exc}"}

    if not preset:
        return {"success": False, "message": "preset not found after save"}

    return {
        "success": True,
        "message": "preset saved",
        "data": preset,
    }


@app.post("/api/reminder/preset/delete")
def reminder_preset_delete(payload: ReminderPresetDeletePayload, request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if not table_exists(cursor, "reminder_preset"):
                    return {"success": False, "message": "reminder_preset table not found"}

                cursor.execute(
                    """
                    DELETE FROM `reminder_preset`
                    WHERE `id` = %s
                    LIMIT 1
                    """,
                    (payload.id,),
                )
                deleted_count = cursor.rowcount
    except Exception as exc:
        return {"success": False, "message": f"delete reminder preset failed: {exc}"}

    if deleted_count <= 0:
        return {"success": False, "message": "preset not found"}

    return {"success": True, "message": "preset deleted"}


@app.get("/api/reminder/audio/list")
def reminder_audio_list(request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if not table_exists(cursor, "reminder_audio_library"):
                    return {
                        "success": True,
                        "message": "audio table not ready",
                        "data": [],
                    }

                cursor.execute(
                    """
                    SELECT
                        `id`,
                        `name`,
                        `gcs_url`,
                        `mime_type`,
                        `duration_seconds`,
                        `is_active`
                    FROM `reminder_audio_library`
                    ORDER BY `is_active` DESC, `id` DESC
                    """
                )
                rows = cursor.fetchall()
    except Exception as exc:
        return {"success": False, "message": f"query reminder audios failed: {exc}"}

    return {
        "success": True,
        "message": "ok",
        "data": [reminder_audio_row_to_dict(row) for row in rows],
    }


@app.post("/api/reminder/audio/save")
def reminder_audio_save(payload: ReminderAudioSavePayload, request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    gcs_url = normalize_optional_text(payload.gcs_url)
    if not gcs_url:
        return {"success": False, "message": "gcs_url is required"}

    name = derive_reminder_audio_name(payload.name, gcs_url)
    mime_type = normalize_optional_text(payload.mime_type)
    duration_seconds = payload.duration_seconds
    is_active = 1 if payload.is_active else 0

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if not table_exists(cursor, "reminder_audio_library"):
                    return {"success": False, "message": "reminder_audio_library table not found"}

                if payload.id is not None:
                    cursor.execute(
                        """
                        SELECT `id`
                        FROM `reminder_audio_library`
                        WHERE `id` = %s
                        LIMIT 1
                        """,
                        (payload.id,),
                    )
                    if not cursor.fetchone():
                        return {"success": False, "message": "audio not found"}

                    cursor.execute(
                        """
                        UPDATE `reminder_audio_library`
                        SET
                            `name` = %s,
                            `gcs_url` = %s,
                            `mime_type` = %s,
                            `duration_seconds` = %s,
                            `is_active` = %s
                        WHERE `id` = %s
                        """,
                        (
                            name,
                            gcs_url,
                            mime_type,
                            duration_seconds,
                            is_active,
                            payload.id,
                        ),
                    )
                    audio_id = payload.id
                else:
                    cursor.execute(
                        """
                        INSERT INTO `reminder_audio_library`
                            (`name`, `gcs_url`, `mime_type`, `duration_seconds`, `is_active`)
                        VALUES
                            (%s, %s, %s, %s, %s)
                        """,
                        (
                            name,
                            gcs_url,
                            mime_type,
                            duration_seconds,
                            is_active,
                        ),
                    )
                    audio_id = int(cursor.lastrowid)

                audio = fetch_reminder_audio_by_id(cursor, int(audio_id))
    except IntegrityError as exc:
        if exc.args and len(exc.args) > 0 and exc.args[0] == 1062:
            return {"success": False, "message": "audio URL already exists"}
        return {"success": False, "message": f"database integrity error: {exc}"}
    except Exception as exc:
        return {"success": False, "message": f"save reminder audio failed: {exc}"}

    if not audio:
        return {"success": False, "message": "audio not found after save"}

    return {
        "success": True,
        "message": "audio saved",
        "data": audio,
    }


@app.post("/api/reminder/audio/delete")
def reminder_audio_delete(payload: ReminderAudioDeletePayload, request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if not table_exists(cursor, "reminder_audio_library"):
                    return {"success": False, "message": "reminder_audio_library table not found"}

                if table_exists(cursor, "reminder_preset"):
                    cursor.execute(
                        """
                        UPDATE `reminder_preset`
                        SET `audio_id` = NULL
                        WHERE `audio_id` = %s
                        """,
                        (payload.id,),
                    )

                cursor.execute(
                    """
                    DELETE FROM `reminder_audio_library`
                    WHERE `id` = %s
                    LIMIT 1
                    """,
                    (payload.id,),
                )
                deleted_count = cursor.rowcount
    except Exception as exc:
        return {"success": False, "message": f"delete reminder audio failed: {exc}"}

    if deleted_count <= 0:
        return {"success": False, "message": "audio not found"}

    return {"success": True, "message": "audio deleted"}


@app.get("/api/reminder/current")
def reminder_current(request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return unauthorized_response()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                timezone_name = load_reminder_timezone(cursor)
                normalized_timezone, tzinfo = resolve_timezone(timezone_name)
                now_dt = datetime.now(tzinfo)
                weekday = now_dt.isoweekday()
                minute_of_day = now_dt.hour * 60 + now_dt.minute

                cursor.execute(
                    reminder_slot_select_sql()
                    + """
                    WHERE s.`is_enabled` = 1
                    ORDER BY s.`weekday` ASC, s.`start_min` ASC, s.`sort_order` ASC, s.`id` ASC
                    """
                )
                slot_rows = cursor.fetchall()
    except Exception as exc:
        return {"success": False, "message": f"query current reminder failed: {exc}"}

    slots = [reminder_slot_row_to_dict(row) for row in slot_rows]
    current_slot, next_slot, min_delta = resolve_current_and_next_slot(
        slots,
        weekday,
        minute_of_day,
    )

    return {
        "success": True,
        "message": "ok",
        "data": {
            "timezone": normalized_timezone,
            "server_now": now_dt.isoformat(),
            "weekday": weekday,
            "minute_of_day": minute_of_day,
            "hhmm": minute_to_hhmm(minute_of_day),
            "current_slot": current_slot,
            "next_slot": next_slot,
            "minutes_until_next": min_delta,
        },
    }


@app.get("/api/reminder/device/current")
def reminder_device_current(request: FastAPIRequest, device_id: str | None = None):
    normalized_device_id = (
        normalize_optional_text(device_id)
        or normalize_optional_text(request.headers.get("x-device-id"))
        or "default-device"
    )

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                timezone_name = load_reminder_timezone(cursor)
                normalized_timezone, tzinfo = resolve_timezone(timezone_name)
                now_dt = datetime.now(tzinfo)
                weekday = now_dt.isoweekday()
                minute_of_day = now_dt.hour * 60 + now_dt.minute

                cursor.execute(
                    reminder_slot_select_sql()
                    + """
                    WHERE s.`is_enabled` = 1
                    ORDER BY s.`weekday` ASC, s.`start_min` ASC, s.`sort_order` ASC, s.`id` ASC
                    """
                )
                slot_rows = cursor.fetchall()
    except Exception as exc:
        return {"success": False, "message": f"query device current reminder failed: {exc}"}

    slots = [reminder_slot_row_to_dict(row) for row in slot_rows]
    current_slot, _, _ = resolve_current_and_next_slot(slots, weekday, minute_of_day)

    event = None
    is_first_time = False

    if current_slot:
        audio = current_slot.get("audio") if isinstance(current_slot.get("audio"), dict) else None
        audio_url = normalize_optional_text(audio.get("gcs_url")) if audio else None
        event_key = build_event_occurrence_key(current_slot, now_dt)
        is_first_time = is_first_time_for_device_event(normalized_device_id, event_key)
        event = {
            "id": int(current_slot["id"]),
            "name": current_slot.get("title") or "",
            "audio_url": audio_url,
            "weekday": int(current_slot["weekday"]),
            "start_min": int(current_slot["start_min"]),
            "end_min": int(current_slot["end_min"]),
            "hhmm_start": minute_to_hhmm(int(current_slot["start_min"])),
            "hhmm_end": minute_to_hhmm(int(current_slot["end_min"])),
        }

    return {
        "success": True,
        "message": "ok",
        "data": {
            "device_id": normalized_device_id,
            "timezone": normalized_timezone,
            "server_now": now_dt.isoformat(),
            "event": event,
            "is_first_time": is_first_time,
        },
    }


@app.get("/api/users")
def list_users(request: FastAPIRequest):
    session_payload = get_session_payload(request)
    if not session_payload:
        return JSONResponse(
            {"success": False, "message": "unauthorized"},
            status_code=401,
        )

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT `id`, `username`, `last_login_time`
                    FROM `user`
                    ORDER BY `id` DESC
                    """
                )
                rows = cursor.fetchall()
    except Exception as exc:
        return {"success": False, "message": f"query users failed: {exc}"}

    users: list[dict] = []
    for row in rows:
        last_login_time = row.get("last_login_time")
        users.append(
            {
                "id": row.get("id"),
                "username": row.get("username"),
                "last_login_time": (
                    last_login_time.strftime("%Y-%m-%d %H:%M:%S")
                    if last_login_time
                    else None
                ),
            }
        )

    return {
        "success": True,
        "message": "ok",
        "columns": ["id", "username", "last_login_time"],
        "data": users,
    }


@app.post("/api/users/create")
def create_user(payload: CreateUserPayload):
    username = payload.username.strip()
    plain_password = payload.password

    if not username:
        return {"success": False, "message": "username is required"}
    if not plain_password or not plain_password.strip():
        return {"success": False, "message": "password is required"}

    hashed_password = hash_password(plain_password)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO `user` (`username`, `password`, `last_login_time`)
                    VALUES (%s, %s, NULL)
                    """,
                    (username, hashed_password),
                )
                new_id = cursor.lastrowid
        return {"success": True, "message": "user created", "data": {"id": new_id}}
    except IntegrityError as exc:
        # 1062 = duplicate entry for unique key
        if exc.args and len(exc.args) > 0 and exc.args[0] == 1062:
            return {"success": False, "message": "username already exists"}
        return {"success": False, "message": f"database integrity error: {exc}"}
    except Exception as exc:
        return {"success": False, "message": f"create user failed: {exc}"}


@app.post("/api/contact")
def contact(payload: ContactPayload, request: FastAPIRequest):
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")

    if not smtp_host or not smtp_user or not smtp_pass:
        raise HTTPException(status_code=500, detail="SMTP env not configured")

    client_ip = request.client.host if request.client else None
    verify_recaptcha(payload.captcha_token, client_ip)

    phone = payload.phone.strip() if payload.phone and payload.phone.strip() else "-"
    subject = f"[edricd.com] New Contact Form - {payload.name}"

    text_body = (
        f"name: {payload.name}\n"
        f"email: {payload.email}\n"
        f"phone: {phone}\n"
        f"message:\n{payload.message}\n"
    )

    html_body = render_contact_html(
        name=payload.name,
        email=payload.email,
        phone=phone,
        message=payload.message,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = smtp_user
    msg["To"] = EMAIL_TO

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [EMAIL_TO], msg.as_string())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SMTP send failed: {exc}")

    return {"ok": True}
