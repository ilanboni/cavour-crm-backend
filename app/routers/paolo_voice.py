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
import os
import json
import re
import uuid
import asyncpg
import httpx

try:
    from zoneinfo import ZoneInfo
    ROMA = ZoneInfo("Europe/Rome")
except Exception:  # pragma: no cover
    ROMA = None

from app.config import get_db, settings
from app.routers.voice import _in, _out, check_auth

router = APIRouter(prefix="/api/paolo", tags=["paolo-voce"])

SESSION_KEY = "session_ilan_paolo"  # memoria unica di Paolo, condivisa tra voce personale e Telegram
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
# trova (posti/ristoranti/info via Google Places) — skill "vero segretario"
# ----------------------------------------------------------------------------
@router.post("/trova", dependencies=[Depends(check_auth)])
async def trova(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, tc = await _in(request)
    cosa = str(params.get("cosa") or params.get("query") or "").strip()
    dove = str(params.get("dove") or "Milano").strip()
    if not cosa:
        return _out({"trovato": False, "messaggio": "Cosa cerco?"}, tc)
    key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return _out({"trovato": False,
                     "messaggio": "La ricerca posti non e' ancora attiva."}, tc)
    q = f"{cosa} a {dove}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as cli:
            r = await cli.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={"query": q, "language": "it", "region": "it", "key": key},
            )
            data = r.json()
    except Exception:
        return _out({"trovato": False,
                     "messaggio": "Non riesco a cercare adesso, riprova tra poco."}, tc)
    posti = []
    for p in (data.get("results") or [])[:3]:
        oh = p.get("opening_hours") or {}
        posti.append({
            "nome": p.get("name"),
            "indirizzo": p.get("formatted_address"),
            "rating": p.get("rating"),
            "aperto_ora": oh.get("open_now"),
            "place_id": p.get("place_id"),
        })
    if not posti:
        return _out({"trovato": False, "messaggio": f"Non trovo nulla per '{cosa}' a {dove}."}, tc)
    return _out({"trovato": True, "posti": posti,
                 "messaggio": f"Ho trovato {len(posti)} opzioni."}, tc)


async def _place_details(key: str, place_id: str) -> dict:
    """Numero e orari di un posto (per chiamarlo / prenotare in futuro)."""
    async with httpx.AsyncClient(timeout=8.0) as cli:
        r = await cli.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={"place_id": place_id, "language": "it",
                    "fields": "name,formatted_phone_number,international_phone_number,opening_hours,formatted_address",
                    "key": key},
        )
        return (r.json() or {}).get("result", {}) or {}


@router.post("/contatto-posto", dependencies=[Depends(check_auth)])
async def contatto_posto(request: Request, db: asyncpg.Pool = Depends(get_db)):
    """Dettagli di contatto di un posto (numero) — base per la prenotazione telefonica."""
    params, tc = await _in(request)
    place_id = str(params.get("place_id") or "").strip()
    key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not place_id or not key:
        return _out({"trovato": False, "messaggio": "Mi serve il posto preciso."}, tc)
    try:
        d = await _place_details(key, place_id)
    except Exception:
        return _out({"trovato": False, "messaggio": "Non riesco a recuperarlo ora."}, tc)
    tel = d.get("formatted_phone_number") or d.get("international_phone_number")
    return _out({
        "trovato": bool(tel),
        "nome": d.get("name"),
        "telefono": tel,
        "indirizzo": d.get("formatted_address"),
    }, tc)


# ----------------------------------------------------------------------------
# prenota (Paolo chiama il ristorante e prenota, via Vapi outbound)
# ----------------------------------------------------------------------------
VAPI_CALL_URL = "https://api.vapi.ai/call"
BOOKING_ASSISTANT_ID = os.getenv("VAPI_BOOKING_ASSISTANT_ID", "943a1a0a-1320-42a7-81dc-b98c02b45cb4")
CALLER_PHONE_ID = os.getenv("VAPI_PHONE_NUMBER_ID", "35ca1dab-04b2-4e2c-9358-aa292536d6f3")


def _e164_it(n: Optional[str]) -> Optional[str]:
    d = re.sub(r"\D", "", n or "")
    if not d:
        return None
    if d.startswith("39") and len(d) >= 11:
        return "+" + d
    if d.startswith("0"):          # geografico italiano (es. 02...) senza prefisso paese
        return "+39" + d
    if len(d) in (9, 10) and d.startswith("3"):
        return "+39" + d
    return "+" + d


@router.post("/prenota", dependencies=[Depends(check_auth)])
async def prenota(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, tc = await _in(request)
    tipo = str(params.get("tipo") or "ristorante").strip().lower()
    luogo = str(params.get("luogo") or params.get("ristorante") or "").strip()
    persone = str(params.get("persone") or "").strip()
    quando = str(params.get("quando") or "").strip()
    cosa = str(params.get("cosa") or "").strip()
    a_nome = str(params.get("a_nome") or "Ilan").strip()
    dove = str(params.get("dove") or "Milano").strip()
    numero = params.get("numero")
    place_id = params.get("place_id")
    try:
        programmata_id = int(params.get("programmata_id")) if params.get("programmata_id") else None
    except Exception:
        programmata_id = None

    key = os.getenv("VAPI_API_KEY", "")
    if not key:
        return _out({"avviata": False, "messaggio": "Le chiamate in uscita non sono ancora configurate."}, tc)
    if not quando:
        return _out({"avviata": False, "messaggio": "Per quando?"}, tc)
    if not luogo and not numero and not place_id:
        return _out({"avviata": False, "messaggio": "Dove devo prenotare?"}, tc)

    rest_num = None
    if numero:
        rest_num = _e164_it(str(numero))
    elif place_id:
        gk = os.getenv("GOOGLE_MAPS_API_KEY", "")
        if gk:
            try:
                d = await _place_details(gk, str(place_id))
                tel = d.get("international_phone_number") or d.get("formatted_phone_number")
                rest_num = _e164_it(tel) if tel else None
                if not luogo and d.get("name"):
                    luogo = d["name"]
            except Exception:
                pass
    # Fallback: solo il NOME -> cerca su Google (con citta' e poi senza, per posti fuori Milano)
    if not rest_num and luogo:
        gk = os.getenv("GOOGLE_MAPS_API_KEY", "")
        if gk:
            c = await _trova_numero(gk, luogo, dove)
            if c:
                rest_num = _e164_it(c["numero"])
                if c.get("ristorante"):
                    luogo = c["ristorante"]
    if not rest_num:
        return _out({"avviata": False, "messaggio": f"Non ho trovato il numero di {luogo or 'quel posto'}."}, tc)

    # frase parlata ("cosa") ed etichetta breve ("oggetto") per tipo di prenotazione
    frasi = {
        "ristorante": (f"un tavolo per {persone} persone" if persone else "un tavolo"),
        "tennis": "un campo da tennis", "padel": "un campo da padel",
        "medico": "una visita", "parrucchiere": "un appuntamento",
    }
    etichette = {
        "ristorante": (f"Tavolo x{persone}" if persone else "Tavolo"),
        "tennis": "Tennis", "padel": "Padel", "medico": "Visita", "parrucchiere": "Parrucchiere",
    }
    if not cosa:
        cosa = frasi.get(tipo, "un appuntamento")
    oggetto = etichette.get(tipo) or (cosa[:30].capitalize() if cosa else "Prenotazione")

    body = {
        "assistantId": BOOKING_ASSISTANT_ID,
        "phoneNumberId": CALLER_PHONE_ID,
        "customer": {"number": rest_num},
        "assistantOverrides": {"variableValues": {
            "cosa": cosa, "luogo": luogo, "quando": quando, "a_nome": a_nome,
            "saluto": ("Buongiorno" if _now_roma().hour < 13 else "Buonasera"),
            "oggi": _now_roma().strftime("%Y-%m-%d"),
        }},
        "metadata": {"tipo": tipo, "luogo": luogo, "oggetto": oggetto, "quando_label": quando},
    }
    call_id = None
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.post(VAPI_CALL_URL,
                               headers={"Authorization": "Bearer " + key,
                                        "Content-Type": "application/json"},
                               json=body)
        ok = r.status_code in (200, 201)
        if ok:
            call_id = (r.json() or {}).get("id")
    except Exception:
        ok = False
    if ok and call_id:
        try:
            async with db.acquire() as conn:
                await conn.execute(
                    "INSERT INTO public.prenotazioni_vocali (call_id, tipo, luogo, oggetto, quando_label, stato, programmata_id) "
                    "VALUES ($1,$2,$3,$4,$5,'in_corso',$6)",
                    call_id, tipo, luogo, oggetto, quando, programmata_id)
        except Exception:
            pass
    return _out({"avviata": ok, "luogo": luogo, "ristorante": luogo,
                 "messaggio": (f"Sto chiamando {luogo} per prenotare {cosa}, {quando}. Ti faccio sapere com'e' andata."
                               if ok else "Non sono riuscito a far partire la chiamata.")}, tc)


# ----------------------------------------------------------------------------
# chiama (Paolo telefona a chiunque per una commissione, via Vapi outbound)
# ----------------------------------------------------------------------------
ERRAND_ASSISTANT_ID = os.getenv("VAPI_ERRAND_ASSISTANT_ID", "")


async def _rubrica(conn) -> list:
    val = await conn.fetchval(
        "SELECT value FROM public.system_config WHERE key = 'rubrica_personale_ilan'")
    if not val:
        return []
    try:
        d = json.loads(val)
        return d if isinstance(d, list) else []
    except Exception:
        return []


@router.post("/chiama", dependencies=[Depends(check_auth)])
async def chiama(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, tc = await _in(request)
    scopo = str(params.get("scopo") or params.get("motivo") or "").strip()
    a_nome = str(params.get("a_nome") or "Ilan").strip()
    chi = str(params.get("chi") or params.get("nome") or "").strip()
    numero = params.get("numero")
    place_id = params.get("place_id")

    key = os.getenv("VAPI_API_KEY", "")
    if not key or not ERRAND_ASSISTANT_ID:
        return _out({"avviata": False, "messaggio": "Le chiamate in uscita non sono ancora configurate."}, tc)
    if not scopo:
        return _out({"avviata": False, "messaggio": "Per dirgli cosa devo chiamare?"}, tc)

    rest_num = None
    nome_dest = chi or "il contatto"
    relazione = ""
    if numero:
        rest_num = _e164_it(str(numero))
    elif place_id:
        gk = os.getenv("GOOGLE_MAPS_API_KEY", "")
        if gk:
            try:
                d = await _place_details(gk, str(place_id))
                tel = d.get("international_phone_number") or d.get("formatted_phone_number")
                rest_num = _e164_it(tel) if tel else None
                nome_dest = d.get("name") or nome_dest
            except Exception:
                pass
    elif chi:
        dl = chi.lower()
        async with db.acquire() as conn:
            for c in await _rubrica(conn):
                chiavi = [str(x).lower() for x in (c.get("relazioni") or [])]
                chiavi.append(str(c.get("nome", "")).lower())
                if any(k and (dl == k or (len(k) >= 4 and k in dl)) for k in chiavi):
                    rest_num = _e164_it(c.get("telefono"))
                    nome_dest = c.get("nome") or chi
                    relazione = "famiglia"
                    break
            if not rest_num:
                row = await conn.fetchrow(
                    "SELECT nome, cognome, telefono FROM public.clienti WHERE attivo AND telefono IS NOT NULL "
                    "AND (nome ILIKE $1 OR cognome ILIKE $1) LIMIT 1", f"%{chi}%")
                if row:
                    rest_num = _e164_it(row["telefono"])
                    nome_dest = " ".join(x for x in [row["nome"], row["cognome"]] if x) or chi
    if not rest_num:
        return _out({"avviata": False, "messaggio": f"Non ho il numero di {nome_dest}."}, tc)

    body = {
        "assistantId": ERRAND_ASSISTANT_ID,
        "phoneNumberId": CALLER_PHONE_ID,
        "customer": {"number": rest_num},
        "assistantOverrides": {"variableValues": {
            "chi": nome_dest, "scopo": scopo, "a_nome": a_nome, "relazione": relazione,
        }},
        "metadata": {"chi": nome_dest, "scopo": scopo[:120]},
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.post(VAPI_CALL_URL,
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                               json=body)
        ok = r.status_code in (200, 201)
    except Exception:
        ok = False
    return _out({"avviata": ok, "chi": nome_dest,
                 "messaggio": (f"Sto chiamando {nome_dest}, ti faccio sapere."
                               if ok else "Non sono riuscito a far partire la chiamata.")}, tc)


# ----------------------------------------------------------------------------
# calcola (provvigione / rata mutuo) — deterministico
# ----------------------------------------------------------------------------
def _num_it(x) -> str:
    try:
        return f"{int(round(float(x))):,}".replace(",", ".")
    except Exception:
        return str(x)


@router.post("/calcola", dependencies=[Depends(check_auth)])
async def calcola(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, tc = await _in(request)
    tipo = str(params.get("tipo") or "").lower()

    def f(*keys, default=0.0):
        for k in keys:
            v = params.get(k)
            if v not in (None, ""):
                try:
                    return float(str(v).replace(",", "."))
                except Exception:
                    pass
        return default

    if "provv" in tipo:
        prezzo = f("prezzo", "importo")
        perc = f("percentuale", "perc", default=3.0)
        if prezzo <= 0:
            return _out({"messaggio": "Su quale prezzo?"}, tc)
        imp = prezzo * perc / 100
        return _out({"importo": round(imp), "con_iva": round(imp * 1.22),
                     "messaggio": f"Provvigione del {perc:g}% su {_num_it(prezzo)} euro: {_num_it(imp)} euro, {_num_it(imp*1.22)} con IVA."}, tc)

    if "mutuo" in tipo or "rata" in tipo:
        cap = f("importo", "capitale", "prezzo")
        tasso_a = f("tasso", "tasso_annuo")
        anni = f("anni", "durata")
        if cap <= 0 or anni <= 0:
            return _out({"messaggio": "Mi servono importo, tasso e anni."}, tc)
        i = tasso_a / 100 / 12
        n = int(anni * 12)
        rata = cap * i / (1 - (1 + i) ** (-n)) if i > 0 else cap / n
        return _out({"rata": round(rata),
                     "messaggio": f"Rata stimata: circa {_num_it(rata)} euro al mese per {int(anni)} anni."}, tc)

    return _out({"messaggio": "Posso calcolare la provvigione o la rata di un mutuo. Quale ti serve?"}, tc)


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


# ----------------------------------------------------------------------------
# giro di perlustrazione: Paolo chiama piu' ristoranti per vedere chi ha posto
# (Fase B). Ogni chiamata usa l'assistente "Perlustrazione" e a fine chiamata
# il webhook /webhook/vapi-perlustrazione (Flask) aggrega e avvisa Ilan.
# ----------------------------------------------------------------------------
PERLUSTRA_ASSISTANT_ID = os.getenv("VAPI_PERLUSTRA_ASSISTANT_ID",
                                   "0405a598-3439-439c-9b9b-0e51d53e08b1")
GIRO_MAX = 5


async def _num_da_place(gk: str, place_id: str, nome_default: str = "") -> Optional[dict]:
    try:
        d = await _place_details(gk, place_id)
    except Exception:
        return None
    tel = d.get("international_phone_number") or d.get("formatted_phone_number")
    num = _e164_it(tel) if tel else None
    if not num:
        return None
    return {"ristorante": d.get("name") or nome_default, "numero": num}


async def _trova_numero(gk: str, nome: str, dove: str) -> Optional[dict]:
    """Cerca il numero di un ristorante: prima con la citta', poi (fallback) col
    solo nome, cosi' trova posti fuori Milano (es. Forte dei Marmi)."""
    queries = [f"{nome} {dove}".strip(), nome.strip()]
    visti = set()
    for q in queries:
        if not q or q in visti:
            continue
        visti.add(q)
        try:
            async with httpx.AsyncClient(timeout=8.0) as cli:
                rr = await cli.get(
                    "https://maps.googleapis.com/maps/api/place/textsearch/json",
                    params={"query": q, "language": "it", "region": "it", "key": gk},
                )
            results = (rr.json() or {}).get("results") or []
        except Exception:
            results = []
        if results:
            c = await _num_da_place(gk, results[0].get("place_id"), nome)
            if c:
                return c
    return None


async def _candidati_da_nomi(gk: str, nomi: list, dove: str) -> list:
    out = []
    for nome in nomi:
        c = await _trova_numero(gk, nome, dove)
        if c:
            out.append(c)
    return out


async def _candidati_da_ricerca(gk: str, cosa: str, dove: str, quanti: int) -> list:
    try:
        async with httpx.AsyncClient(timeout=8.0) as cli:
            rr = await cli.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={"query": f"{cosa} a {dove}", "language": "it", "region": "it", "key": gk},
            )
        results = (rr.json() or {}).get("results") or []
    except Exception:
        results = []
    out = []
    for p in results:
        if len(out) >= quanti:
            break
        c = await _num_da_place(gk, p.get("place_id"), p.get("name") or "")
        if c:
            out.append(c)
    return out


@router.post("/giro", dependencies=[Depends(check_auth)])
async def giro(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, tc = await _in(request)
    persone = str(params.get("persone") or "").strip()
    quando = str(params.get("quando") or "").strip()
    dove = str(params.get("dove") or "Milano").strip()
    ristoranti = params.get("ristoranti")
    cosa = str(params.get("cosa") or params.get("query") or "").strip()
    try:
        quanti = int(params.get("quanti") or 4)
    except Exception:
        quanti = 4
    quanti = max(1, min(quanti, GIRO_MAX))

    key = os.getenv("VAPI_API_KEY", "")
    gk = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return _out({"avviato": False, "messaggio": "Le chiamate in uscita non sono configurate."}, tc)
    if not persone or not quando:
        return _out({"avviato": False, "messaggio": "Per quante persone e per quando?"}, tc)
    if not gk:
        return _out({"avviato": False, "messaggio": "La ricerca posti non e' attiva."}, tc)

    nomi = []
    if isinstance(ristoranti, str):
        nomi = [x.strip() for x in re.split(r"[,;]|\be\b", ristoranti) if x.strip()]
    elif isinstance(ristoranti, list):
        nomi = [str(x).strip() for x in ristoranti if str(x).strip()]

    if nomi:
        cand = await _candidati_da_nomi(gk, nomi[:GIRO_MAX], dove)
    elif cosa:
        cand = await _candidati_da_ricerca(gk, cosa, dove, quanti)
    else:
        return _out({"avviato": False, "messaggio": "Quali ristoranti chiamo, o cosa cerco?"}, tc)

    if not cand:
        return _out({"avviato": False, "messaggio": "Non ho trovato numeri da chiamare."}, tc)
    cand = cand[:GIRO_MAX]

    gid = uuid.uuid4().hex
    saluto = "Buongiorno" if _now_roma().hour < 13 else "Buonasera"
    oggi = _now_roma().strftime("%Y-%m-%d")
    lanciate = 0
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.giri_perlustrazione (id, persone, quando, attesi, stato, notificato) "
            "VALUES ($1,$2,$3,0,'in_corso',false)", gid, persone, quando)
        for idx, c in enumerate(cand, start=1):
            body = {
                "assistantId": PERLUSTRA_ASSISTANT_ID,
                "phoneNumberId": CALLER_PHONE_ID,
                "customer": {"number": c["numero"]},
                "assistantOverrides": {"variableValues": {
                    "ristorante": c["ristorante"], "persone": persone,
                    "quando": quando, "saluto": saluto, "oggi": oggi,
                }},
                "metadata": {"giro_id": gid},
            }
            call_id = None
            try:
                async with httpx.AsyncClient(timeout=15.0) as cli:
                    r = await cli.post(VAPI_CALL_URL,
                                       headers={"Authorization": "Bearer " + key,
                                                "Content-Type": "application/json"},
                                       json=body)
                if r.status_code in (200, 201):
                    call_id = (r.json() or {}).get("id")
                    lanciate += 1
            except Exception:
                pass
            await conn.execute(
                "INSERT INTO public.giro_chiamate (giro_id, ordine, ristorante, numero, call_id, stato) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                gid, idx, c["ristorante"], c["numero"], call_id,
                ("in_corso" if call_id else "fallita"))
        # attesi = solo le chiamate realmente partite (per cui aspettiamo un webhook)
        await conn.execute(
            "UPDATE public.giri_perlustrazione SET attesi=$2 WHERE id=$1", gid, lanciate)

    return _out({
        "avviato": lanciate > 0, "giro_id": gid, "quante": lanciate,
        "messaggio": (f"Sto chiamando {lanciate} ristoranti per vedere chi ha posto {quando}. "
                      f"Ti mando il riepilogo appena finisco."
                      if lanciate > 0 else "Non sono riuscito ad avviare le chiamate."),
    }, tc)


@router.post("/call-esito", dependencies=[Depends(check_auth)])
async def call_esito(request: Request, db: asyncpg.Pool = Depends(get_db)):
    """Ritorna l'esito di una chiamata Vapi (usato dal poller di Paolo, che non
    ha la VAPI_API_KEY). 'pronto' e' True solo se la chiamata e' terminata."""
    params, tc = await _in(request)
    call_id = str(params.get("call_id") or "").strip()
    key = os.getenv("VAPI_API_KEY", "")
    if not call_id or not key:
        return _out({"pronto": False}, tc)
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.get("https://api.vapi.ai/call/" + call_id,
                              headers={"Authorization": "Bearer " + key})
        if r.status_code != 200:
            return _out({"pronto": False}, tc)
        call = r.json() or {}
    except Exception:
        return _out({"pronto": False}, tc)
    if call.get("status") != "ended":
        return _out({"pronto": False}, tc)
    sd = (call.get("analysis") or {}).get("structuredData") or {}
    ended = str(call.get("endedReason") or "")
    if sd:
        return _out({"pronto": True, "disponibile": bool(sd.get("disponibile")),
                     "orario_proposto": str(sd.get("orario_proposto") or ""),
                     "note": str(sd.get("note") or ""),
                     "structured": sd, "ended_reason": ended}, tc)
    return _out({"pronto": True, "disponibile": False, "orario_proposto": "",
                 "note": ended, "structured": {}, "ended_reason": ended}, tc)


def _parse_esegui(s: str):
    """Interpreta l'orario a cui far PARTIRE la chiamata: ISO completo
    (es. 2026-08-11T23:01) oppure solo orario HH:MM (oggi, o domani se gia' passato)."""
    s = (s or "").replace("Z", "").strip()
    if not s:
        return None
    if s.lower() in ("subito", "ora", "adesso", "now"):
        return datetime.now(ROMA) if ROMA else datetime.now()
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None and ROMA:
            dt = dt.replace(tzinfo=ROMA)
        return dt
    except Exception:
        pass
    m = re.match(r"^(\d{1,2})[:.](\d{2})$", s)
    if m:
        now = datetime.now(ROMA) if ROMA else datetime.now()
        cand = now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                           second=0, microsecond=0)
        if cand <= now:
            cand = cand + timedelta(days=1)
        return cand
    return None


@router.post("/programma-chiamata", dependencies=[Depends(check_auth)])
async def programma_chiamata(request: Request, db: asyncpg.Pool = Depends(get_db)):
    """Salva una chiamata (prenotazione o giro) da far partire a un orario futuro.
    Un job al minuto la esegue quando arriva l'ora."""
    params, tc = await _in(request)
    dt = _parse_esegui(str(params.get("esegui_alle") or params.get("quando_chiamare") or ""))
    if not dt:
        return _out({"programmata": False, "messaggio": "A che ora devo richiamare?"}, tc)
    azione = str(params.get("azione") or "prenota").strip().lower()
    if azione not in ("prenota", "giro"):
        azione = "prenota"
    ripeti = bool(params.get("ripeti"))
    try:
        max_tentativi = int(params.get("max_tentativi") or (2 if ripeti else 1))
    except Exception:
        max_tentativi = 2 if ripeti else 1
    max_tentativi = max(1, min(max_tentativi, 5))
    try:
        intervallo_min = int(params.get("intervallo_min") or 15)
    except Exception:
        intervallo_min = 15
    intervallo_min = max(5, min(intervallo_min, 120))
    payload = {}
    for k in ("tipo", "luogo", "quando", "persone", "dove", "cosa", "ristoranti", "a_nome"):
        v = params.get(k)
        if v is not None and v != "":
            payload[k] = v
    payload.setdefault("a_nome", "Ilan")
    if not payload.get("luogo") and not payload.get("ristoranti"):
        return _out({"programmata": False, "messaggio": "Quale posto devo richiamare?"}, tc)
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.chiamate_programmate "
            "(esegui_alle, azione, payload, stato, ripeti, intervallo_min, max_tentativi, tentativi) "
            "VALUES ($1,$2,$3::jsonb,'in_attesa',$4,$5,$6,0)",
            dt, azione, json.dumps(payload, ensure_ascii=False), ripeti, intervallo_min, max_tentativi)
    coda = ""
    if ripeti and max_tentativi > 1:
        coda = f" Se non risponde nessuno riprovo (fino a {max_tentativi} volte, ogni {intervallo_min} min)."
    return _out({"programmata": True, "esegui_alle": dt.isoformat(),
                 "messaggio": (f"Ok, richiamo {payload.get('luogo', 'il posto')} alle "
                               f"{dt.strftime('%H:%M')} e ti mando l'esito." + coda)}, tc)


@router.post("/prenota-giro", dependencies=[Depends(check_auth)])
async def prenota_giro(request: Request, db: asyncpg.Pool = Depends(get_db)):
    """Fase B — B2: dopo il report, Ilan sceglie 'prenota il N' e Paolo prenota
    quel ristorante (riusa l'assistente di prenotazione + calendario)."""
    params, tc = await _in(request)
    a_nome = str(params.get("a_nome") or "Ilan").strip()
    try:
        n = int(params.get("numero_opzione") or params.get("numero") or 0)
    except Exception:
        n = 0
    key = os.getenv("VAPI_API_KEY", "")
    if not key:
        return _out({"avviata": False, "messaggio": "Le chiamate non sono configurate."}, tc)

    async with db.acquire() as conn:
        g = await conn.fetchrow(
            "SELECT id, persone, quando FROM public.giri_perlustrazione ORDER BY creato_at DESC LIMIT 1")
        if not g:
            return _out({"avviata": False, "messaggio": "Non c'e' nessun giro recente."}, tc)
        disp = await conn.fetch(
            "SELECT ristorante, numero, orario_proposto FROM public.giro_chiamate "
            "WHERE giro_id=$1 AND stato='disponibile' ORDER BY ordine", g["id"])
    if not disp:
        return _out({"avviata": False, "messaggio": "Nessun ristorante disponibile nell'ultimo giro."}, tc)
    if n < 1 or n > len(disp):
        return _out({"avviata": False,
                     "messaggio": f"Scegli un numero tra 1 e {len(disp)}."}, tc)

    scelto = disp[n - 1]
    quando = scelto["orario_proposto"] or g["quando"]
    persone = g["persone"]
    cosa = f"un tavolo per {persone} persone" if persone else "un tavolo"
    oggetto = f"Tavolo x{persone}" if persone else "Tavolo"
    body = {
        "assistantId": BOOKING_ASSISTANT_ID,
        "phoneNumberId": CALLER_PHONE_ID,
        "customer": {"number": scelto["numero"]},
        "assistantOverrides": {"variableValues": {
            "cosa": cosa, "luogo": scelto["ristorante"],
            "quando": quando, "a_nome": a_nome,
            "saluto": ("Buongiorno" if _now_roma().hour < 13 else "Buonasera"),
            "oggi": _now_roma().strftime("%Y-%m-%d"),
        }},
        "metadata": {"tipo": "ristorante", "luogo": scelto["ristorante"],
                     "oggetto": oggetto, "quando_label": quando},
    }
    call_id = None
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.post(VAPI_CALL_URL,
                               headers={"Authorization": "Bearer " + key,
                                        "Content-Type": "application/json"},
                               json=body)
        ok = r.status_code in (200, 201)
        if ok:
            call_id = (r.json() or {}).get("id")
    except Exception:
        ok = False
    if ok and call_id:
        try:
            async with db.acquire() as conn:
                await conn.execute(
                    "INSERT INTO public.prenotazioni_vocali (call_id, tipo, luogo, oggetto, quando_label, stato) "
                    "VALUES ($1,'ristorante',$2,$3,$4,'in_corso')",
                    call_id, scelto["ristorante"], oggetto, quando)
        except Exception:
            pass
    return _out({"avviata": ok, "ristorante": scelto["ristorante"],
                 "messaggio": (f"Sto chiamando {scelto['ristorante']} per prenotare. "
                               f"Ti confermo appena ho finito."
                               if ok else "Non sono riuscito a far partire la chiamata.")}, tc)


# ----------------------------------------------------------------------------
# chiama-lead: Paolo telefona SUBITO a un nuovo lead per qualificarlo
# (speed-to-lead). Assistente Vapi dedicato "Qualifica Lead".
# ----------------------------------------------------------------------------
LEAD_ASSISTANT_ID = os.getenv("VAPI_LEAD_ASSISTANT_ID", "")


@router.post("/chiama-lead", dependencies=[Depends(check_auth)])
async def chiama_lead(request: Request, db: asyncpg.Pool = Depends(get_db)):
    params, tc = await _in(request)
    nome = str(params.get("nome") or "").strip()
    numero = params.get("numero") or params.get("telefono")
    note = str(params.get("note") or "").strip()

    key = os.getenv("VAPI_API_KEY", "")
    if not key or not LEAD_ASSISTANT_ID:
        return _out({"avviata": False, "messaggio": "La chiamata ai lead non e' ancora configurata."}, tc)
    num = _e164_it(str(numero)) if numero else None
    if not num:
        return _out({"avviata": False, "messaggio": "Numero del lead mancante o non valido."}, tc)

    saluto = "Buongiorno" if _now_roma().hour < 13 else "Buonasera"
    body = {
        "assistantId": LEAD_ASSISTANT_ID,
        "phoneNumberId": CALLER_PHONE_ID,
        "customer": {"number": num},
        "assistantOverrides": {"variableValues": {"nome": (nome or ""), "saluto": saluto}},
        "metadata": {"lead": True, "nome": nome, "telefono": num, "note": note[:120]},
    }
    ok = False
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.post(VAPI_CALL_URL,
                               headers={"Authorization": "Bearer " + key,
                                        "Content-Type": "application/json"},
                               json=body)
        ok = r.status_code in (200, 201)
    except Exception:
        ok = False
    return _out({"avviata": ok, "nome": nome,
                 "messaggio": (f"Sto chiamando {nome or 'il lead'} per qualificarlo, ti mando l'esito."
                               if ok else "Non sono riuscito a far partire la chiamata.")}, tc)
