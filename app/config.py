import asyncpg
from pydantic_settings import BaseSettings
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    database_url: str = os.getenv("DATABASE_URL", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    ultramsg_instance_id: str = os.getenv("ULTRAMSG_INSTANCE_ID", "")
    ultramsg_api_key: str = os.getenv("ULTRAMSG_API_KEY", "")
    app_env: str = os.getenv("APP_ENV", "development")
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret")

    class Config:
        env_file = ".env"

settings = Settings()

_pool: Optional[asyncpg.Pool] = None

async def get_db() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=10,
            command_timeout=60
        )
    return _pool

async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
