import base64
import hashlib
import hmac
import json
import os
import smtplib
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
    recaptchaToken: str = Field(min_length=1)


EMAIL_TO = "edricding0108@gmail.com"
SESSION_COOKIE_NAME = "edricd_session"
SESSION_TTL_SECONDS = 30 * 60

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


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/recaptcha-sitekey")
def recaptcha_sitekey():
    site_key = os.getenv("RECAPTCHA_SITE_KEY", "").strip()
    return {"site_key": site_key}


@app.post("/api/AuthLogin")
def auth_login(payload: LoginPayload, request: FastAPIRequest):
    username = payload.username.strip()
    plain_password = payload.password
    recaptcha_token = payload.recaptchaToken

    if not username:
        return {"success": False, "message": "username is required"}
    if not plain_password or not plain_password.strip():
        return {"success": False, "message": "password is required"}

    client_ip = request.client.host if request.client else None
    try:
        verify_recaptcha(recaptcha_token, client_ip)
    except HTTPException as exc:
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
