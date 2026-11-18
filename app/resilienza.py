"""Il CRM non deve diventare bianco quando il database non risponde.

PERCHE' ESISTE
--------------
Il 18 novembre 2026 Supabase e' rimasto irraggiungibile per circa due ore. Il
sito pubblico non se ne e' accorto, perche' serve l'ultimo elenco buono tenuto
in memoria. Il CRM invece mostrava schermate vuote e messaggi di errore, e non
c'era modo di sapere se un cliente non avesse appuntamenti o se fosse il
database a non rispondere. Sono due cose molto diverse per chi lavora.

COSA FA
-------
Tiene in memoria l'ultima risposta buona di ogni GET. Quando la stessa rotta
torna a fallire per un problema di database, serve quella copia con due
intestazioni:

    X-Degradato: 1
    X-Dati-Vecchi: <istante ISO della copia>

Il frontend le legge e mostra una fascia "dati di N minuti fa, il database non
risponde" sopra i dati veri, invece del vuoto. Se non c'e' nessuna copia, la
risposta e' un 503 con un messaggio in italiano al posto di una traccia di
stack: almeno si capisce di chi e' la colpa.

LIMITI VOLUTI
-------------
- Solo GET. Una scrittura non si finge riuscita: se non arriva al database deve
  fallire, e l'utente deve saperlo.
- Nessuna copia piu' vecchia di SCADENZA_SNAPSHOT. Un dato di ieri presentato
  come "di poco fa" e' peggio di una pagina vuota.
- Al massimo MAX_VOCI rotte e MAX_BYTE per risposta: questo gira su un
  contenitore con memoria contata, e una cache senza tetto e' il prossimo
  guasto.
- La copia vive nel processo e si perde al riavvio. Va bene: al riavvio il
  database di solito c'e'.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse, Response

MAX_VOCI = 300
MAX_BYTE = 512 * 1024
SCADENZA_SNAPSHOT = 6 * 3600  # sei ore

# chiave rotta -> (istante, corpo, content_type)
_ultima_buona: dict[str, tuple[float, bytes, str]] = {}

# Frammenti che compaiono quando il problema e' il database o il pooler, non la
# nostra logica. Raccolti dai log veri del 18 novembre.
_SEGNALI_DB = (
    "connection", "connessione", "timeout", "pool", "terminated",
    "server disconnected", "cannot connect", "too many",
    "authentication", "schema cache", "521", "522",
    "connection reset", "closed", "unavailable", "refused",
)


def _e_problema_di_database(exc: BaseException) -> bool:
    """Vero se l'eccezione somiglia a un guasto di database, non a un bug.

    Si guarda prima il tipo, perche' e' affidabile, e solo dopo il testo.
    """
    import asyncio
    try:
        import asyncpg
        if isinstance(exc, (asyncpg.PostgresConnectionError, asyncpg.InterfaceError,
                            asyncpg.TooManyConnectionsError)):
            return True
    except Exception:
        pass
    if isinstance(exc, (asyncio.TimeoutError, OSError, ConnectionError)):
        return True
    testo = f"{type(exc).__name__} {exc}".lower()
    return any(s in testo for s in _SEGNALI_DB)


def _chiave(request: Request) -> str:
    q = request.url.query
    return request.url.path + ("?" + q if q else "")


def _ricorda(chiave: str, corpo: bytes, content_type: str) -> None:
    if len(corpo) > MAX_BYTE:
        return
    if chiave not in _ultima_buona and len(_ultima_buona) >= MAX_VOCI:
        # Butta la copia piu' vecchia: serve un tetto, non una politica fine.
        piu_vecchia = min(_ultima_buona, key=lambda k: _ultima_buona[k][0])
        _ultima_buona.pop(piu_vecchia, None)
    _ultima_buona[chiave] = (time.time(), corpo, content_type)


def _ripiego(chiave: str, motivo: str) -> Response:
    voce = _ultima_buona.get(chiave)
    if voce and (time.time() - voce[0]) <= SCADENZA_SNAPSHOT:
        quando, corpo, content_type = voce
        iso = datetime.fromtimestamp(quando, timezone.utc).isoformat()
        return Response(
            content=corpo,
            status_code=200,
            media_type=content_type,
            headers={"X-Degradato": "1", "X-Dati-Vecchi": iso,
                     "Cache-Control": "no-store"},
        )
    return JSONResponse(
        status_code=503,
        content={
            "errore": "database_non_raggiungibile",
            "messaggio": "Il database non risponde. I dati di questa pagina non "
                         "sono disponibili e non ne ho una copia recente. "
                         "Riprova fra qualche minuto.",
            "dettaglio": motivo[:300],
        },
        headers={"X-Degradato": "1", "Cache-Control": "no-store"},
    )


async def degrada_con_grazia(request: Request, call_next):
    if request.method != "GET":
        return await call_next(request)

    chiave = _chiave(request)

    try:
        risposta = await call_next(request)
    except Exception as exc:
        if _e_problema_di_database(exc):
            return _ripiego(chiave, f"{type(exc).__name__}: {exc}")
        raise

    if risposta.status_code >= 500:
        return _ripiego(chiave, f"la rotta ha risposto {risposta.status_code}")

    if risposta.status_code != 200:
        return risposta

    # Per memorizzare il corpo bisogna consumarlo, e poi ricostruire la
    # risposta: un body_iterator si legge una volta sola.
    pezzi = [pezzo async for pezzo in risposta.body_iterator]
    corpo = b"".join(pezzi)
    content_type = risposta.headers.get("content-type", "application/json")
    if "json" in content_type:
        _ricorda(chiave, corpo, content_type)
    return Response(content=corpo, status_code=200, media_type=content_type,
                    headers={k: v for k, v in risposta.headers.items()
                             if k.lower() not in ("content-length", "content-type")})
