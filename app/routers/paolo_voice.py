"""
Paolo vocale — l'assistente PERSONALE di Ilan al telefono (non i clienti).

Condivide la memoria con il Paolo di Telegram tramite system_config['session_ilan_paolo']
(testo JSON di messaggi {role, content}): la carica a inizio chiamata (tool `contesto`)
e ci riscrive un riassunto a fine chiamata (webhook `/api/paolo/vapi-events`).

Strumenti (Fase 1):
- agenda      -> appuntamenti di oggi/domani
- situazione  -> nuovi lead + promemoria (tasks_ilan) aperti
- cerca       -> cliente per nome/telefono o immobile per indirizzo
- promemoria  -> crea un promemoria/istruzione in tasks_ilan
- contesto    -> ultimi messaggi della memoria condivisa con Telegram

Auth: stesso header X-Voice-Secret degli endpoint /api/voice.
Protocollo Vapi: riusa _in/_out da app.routers.voice.
"""
from fastapi import APIRouter, Header, HTTPException, Depends, Request
from typing import Optional
from datetime import datetime, timedelta
import json
import re
import asyncpg

try:
    from zoneinfo import ZoneInfo
    ROMA = ZoneInfo("Europe/Rome")
except Exception:  # pragma: no cover
    ROMA = None

from app.config import get_db, settings
from app.routers.voice import _in, _out, check_auth

router = APIRouter(prefix="/api/paolo", tags=["paolo-voce"])

SESSION_KEY = "session_ilan_paolo"
MAX_MEMORIA = 30

GIORNI = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]
MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def _now_roma() -> datetime:
    d = datetime.now(ROMA) if ROMA else datetime.now()
    return d.replace(tzinfo=None)


def _ora_parlata(dt: datetime) -> str:
    return f"{dt.hour}" if dt.minute == 0 else f"{dt.hour} e {dt.minute:02d}"


def _giorno_parlato(dt: datetime) -> str:
    return f"{GIORNI[dt.weekday()]} {dt.day} {MESI[dt.month]}"


# ----------------------------------------------------------------------------
# agenda
# ----------------------------------------------------------------------------
@router.post("/agenda", dependencies=[Depends(check_auth)])
async def agenda(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, tc = await _in(request)
    quando = str(params.get("quando") or "").lower().strip()
    oggi = _now_roma().replace(hour=0, minute=0, second=0, microsecond=0)
    if quando == "domani":
        ini, fin = oggi + timedelta(days=1), oggi + timedelta(days=2)
    elif quando == "oggi":
        ini, fin = oggi, oggi + timedelta(days=1)
    else:  # default: da adesso a fine domani
        ini, fin = oggi, oggi + timedelta(days=2)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.data_ora, a.luogo, a.tipo,
                   c.nome AS c_nome, c.cognome AS c_cognome,
                   i.indirizzo AS i_indirizzo
            FROM public.appuntamenti a
            LEFT JOIN public.clienti c ON c.id = a.cliente_id
            LEFT JOIN public.immobili i ON i.id = a.immobile_id
            WHERE a.data_ora >= $1 AND a.data_ora < $2
              AND coalesce(a.completato, false) = false
            ORDER BY a.data_ora
            """,
            ini, fin,
        )
    app_list = []
    for r in rows:
        dt = r["data_ora"]
        chi = " ".join(x for x in [r["c_nome"], r["c_cognome"]] if x) or "cliente"
        dove = r["i_indirizzo"] or r["luogo"] or ""
        app_list.append({
            "quando_parlato": f"{_giorno_parlato(dt)} alle {_ora_parlata(dt)}",
            "con": chi,
            "dove": dove,
        })
    msg = ("Non ha appuntamenti in questo periodo." if not app_list
           else f"Ha {len(app_list)} appuntament{'o' if len(app_list)==1 else 'i'}.")
    return _out({"appuntamenti": app_list, "messaggio": msg}, tc)


# ----------------------------------------------------------------------------
# situazione (lead nuovi + promemoria aperti)
# ----------------------------------------------------------------------------
@router.post("/situazione", dependencies=[Depends(check_auth)])
async def situazione(request: Request, db: asyncpg.Pool = Depends(get_db)):
    _params, tc = await _in(request)
    async with db.acquire() as conn:
        lead = await conn.fetch(
            """
            SELECT nome, cognome, telefono, fonte, info_chiave, created_at
            FROM public.leads
            WHERE created_at > now() - interval '3 days'
            ORDER BY created_at DESC LIMIT 5
            """
        )
        task = await conn.fetch(
            """
            SELECT short_id, tipo, descrizione, nome_riferimento, priorita
            FROM public.tasks_ilan
            WHERE stato = 'attivo'
            ORDER BY priorita NULLS LAST, created_at DESC LIMIT 8
            """
        )
    leads = [{
        "nome": " ".join(x for x in [l["nome"], l["cognome"]] if x) or "senza nome",
        "fonte": l["fonte"],
        "info": (l["info_chiave"] or "")[:120] or None,
    } for l in lead]
    promemoria = [{
        "riferimento": t["nome_riferimento"] or t["tipo"],
        "cosa": (t["descrizione"] or "")[:140],
        "priorita": t["priorita"],
    } for t in task]
    return _out({
        "nuovi_lead": leads,
        "promemoria_aperti": promemoria,
        "messaggio": f"{len(leads)} nuovi lead negli ultimi giorni e {len(promemoria)} promemoria aperti.",
    }, tc)


# ----------------------------------------------------------------------------
# cerca (cliente per nome/telefono o immobile per indirizzo)
# ----------------------------------------------------------------------------
@router.post("/cerca", dependencies=[Depends(check_auth)])
async def cerca(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, tc = await _in(request)
    testo = str(params.get("testo") or params.get("query") or "").strip()
    if not testo:
        return _out({"trovato": False, "messaggio": "Chi o cosa cerco?"}, tc)
    digits = re.sub(r"\D", "", testo)
    like = f"%{testo}%"
    async with db.acquire() as conn:
        cli = await conn.fetchrow(
            """
            SELECT id, nome, cognome, telefono, email, note
            FROM public.clienti
            WHERE attivo AND (
                nome ILIKE $1 OR cognome ILIKE $1
                OR (length($2) >= 6 AND regexp_replace(coalesce(telefono,''),'\\D','','g') LIKE '%'||$2||'%')
            )
            ORDER BY updated_at DESC NULLS LAST LIMIT 1
            """,
            like, digits,
        )
        if cli:
            ric = await conn.fetchrow(
                """
                SELECT zona, budget_massimo, mq_minimi, tipo_contratto
                FROM public.richieste WHERE cliente_id = $1 AND attiva
                ORDER BY updated_at DESC NULLS LAST LIMIT 1
                """,
                cli["id"],
            )
            r = dict(ric) if ric else {}
            return _out({
                "trovato": True, "tipo": "cliente",
                "nome": " ".join(x for x in [cli["nome"], cli["cognome"]] if x),
                "telefono": cli["telefono"],
                "cerca_in_zona": r.get("zona"),
                "budget": r.get("budget_massimo") and int(float(r["budget_massimo"])),
                "note": (cli["note"] or "")[:200] or None,
            }, tc)
        imm = await conn.fetchrow(
            """
            SELECT id, indirizzo, zona, tipo_contratto, prezzo, canone_mensile, mq, stato_vendita
            FROM public.immobili
            WHERE attivo AND indirizzo ILIKE $1
            ORDER BY updated_at DESC NULLS LAST LIMIT 1
            """,
            like,
        )
        if imm:
            prezzo = imm["canone_mensile"] if (imm["tipo_contratto"] == "locazione") else imm["prezzo"]
            return _out({
                "trovato": True, "tipo": "immobile",
                "indirizzo": imm["indirizzo"], "zona": imm["zona"],
                "mq": imm["mq"], "tipo_contratto": imm["tipo_contratto"],
                "prezzo": prezzo and int(float(prezzo)),
                "stato": imm["stato_vendita"],
            }, tc)
    return _out({"trovato": False, "messaggio": f"Non trovo nulla per '{testo}'."}, tc)


# ----------------------------------------------------------------------------
# promemoria (prendi istruzione)
# ----------------------------------------------------------------------------
@router.post("/promemoria", dependencies=[Depends(check_auth)])
async def promemoria(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, tc = await _in(request)
    testo = str(params.get("testo") or params.get("cosa") or "").strip()
    riferimento = params.get("riferimento") or params.get("nome")
    if not testo:
        return _out({"salvato": False, "messaggio": "Cosa devo ricordarle?"}, tc)
    import random
    short = "VOX" + "".join(random.choice("0123456789") for _ in range(5))
    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.tasks_ilan
                (short_id, tipo, descrizione, nome_riferimento, origine,
                 origine_dettaglio, stato, priorita, created_at, updated_at)
            VALUES ($1, 'promemoria', $2, $3, 'paolo_voce', 'chiamata', 'attivo', 2, now(), now())
            """,
            short, testo, (str(riferimento)[:120] if riferimento else None),
        )
    return _out({"salvato": True, "messaggio": "Fatto, me lo sono segnato."}, tc)


# ----------------------------------------------------------------------------
# contesto (memoria condivisa con Telegram)
# ----------------------------------------------------------------------------
async def _carica_memoria(conn) -> list:
    val = await conn.fetchval(
        "SELECT value FROM public.system_config WHERE key = $1", SESSION_KEY
    )
    if not val:
        return []
    try:
        data = json.loads(val)
        return data if isinstance(data, list) else []
    except Exception:
        return []


@router.post("/contesto", dependencies=[Depends(check_auth)])
async def contesto(request: Request, db: asyncpg.Pool = Depends(get_db)):
    _params, tc = await _in(request)
    async with db.acquire() as conn:
        msgs = await _carica_memoria(conn)
    ultimi = msgs[-8:]
    recap = " | ".join(
        f"{m.get('role','?')}: {str(m.get('content',''))[:120]}" for m in ultimi
    ) or "Nessuno scambio recente."
    return _out({"recap": recap, "n_messaggi": len(msgs)}, tc)


# ----------------------------------------------------------------------------
# Webhook eventi Vapi: a fine chiamata riscrive un riassunto nella memoria
# condivisa, cosi' Telegram e' allineato. (server.url dell'assistente Paolo)
# ----------------------------------------------------------------------------
@router.post("/vapi-events")
async def vapi_events(request: Request, x_voice_secret: Optional[str] = Header(None),
                      db: asyncpg.Pool = Depends(get_db)):
    if settings.voice_secret and x_voice_secret != settings.voice_secret:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        raw = await request.json()
    except Exception:
        return {"ok": True}
    msg = (raw or {}).get("message", {}) if isinstance(raw, dict) else {}
    tipo = msg.get("type")
    if tipo not in ("end-of-call-report", "status-update"):
        return {"ok": True}
    summary = msg.get("summary") or msg.get("analysis", {}).get("summary")
    transcript = msg.get("transcript")
    testo = summary or (transcript or "")[:600]
    if not testo:
        return {"ok": True}
    async with db.acquire() as conn:
        msgs = await _carica_memoria(conn)
        msgs.append({"role": "assistant", "content": f"[Chiamata vocale con Ilan] {testo}"})
        msgs = msgs[-MAX_MEMORIA:]
        await conn.execute(
            """
            INSERT INTO public.system_config (key, value, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            SESSION_KEY, json.dumps(msgs, ensure_ascii=False),
        )
    return {"ok": True}
