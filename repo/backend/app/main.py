import os
import smtplib
from email.mime.text import MIMEText
from email.header import Header

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
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
    body = (
        f"name: {payload.name}\n"
        f"email: {payload.email}\n"
        f"phone: {phone}\n"
        f"message:\n{payload.message}\n"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = smtp_user
    msg["To"] = EMAIL_TO

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
