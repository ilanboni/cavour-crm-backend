import asyncpg
import json
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

# Quando aprire il pool fallisce, non si ritenta a ogni richiesta.
PAUSA_RICONNESSIONE = 10.0  # secondi
_ultimo_tentativo_pool: float = -1e9

def _json_in(valore):
    """Codifica verso jsonb. Chi passa giá una stringa (molte query fanno
    json.dumps a mano e poi $n::jsonb) la vedrebbe incapsulata una seconda
    volta: la lasciamo passare come sta."""
    if isinstance(valore, str) or valore is None:
        return valore
    return json.dumps(valore, ensure_ascii=False)


async def _registra_codec(conn: asyncpg.Connection) -> None:
    """Senza questo asyncpg restituisce jsonb come STRINGA, non come lista o
    dizionario. Il 09/11/2026 era il motivo per cui cliccare un immobile in
    Caccia non apriva nulla: il pannello faceva lista_agenzie.join(", ") su
    una stringa, React lanciava TypeError in fase di render e smontava tutta
    la pagina. L'API rispondeva 200, quindi nei log non si vedeva niente."""
    for tipo in ("json", "jsonb"):
        await conn.set_type_codec(
            tipo,
            encoder=_json_in,
            decoder=json.loads,
            schema="pg_catalog",
        )


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
        init=_registra_codec,
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
    global _pool, _ultimo_tentativo_pool
    if _pool is not None:
        return _pool
    # Pausa fra i tentativi di aprire il pool. Senza, ogni richiesta che arriva
    # mentre il database e' giu' prova a riconnettersi: e' esattamente la
    # tempesta che l'8 ottobre ha portato Supabase a rispondere "too many
    # authentication failures" e ha reso inutile sistemare la variabile.
    import time as _t
    adesso = _t.monotonic()
    if adesso - _ultimo_tentativo_pool < PAUSA_RICONNESSIONE:
        raise ConnectionError(
            "database non raggiungibile: nuovo tentativo fra "
            f"{PAUSA_RICONNESSIONE - (adesso - _ultimo_tentativo_pool):.0f}s"
        )
    _ultimo_tentativo_pool = adesso
    _pool = await crea_pool()
    return _pool


async def prova_connessione(tentativi: int = 3, attesa: float = 3.0) -> bool:
    """Tentativi distanziati all'avvio. Se falliscono, l'app parte lo stesso."""
    import asyncio
    global _ultimo_tentativo_pool
    for n in range(1, tentativi + 1):
        try:
            # L'avvio scavalca la pausa di riconnessione: i tentativi qui sono
            # pochi, distanziati e voluti, e senza questa riga il secondo e il
            # terzo verrebbero rifiutati dalla pausa invece di provare davvero.
            _ultimo_tentativo_pool = -1e9
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
