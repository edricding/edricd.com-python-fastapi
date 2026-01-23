import os
import smtplib
from pathlib import Path
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Environment, FileSystemLoader, select_autoescape

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

EMAIL_TO = "d.singine@gmail.com"  # 固定收件人

# --- Jinja2 模板加载（模板放在 app/templates/contact_email.html） ---
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"])
)

def render_contact_html(name: str, email: str, phone: str, message: str) -> str | None:
    """
    渲染 HTML 邮件模板。
    如果模板文件不存在/渲染失败，返回 None（会 fallback 到纯文本）。
    """
    try:
        template = jinja_env.get_template("contact_email.html")
        return template.render(name=name, email=email, phone=phone, message=message)
    except Exception:
        return None

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/contact")
def contact(payload: ContactPayload):
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")

    if not smtp_host or not smtp_user or not smtp_pass:
        raise HTTPException(status_code=500, detail="SMTP env not configured")

    phone = payload.phone.strip() if payload.phone and payload.phone.strip() else "-"

    subject = f"[edricd.com] New Contact Form - {payload.name}"

    # 纯文本版本（永远存在）
    text_body = (
        f"name: {payload.name}\n"
        f"email: {payload.email}\n"
        f"phone: {phone}\n"
        f"message:\n{payload.message}\n"
    )

    # HTML 版本（如果模板存在就用）
    html_body = render_contact_html(
        name=payload.name,
        email=payload.email,
        phone=phone,
        message=payload.message,
    )

    # 组合邮件：plain + html（推荐）
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SMTP send failed: {e}")

    return {"ok": True}
