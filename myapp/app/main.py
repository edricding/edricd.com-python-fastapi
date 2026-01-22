from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
)

# CORS（如果你是纯后端给前端域名用，建议只放你自己的域名）
if settings.CORS_ALLOW_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOW_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

@app.get("/health", tags=["system"])
def health():
    return {"ok": True, "service": settings.APP_NAME, "version": settings.APP_VERSION}

@app.get("/v1/hello", tags=["demo"])
def hello(name: str = "world"):
    return {"message": f"hello, {name}"}
