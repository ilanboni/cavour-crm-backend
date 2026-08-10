"""
Endpoint per l'agente vocale (Vapi).

Regola architetturale: il modello non ricorda nulla. Prezzi, disponibilita',
indirizzi e agenda si leggono SEMPRE da qui. Risposte asciutte, prezzo gia'
formattato per il TTS.

Auth: header X-Voice-Secret su ogni richiesta (env VOICE_SECRET).
Whitelist mandato: solo gli immobili in VOICE_WHITELIST_IDS finiscono in bocca
all'agente (finche' immobili.esclusiva non e' popolato). immobili_esterni
(scouting) non viene MAI toccato da questi endpoint.

Stack DB: asyncpg (pool Postgres), come il resto del backend.
"""
from fastapi import APIRouter, Header, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import asyncpg
import json

from app.config import get_db, settings

router = APIRouter(prefix="/api/voice", tags=["voice"])


# ----------------------------------------------------------------------------
# Protocollo Vapi: gli endpoint accettano sia il formato "piatto" (test/REST)
# sia l'envelope Vapi {message:{toolCalls:[{id, function:{name, arguments}}]}}
# e rispondono di conseguenza. Se e' Vapi, la risposta va incapsulata in
# {results:[{toolCallId, result:"<stringa>"}]}.
# ----------------------------------------------------------------------------
async def _in(request: Request):
    """Ritorna (params: dict, tool_call_id | None)."""
    try:
        raw = await request.json()
    except Exception:
        return {}, None
    if isinstance(raw, dict) and isinstance(raw.get("message"), dict):
        msg = raw["message"]
        tcs = msg.get("toolCalls") or msg.get("toolCallList") or []
        if tcs:
            tc = tcs[0]
            tcid = tc.get("id") or tc.get("toolCallId")
            fn = tc.get("function") or {}
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            return (args or {}), tcid
    return (raw if isinstance(raw, dict) else {}), None


def _out(result, tool_call_id):
    if tool_call_id:
        return {"results": [{
            "toolCallId": tool_call_id,
            "result": json.dumps(result, ensure_ascii=False),
        }]}
    return result

# --- Whitelist mandato confermata (immobili pubblicati sito+vetrina) ---
VOICE_WHITELIST_IDS = [6, 7, 8, 9, 10, 11, 12, 13, 14]

# Orario visite ammesso (per il controllo disponibilita')
ORA_APERTURA = 9
ORA_CHIUSURA = 20
# Durata slot: nessun altro appuntamento sullo stesso immobile entro +/- questo
SLOT_MINUTI = 90

GIORNI = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]
MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


# ----------------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------------
def check_auth(x_voice_secret: Optional[str] = Header(None)):
    atteso = settings.voice_secret
    if not atteso:
        # Meglio fallire chiuso che lasciare aperto il portafoglio clienti.
        raise HTTPException(status_code=503, detail="VOICE_SECRET non configurato")
    if x_voice_secret != atteso:
        raise HTTPException(status_code=401, detail="unauthorized")
    return True


# ----------------------------------------------------------------------------
# Helper puri (formattazione per il TTS)
# ----------------------------------------------------------------------------
def _euro(v) -> Optional[str]:
    """270000 -> '270.000' (punto separatore migliaia, letto bene dal TTS)."""
    if v in (None, ""):
        return None
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return str(v)
    return f"{n:,}".replace(",", ".")


def _prezzo_parlato(row: dict) -> Optional[str]:
    tipo = (row.get("tipo_contratto") or "").lower()
    if tipo == "locazione":
        e = _euro(row.get("canone_mensile"))
        return f"{e} euro al mese" if e else None
    e = _euro(row.get("prezzo"))
    return f"{e} euro" if e else None


def _stato_parlato(row: dict) -> Optional[str]:
    if row.get("stato_nuovo"):
        return "nuovo"
    if row.get("stato_ristrutturato"):
        return "ristrutturato"
    if row.get("stato_buono"):
        return "in buono stato"
    if row.get("stato_da_ristrutturare"):
        return "da ristrutturare"
    return None


def _data_parlata(dt: datetime) -> str:
    g = GIORNI[dt.weekday()]
    ora = f"{dt.hour}" if dt.minute == 0 else f"{dt.hour} e {dt.minute:02d}"
    return f"{g} {dt.day} {MESI[dt.month]} alle {ora}"


def _num(v):
    if v in (None, ""):
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return v


def _scheda_immobile(row: dict) -> dict:
    return {
        "trovato": True,
        "id": row["id"],
        "indirizzo": row.get("indirizzo"),
        "zona": row.get("zona"),
        "tipologia": row.get("categoria") or row.get("titolo"),
        "mq": row.get("mq"),
        "locali": row.get("locali"),
        "stato": _stato_parlato(row),
        "tipo_contratto": row.get("tipo_contratto"),
        "prezzo_parlato": _prezzo_parlato(row),
    }


# ----------------------------------------------------------------------------
# 3.1 lookup-cliente
# ----------------------------------------------------------------------------
class LookupIn(BaseModel):
    telefono: str


@router.post("/lookup-cliente", dependencies=[Depends(check_auth)])
async def lookup_cliente(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, _tcid = await _in(request)
    tel = str(params.get("telefono") or "")
    async with db.acquire() as conn:
        # Match robusto: ultime 9 cifre, ignorando +, spazi, prefissi.
        cli = await conn.fetchrow(
            """
            SELECT id, appellativo, nome, cognome, telefono, note
            FROM public.clienti
            WHERE attivo
              AND right(regexp_replace(coalesce(telefono,''), '\\D', '', 'g'), 9)
                = right(regexp_replace($1, '\\D', '', 'g'), 9)
              AND length(regexp_replace(coalesce(telefono,''), '\\D', '', 'g')) >= 9
            LIMIT 1
            """,
            tel,
        )
        if cli:
            nome = " ".join(x for x in [cli["appellativo"], cli["nome"],
                                        cli["cognome"]] if x) or cli["nome"]
            ric = await conn.fetchrow(
                """
                SELECT id, zona, budget_massimo, mq_minimi, mq_massimi
                FROM public.richieste
                WHERE cliente_id = $1 AND attiva
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 1
                """,
                cli["id"],
            )
            r = dict(ric) if ric else {}
            import re
            zone = [z.strip() for z in re.split(r"[,/;]", r.get("zona") or "") if z.strip()]
            return _out({
                "trovato": True,
                "tipo": "cliente",
                "cliente_id": cli["id"],
                "nome": nome,
                "richiesta_id": r.get("id"),
                "zone": zone,
                "budget_max": _num(r.get("budget_massimo")),
                "mq_min": r.get("mq_minimi"),
                "mq_max": r.get("mq_massimi"),
                "note_sintetiche": (cli["note"] or "")[:200] or None,
            }, _tcid)

        # Memoria unificata: cerca tra i lead di Paolo (WhatsApp/portali)
        ld = await conn.fetchrow(
            """
            SELECT id, nome, cognome, telefono, info_chiave, tipo_lead
            FROM public.leads
            WHERE right(regexp_replace(coalesce(telefono,''), '\\D', '', 'g'), 9)
                = right(regexp_replace($1, '\\D', '', 'g'), 9)
              AND length(regexp_replace(coalesce(telefono,''), '\\D', '', 'g')) >= 9
            LIMIT 1
            """,
            tel,
        )
        if ld:
            nome = " ".join(x for x in [ld["nome"], ld["cognome"]] if x) or None
            return _out({
                "trovato": True,
                "tipo": "lead",
                "cliente_id": None,
                "lead_id": str(ld["id"]),
                "nome": nome,
                "note_sintetiche": (ld["info_chiave"] or "")[:200] or None,
            }, _tcid)

    return _out({"trovato": False}, _tcid)


# ----------------------------------------------------------------------------
# 3.2 immobile
# ----------------------------------------------------------------------------
class ImmobileIn(BaseModel):
    id: Optional[int] = None
    indirizzo: Optional[str] = None


@router.post("/immobile", dependencies=[Depends(check_auth)])
async def immobile(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, _tcid = await _in(request)
    body = ImmobileIn(**{k: params.get(k) for k in ("id", "indirizzo") if k in params})
    async with db.acquire() as conn:
        if body.id is not None:
            if body.id not in VOICE_WHITELIST_IDS:
                return _out({"trovato": False}, _tcid)
            row = await conn.fetchrow(
                "SELECT * FROM public.immobili WHERE id = $1 AND attivo", body.id
            )
            return _out(_scheda_immobile(dict(row)) if row else {"trovato": False}, _tcid)

        if body.indirizzo:
            row = await conn.fetchrow(
                """
                SELECT * FROM public.immobili
                WHERE id = ANY($1::int[]) AND attivo AND indirizzo ILIKE $2
                LIMIT 1
                """,
                VOICE_WHITELIST_IDS, f"%{body.indirizzo}%",
            )
            return _out(_scheda_immobile(dict(row)) if row else {"trovato": False}, _tcid)

    return _out({"trovato": False, "messaggio": "Serve id o indirizzo"}, _tcid)


# ----------------------------------------------------------------------------
# 3.3 proposte
# ----------------------------------------------------------------------------
class ProposteIn(BaseModel):
    zone: Optional[List[str]] = None
    budget_max: Optional[float] = None
    mq_min: Optional[int] = None
    tipo_contratto: Optional[str] = "vendita"


@router.post("/proposte", dependencies=[Depends(check_auth)])
async def proposte(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, _tcid = await _in(request)
    body = ProposteIn(**{k: params.get(k) for k in
                         ("zone", "budget_max", "mq_min", "tipo_contratto") if k in params})
    tipo = (body.tipo_contratto or "vendita").lower()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM public.immobili
            WHERE id = ANY($1::int[]) AND attivo AND tipo_contratto = $2
            """,
            VOICE_WHITELIST_IDS, tipo,
        )
    rows = [dict(r) for r in rows]
    zone_in = [z.lower() for z in (body.zone or [])]

    def _prezzo_val(r):
        v = r.get("canone_mensile") if tipo == "locazione" else r.get("prezzo")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _match_zona(r):
        z = (r.get("zona") or "").lower()
        if not zone_in or not z:
            return not zone_in
        return any(zi in z or z in zi for zi in zone_in)

    def _filtro(r, budget_mult, usa_zona, mq_mult):
        if body.mq_min and (r.get("mq") or 0) < body.mq_min * mq_mult:
            return False
        if body.budget_max:
            pv = _prezzo_val(r)
            if pv is not None and pv > float(body.budget_max) * budget_mult:
                return False
        if usa_zona and not _match_zona(r):
            return False
        return True

    # Fallback progressivo: dallo stretto al piu' largo, come un bravo agente.
    tentativi = [
        (1.00, True, 1.00),
        (1.15, True, 1.00),
        (1.15, False, 1.00),
        (1.30, False, 0.85),
    ]

    def _score(r):
        pv = _prezzo_val(r) or 0
        return abs((float(body.budget_max) - pv)) if body.budget_max else pv

    scelti, visti = [], set()
    for budget_mult, usa_zona, mq_mult in tentativi:
        cand = [r for r in rows if r["id"] not in visti
                and _filtro(r, budget_mult, usa_zona, mq_mult)]
        cand.sort(key=_score)
        for r in cand:
            scelti.append(r)
            visti.add(r["id"])
            if len(scelti) >= 3:
                break
        if len(scelti) >= 3:
            break

    return _out({"risultati": [{
        "id": r["id"],
        "indirizzo": r.get("indirizzo"),
        "zona": r.get("zona"),
        "mq": r.get("mq"),
        "prezzo_parlato": _prezzo_parlato(r),
    } for r in scelti[:3]]}, _tcid)


# ----------------------------------------------------------------------------
# 3.4 appuntamento
# ----------------------------------------------------------------------------
class AppuntamentoIn(BaseModel):
    cliente_id: Optional[int] = None
    id_immobile: Optional[int] = None
    codice_immobile: Optional[int] = None  # alias compat col brief
    quando: datetime
    telefono: Optional[str] = None
    nome: Optional[str] = None


@router.post("/appuntamento", dependencies=[Depends(check_auth)])
async def appuntamento(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, _tcid = await _in(request)
    try:
        body = AppuntamentoIn(**{k: params.get(k) for k in
            ("cliente_id", "id_immobile", "codice_immobile", "quando", "telefono", "nome")
            if k in params})
    except Exception:
        return _out({"confermato": False, "motivo": "dati_incompleti",
                     "messaggio": "Mi serve l'immobile e giorno e ora della visita."}, _tcid)

    imm_id = body.id_immobile or body.codice_immobile
    if not imm_id or imm_id not in VOICE_WHITELIST_IDS:
        return _out({"confermato": False, "motivo": "immobile_non_valido",
                     "messaggio": "Non ho quell'immobile tra quelli disponibili."}, _tcid)

    quando = body.quando
    if quando.tzinfo is not None:
        quando = quando.replace(tzinfo=None)

    if not (ORA_APERTURA <= quando.hour < ORA_CHIUSURA):
        return _out({"confermato": False, "motivo": "fuori_orario",
                     "messaggio": "Le visite si fissano tra le 9 e le 20."}, _tcid)

    async with db.acquire() as conn:
        cliente_id = body.cliente_id
        if not cliente_id and body.telefono:
            cliente_id = await _ensure_cliente(conn, body.telefono, body.nome)
        if not cliente_id:
            return _out({"confermato": False, "motivo": "serve_contatto",
                         "messaggio": "Mi lascia un recapito per fissare la visita?"}, _tcid)

        ini = quando - timedelta(minutes=SLOT_MINUTI)
        fin = quando + timedelta(minutes=SLOT_MINUTI)
        occupato = await conn.fetchval(
            """
            SELECT 1 FROM public.appuntamenti
            WHERE immobile_id = $1 AND data_ora BETWEEN $2 AND $3
            LIMIT 1
            """,
            imm_id, ini, fin,
        )
        if occupato:
            return _out({"confermato": False, "motivo": "slot_occupato",
                         "messaggio": "Quell'orario e' gia' occupato, ne propongo un altro."}, _tcid)

        appt_id = await conn.fetchval(
            """
            INSERT INTO public.appuntamenti
                (cliente_id, immobile_id, data_ora, tipo, confermato, note)
            VALUES ($1, $2, $3, 'visita', true, 'Fissato da agente vocale')
            RETURNING id
            """,
            cliente_id, imm_id, quando,
        )

    return _out({
        "confermato": True,
        "appuntamento_id": appt_id,
        "quando_parlato": _data_parlata(quando),
    }, _tcid)


async def _ensure_cliente(conn, telefono: str, nome: Optional[str]) -> Optional[int]:
    """Trova il cliente per telefono (ultime 9 cifre) o lo crea. Ritorna id."""
    cid = await conn.fetchval(
        """
        SELECT id FROM public.clienti
        WHERE attivo
          AND right(regexp_replace(coalesce(telefono,''), '\\D', '', 'g'), 9)
            = right(regexp_replace($1, '\\D', '', 'g'), 9)
          AND length(regexp_replace(coalesce(telefono,''), '\\D', '', 'g')) >= 9
        LIMIT 1
        """,
        telefono,
    )
    if cid:
        return cid
    nome_p = (nome or "").strip()
    parti = nome_p.split(" ", 1)
    return await conn.fetchval(
        """
        INSERT INTO public.clienti
            (nome, cognome, telefono, fonte_acquisizione, tipo_cliente, attivo)
        VALUES ($1, $2, $3, 'chiamata_vocale', 'compratore', true)
        RETURNING id
        """,
        parti[0] or "Cliente",
        parti[1] if len(parti) > 1 else None,
        _norm_e164(telefono),
    )


def _norm_e164(t: Optional[str]) -> Optional[str]:
    import re
    d = re.sub(r"\D", "", t or "")
    if not d:
        return None
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("39") and len(d) > 10:
        return "+" + d
    if len(d) in (9, 10) and d.startswith("3"):
        return "+39" + d
    if d.startswith("39"):
        return "+" + d
    return "+" + d


# ----------------------------------------------------------------------------
# 3.5 lead
# ----------------------------------------------------------------------------
class LeadIn(BaseModel):
    telefono: str
    nome: Optional[str] = None
    zone: Optional[List[str]] = None
    budget_max: Optional[float] = None
    tipo_contratto: Optional[str] = "vendita"
    note: Optional[str] = None


@router.post("/lead", dependencies=[Depends(check_auth)])
async def lead(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, _tcid = await _in(request)
    try:
        body = LeadIn(**{k: params.get(k) for k in
            ("telefono", "nome", "zone", "budget_max", "tipo_contratto", "note")
            if k in params})
    except Exception:
        return _out({"salvato": False, "messaggio": "Mi lascia un numero di telefono?"}, _tcid)
    async with db.acquire() as conn:
        cliente_id = await _ensure_cliente(conn, body.telefono, body.nome)
        if not cliente_id:
            return _out({"salvato": False, "messaggio": "Non sono riuscito a salvare, la faccio richiamare."}, _tcid)
        zona = ", ".join(body.zone) if body.zone else None
        ric_id = await conn.fetchval(
            """
            INSERT INTO public.richieste
                (cliente_id, tipo_contratto, budget_massimo, zona, note_agente, attiva)
            VALUES ($1, $2, $3, $4, $5, true)
            RETURNING id
            """,
            cliente_id,
            (body.tipo_contratto or "vendita").lower(),
            float(body.budget_max) if body.budget_max else None,
            zona,
            body.note,
        )
    return _out({"salvato": True, "cliente_id": cliente_id, "richiesta_id": ric_id}, _tcid)
