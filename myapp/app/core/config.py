from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List

class Settings(BaseSettings):
    APP_NAME: str = "edricd-fastapi"
    APP_VERSION: str = "0.1.0"

    # 逗号分隔也行：CORS_ALLOW_ORIGINS="https://edricd.com,https://www.edricd.com"
    CORS_ALLOW_ORIGINS: List[str] = Field(default_factory=list)

    # 让 Uvicorn 正确识别反代后的真实 IP / 协议 (http/https)
    FORWARDED_ALLOW_IPS: str = "*"

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
