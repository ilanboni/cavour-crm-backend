from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from decimal import Decimal
import asyncpg
from app.config import get_db

router = APIRouter(prefix="/api/immobili-esterni", tags=["scouting"])
scouting_router = APIRouter(prefix="/api/scouting", tags=["scouting"])

class ImmobileEsternoCreate(BaseModel):
    titolo: str
    descrizione: Optional[str] = None
    indirizzo: Optional[str] = None
    zona: Optional[str] = None
    citta: str = "Milano"
    mq: Optional[int] = None
    camere: Optional[int] = None
    prezzo: Optional[Decimal] = None
    contatto_nome: Optional[str] = None
    contatto_telefono: Optional[str] = None
    url_annuncio: Optional[str] = None
    tipo_fonte: str = "privato"
    fonte: str = "casafari"
    testo_originale: Optional[str] = None
    id_portale: Optional[str] = None

@router.get("")
async def lista_immobili_esterni(
    tipo_fonte: Optional[str] = None,
    stato_contatto: Optional[str] = None,
    zona: Optional[str] = None,
    attivo: bool = True,
    limit: int = 100,
    offset: int = 0,
    db: asyncpg.Pool = Depends(get_db)
):
    conditions = ["attivo = \"]
    params = [attivo]
    i = 2
    if tipo_fonte:
        conditions.append(f"tipo_fonte = \"); params.append(tipo_fonte); i += 1
    if stato_contatto:
        conditions.append(f"stato_contatto = \"); params.append(stato_contatto); i += 1
    if zona:
        conditions.append(f"zona ILIKE \"); params.append(f"%{zona}%"); i += 1
    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(f"SELECT * FROM public.immobili_esterni WHERE {where} ORDER BY created_at DESC LIMIT \ OFFSET \", *params, limit, offset)
        total = await conn.fetchval(f"SELECT COUNT(*) FROM public.immobili_esterni WHERE {where}", *params)
    return {"data": [dict(r) for r in rows], "total": total}

@router.get("/privati")
async def lista_privati(zona: Optional[str] = None, limit: int = 50, db: asyncpg.Pool = Depends(get_db)):
    conditions = ["tipo_fonte = 'privato'", "attivo = true", "stato_contatto = 'nuovo'"]
    params = []
    i = 1
    if zona:
        conditions.append(f"zona ILIKE \"); params.append(f"%{zona}%"); i += 1
    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(f"SELECT * FROM public.immobili_esterni WHERE {where} ORDER BY created_at DESC LIMIT \", *params, limit)
    return [dict(r) for r in rows]

@router.get("/multiagenzia")
async def lista_multiagenzia(zona: Optional[str] = None, limit: int = 50, db: asyncpg.Pool = Depends(get_db)):
    conditions = ["tipo_fonte = 'multiagenzia'", "attivo = true"]
    params = []
    i = 1
    if zona:
        conditions.append(f"zona ILIKE \"); params.append(f"%{zona}%"); i += 1
    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(f"SELECT * FROM public.immobili_esterni WHERE {where} ORDER BY score_compatibilita DESC LIMIT \", *params, limit)
    return [dict(r) for r in rows]

@router.get("/{immobile_id}")
async def get_immobile_esterno(immobile_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM public.immobili_esterni WHERE id = \", immobile_id)
    if not row:
        raise HTTPException(status_code=404, detail="Immobile non trovato")
    return dict(row)

@router.post("")
async def crea_immobile_esterno(imm: ImmobileEsternoCreate, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        if imm.id_portale:
            existing = await conn.fetchval("SELECT id FROM public.immobili_esterni WHERE id_portale = \", imm.id_portale)
            if existing:
                return {"id": existing, "duplicate": True}
        row = await conn.fetchrow("""
            INSERT INTO public.immobili_esterni
                (titolo, descrizione, indirizzo, zona, citta, mq, camere,
                 prezzo, contatto_nome, contatto_telefono, url_annuncio,
                 tipo_fonte, fonte, testo_originale, id_portale)
            VALUES (\,\,\,\,\,\,\,\,\,\,\,\,\,\,\)
            RETURNING *
        """, imm.titolo, imm.descrizione, imm.indirizzo, imm.zona, imm.citta,
            imm.mq, imm.camere, imm.prezzo, imm.contatto_nome,
            imm.contatto_telefono, imm.url_annuncio, imm.tipo_fonte,
            imm.fonte, imm.testo_originale, imm.id_portale)
    return dict(row)

@router.patch("/{immobile_id}/stato-contatto")
async def aggiorna_stato(immobile_id: int, stato: str, messaggio: Optional[str] = None, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE public.immobili_esterni
            SET stato_contatto = \, messaggio_inviato = COALESCE(\, messaggio_inviato),
                data_contatto = CASE WHEN \ = 'contattato' THEN NOW() ELSE data_contatto END,
                updated_at = NOW()
            WHERE id = \ RETURNING *
        """, immobile_id, stato, messaggio)
    if not row:
        raise HTTPException(status_code=404, detail="Immobile non trovato")
    return dict(row)

@scouting_router.get("/oggi")
async def scouting_oggi(db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.*, c.nome as cliente_nome, c.cognome as cliente_cognome,
                   ie.titolo, ie.indirizzo, ie.zona, ie.prezzo, ie.tipo_fonte
            FROM public.scouting_giornaliero s
            JOIN public.clienti c ON s.cliente_id = c.id
            JOIN public.immobili_esterni ie ON s.immobile_esterno_id = ie.id
            WHERE s.data_scouting = CURRENT_DATE
            ORDER BY s.score_compatibilita DESC
        """)
    return [dict(r) for r in rows]

@scouting_router.get("/report/{data}")
async def report_giornaliero(data: str, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM public.report_giornaliero WHERE data = \", data)
    return dict(row) if row else {}
