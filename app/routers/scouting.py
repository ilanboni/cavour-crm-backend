from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime
import asyncpg
from app.config import get_db

router = APIRouter(prefix="/api/immobili-esterni", tags=["scouting"])

class ImmobileEsternoCreate(BaseModel):
    titolo: str
    descrizione: Optional[str] = None
    indirizzo: Optional[str] = None
    zona: Optional[str] = None
    citta: str = "Milano"
    mq: Optional[int] = None
    piano: Optional[int] = None
    camere: Optional[int] = None
    bagni: Optional[int] = None
    prezzo: Optional[Decimal] = None
    contatto_nome: Optional[str] = None
    contatto_telefono: Optional[str] = None
    contatto_email: Optional[str] = None
    url_annuncio: Optional[str] = None
    tipo_fonte: str = "privato"
    fonte: str = "casafari"
    testo_originale: Optional[str] = None
    id_portale: Optional[str] = None
    id_web: Optional[str] = None
    ascensore: bool = False
    balcone: bool = False
    terrazzo: bool = False
    box: bool = False
    note: Optional[str] = None

@router.get("")
async def lista_immobili_esterni(
    tipo_fonte: Optional[str] = None,
    stato_contatto: Optional[str] = None,
    zona: Optional[str] = None,
    attivo: bool = True,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: asyncpg.Pool = Depends(get_db)
):
    conditions = ["attivo = $1"]
    params = [attivo]
    i = 2

    if tipo_fonte:
        conditions.append(f"tipo_fonte = ${i}"); params.append(tipo_fonte); i += 1
    if stato_contatto:
        conditions.append(f"stato_contatto = ${i}"); params.append(stato_contatto); i += 1
    if zona:
        conditions.append(f"zona ILIKE ${i}"); params.append(f"%{zona}%"); i += 1
    if search:
        conditions.append(f"(titolo ILIKE ${i} OR indirizzo ILIKE ${i})")
        params.append(f"%{search}%"); i += 1

    where = " AND ".join(conditions)
    query = f"""
        SELECT * FROM public.immobili_esterni
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${i} OFFSET ${i+1}
    """
    params.extend([limit, offset])

    async with db.acquire() as conn:
        rows = await conn.fetch(query, *params)
        count_query = f"SELECT COUNT(*) FROM public.immobili_esterni WHERE {where}"
        total = await conn.fetchval(count_query, *params[:-2])

    return {"data": [dict(r) for r in rows], "total": total}

@router.get("/privati")
async def lista_privati(
    zona: Optional[str] = None,
    limit: int = 50,
    db: asyncpg.Pool = Depends(get_db)
):
    """Immobili da privati — priorità alta per contatto diretto"""
    conditions = ["tipo_fonte = 'privato'", "attivo = true", "stato_contatto = 'nuovo'"]
    params = []
    i = 1

    if zona:
        conditions.append(f"zona ILIKE ${i}"); params.append(f"%{zona}%"); i += 1

    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM public.immobili_esterni WHERE {where} ORDER BY created_at DESC LIMIT ${i}",
            *params, limit
        )
    return [dict(r) for r in rows]

@router.get("/multiagenzia")
async def lista_multiagenzia(
    zona: Optional[str] = None,
    limit: int = 50,
    db: asyncpg.Pool = Depends(get_db)
):
    """Immobili multi-agenzia — opportunità mandato"""
    conditions = ["tipo_fonte = 'multiagenzia'", "attivo = true"]
    params = []
    i = 1

    if zona:
        conditions.append(f"zona ILIKE ${i}"); params.append(f"%{zona}%"); i += 1

    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM public.immobili_esterni WHERE {where} ORDER BY score_compatibilita DESC LIMIT ${i}",
            *params, limit
        )
    return [dict(r) for r in rows]

@router.get("/{immobile_id}")
async def get_immobile_esterno(immobile_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.immobili_esterni WHERE id = $1", immobile_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Immobile non trovato")
    return dict(row)

@router.post("")
async def crea_immobile_esterno(imm: ImmobileEsternoCreate, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        # Evita duplicati per id_portale
        if imm.id_portale:
            existing = await conn.fetchval(
                "SELECT id FROM public.immobili_esterni WHERE id_portale = $1",
                imm.id_portale
            )
            if existing:
                return {"id": existing, "duplicate": True}

        row = await conn.fetchrow("""
            INSERT INTO public.immobili_esterni
                (titolo, descrizione, indirizzo, zona, citta, mq, piano,
                 camere, bagni, prezzo, contatto_nome, contatto_telefono,
                 contatto_email, url_annuncio, tipo_fonte, fonte,
                 testo_originale, id_portale, id_web,
                 ascensore, balcone, terrazzo, box, note)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                    $16,$17,$18,$19,$20,$21,$22,$23,$24)
            RETURNING *
        """,
            imm.titolo, imm.descrizione, imm.indirizzo, imm.zona, imm.citta,
            imm.mq, imm.piano, imm.camere, imm.bagni, imm.prezzo,
            imm.contatto_nome, imm.contatto_telefono, imm.contatto_email,
            imm.url_annuncio, imm.tipo_fonte, imm.fonte, imm.testo_originale,
            imm.id_portale, imm.id_web,
            imm.ascensore, imm.balcone, imm.terrazzo, imm.box, imm.note
        )
    return dict(row)

@router.patch("/{immobile_id}/stato-contatto")
async def aggiorna_stato_contatto(
    immobile_id: int,
    stato: str,
    messaggio: Optional[str] = None,
    db: asyncpg.Pool = Depends(get_db)
):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE public.immobili_esterni
            SET stato_contatto = $2,
                messaggio_inviato = COALESCE($3, messaggio_inviato),
                data_contatto = CASE WHEN $2 = 'contattato' THEN NOW() ELSE data_contatto END,
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
        """, immobile_id, stato, messaggio)
    if not row:
        raise HTTPException(status_code=404, detail="Immobile non trovato")
    return dict(row)


# ─── SCOUTING GIORNALIERO ────────────────────────────────────────────────────

scouting_router = APIRouter(prefix="/api/scouting", tags=["scouting"])

@scouting_router.get("/oggi")
async def scouting_oggi(db: asyncpg.Pool = Depends(get_db)):
    """Report scouting di oggi"""
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.*,
                   c.nome as cliente_nome, c.cognome as cliente_cognome,
                   ie.titolo, ie.indirizzo, ie.zona, ie.prezzo, ie.mq,
                   ie.tipo_fonte, ie.contatto_telefono, ie.url_annuncio
            FROM public.scouting_giornaliero s
            JOIN public.clienti c ON s.cliente_id = c.id
            JOIN public.immobili_esterni ie ON s.immobile_esterno_id = ie.id
            WHERE s.data_scouting = CURRENT_DATE
            ORDER BY s.score_compatibilita DESC
        """)
    return [dict(r) for r in rows]

@scouting_router.get("/report/{data}")
async def report_giornaliero(data: str, db: asyncpg.Pool = Depends(get_db)):
    """Report di un giorno specifico (formato: YYYY-MM-DD)"""
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.report_giornaliero WHERE data = $1", data
        )
    return dict(row) if row else {}

@scouting_router.get("/stats")
async def stats_scouting(giorni: int = 7, db: asyncpg.Pool = Depends(get_db)):
    """Statistiche ultimi N giorni"""
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT data,
                   COUNT(*) as totale,
                   COUNT(*) FILTER (WHERE classificazione = 'privato') as privati,
                   COUNT(*) FILTER (WHERE classificazione = 'multiagenzia') as multiagenzia,
                   COUNT(*) FILTER (WHERE notificato = true) as notificati
            FROM public.scouting_giornaliero
            WHERE data_scouting >= CURRENT_DATE - $1::interval
            GROUP BY data
            ORDER BY data DESC
        """, f"{giorni} days")
    return [dict(r) for r in rows]

@scouting_router.post("")
async def crea_scouting(
    data_scouting: str,
    cliente_id: int,
    immobile_esterno_id: int,
    portale: str = "casafari",
    classificazione: str = "privato",
    score_compatibilita: int = 0,
    db: asyncpg.Pool = Depends(get_db)
):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO public.scouting_giornaliero
                (data_scouting, cliente_id, immobile_esterno_id,
                 portale, classificazione, score_compatibilita)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (data_scouting, cliente_id, immobile_esterno_id) DO NOTHING
            RETURNING *
        """, data_scouting, cliente_id, immobile_esterno_id,
            portale, classificazione, score_compatibilita)
    return dict(row) if row else {"duplicate": True}

@scouting_router.get("/clienti-attivi")
async def clienti_attivi_con_richieste(db: asyncpg.Pool = Depends(get_db)):
    """Restituisce tutti i clienti con richieste attive e i loro link di ricerca.
    Usato da Cowork per sapere quali clienti cercare ogni mattina."""
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.id as richiesta_id,
                   r.cliente_id,
                   c.appellativo, c.nome, c.cognome, c.telefono,
                   r.zona, r.budget_massimo, r.mq_minimi, r.mq_massimi,
                   r.camere_minime, r.stato_buono, r.stato_ristrutturato,
                   r.stato_nuovo, r.stato_da_ristrutturare,
                   r.ascensore, r.balcone, r.terrazzo, r.box,
                   r.link_ricerca, r.link_ricerca_immobiliare,
                   r.priorita, r.note_agente, r.descrizione_libera
            FROM public.richieste r
            JOIN public.clienti c ON r.cliente_id = c.id
            WHERE r.attiva = true
            ORDER BY r.priorita DESC
        """)
    return [dict(r) for r in rows]
