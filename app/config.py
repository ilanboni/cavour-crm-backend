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
    voice_secret: str = os.getenv("VOICE_SECRET", "")

    class Config:
        env_file = ".env"

settings = Settings()

_pool: Optional[asyncpg.Pool] = None

async def crea_pool() -> asyncpg.Pool:
    """Apre il pool. statement_cache_size=0 serve con il pooler di Supabase:
    in transaction mode le query preparate non sopravvivono fra una chiamata e
    l'altra, e asyncpg le usa di default."""
    return await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=60,
        statement_cache_size=0,
    )


async def get_db() -> asyncpg.Pool:
    """Ritorna il pool, aprendolo al primo uso.

    Non solleva all'avvio: l'8 ottobre 2026 una password sbagliata in
    DATABASE_URL ha fatto morire il processo in partenza, Railway lo ha
    riavviato all'infinito e dopo qualche centinaio di tentativi Supabase ha
    bloccato le nuove connessioni ("too many authentication failures"). Il CRM
    e' rimasto giu' per ore e sistemare la variabile non bastava piu'.
    Meglio un'applicazione viva che risponde male su alcune rotte.
    """
    global _pool
    if _pool is None:
        _pool = await crea_pool()
    return _pool


async def prova_connessione(tentativi: int = 3, attesa: float = 3.0) -> bool:
    """Tentativi distanziati all'avvio. Se falliscono, l'app parte lo stesso."""
    import asyncio
    for n in range(1, tentativi + 1):
        try:
            await get_db()
            return True
        except Exception as exc:
            print(f"[DB] tentativo {n}/{tentativi} fallito: {exc}")
            if n < tentativi:
                await asyncio.sleep(attesa)
    print("[DB] nessuna connessione: l'app parte comunque, /health lo segnala")
    return False

async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
