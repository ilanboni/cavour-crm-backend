from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from decimal import Decimal
import asyncpg
from app.config import get_db

router = APIRouter(prefix="/api/immobili", tags=["immobili"])

class ImmobileCreate(BaseModel):
    proprietario_id: Optional[int] = None
    titolo: str
    descrizione: Optional[str] = None
    tipo_contratto: str = "vendita"
    indirizzo: Optional[str] = None
    zona: Optional[str] = None
    citta: str = "Milano"
    mq: Optional[int] = None
    piano: Optional[int] = None
    camere: Optional[int] = None
    bagni: Optional[int] = None
    prezzo: Optional[Decimal] = None
    ascensore: bool = False
    balcone: bool = False
    terrazzo: bool = False
    box: bool = False
    stato_vendita: str = "disponibile"
    note_interne: Optional[str] = None

class ImmobileUpdate(BaseModel):
    titolo: Optional[str] = None
    descrizione: Optional[str] = None
    prezzo: Optional[Decimal] = None
    stato_vendita: Optional[str] = None
    note_interne: Optional[str] = None
    attivo: Optional[bool] = None

@router.get("")
async def lista_immobili(
    tipo_contratto: Optional[str] = None,
    stato_vendita: Optional[str] = None,
    attivo: Optional[bool] = True,
    limit: int = 100,
    db: asyncpg.Pool = Depends(get_db)
):
    conditions = ["im.attivo = $1"]
    params = [attivo]
    i = 2
    if tipo_contratto:
        conditions.append(f"im.tipo_contratto = ${i}")
        params.append(tipo_contratto)
        i += 1
    if stato_vendita:
        conditions.append(f"im.stato_vendita = ${i}")
        params.append(stato_vendita)
        i += 1
    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT im.*, c.nome as proprietario_nome, c.cognome as proprietario_cognome,
                   c.telefono as proprietario_telefono
            FROM public.immobili im
            LEFT JOIN public.clienti c ON im.proprietario_id = c.id
            WHERE {where} ORDER BY im.updated_at DESC LIMIT ${i}
        """, *params, limit)
    return [dict(r) for r in rows]

@router.get("/{immobile_id}")
async def get_immobile(immobile_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT im.*, c.nome as proprietario_nome, c.cognome as proprietario_cognome
            FROM public.immobili im
            LEFT JOIN public.clienti c ON im.proprietario_id = c.id
            WHERE im.id = $1
        """, immobile_id)
    if not row:
        raise HTTPException(status_code=404, detail="Immobile non trovato")
    return dict(row)

@router.get("/{immobile_id}/comunicazioni")
async def get_comunicazioni_immobile(immobile_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.*, cl.nome as cliente_nome, cl.cognome as cliente_cognome
            FROM public.comunicazioni c
            LEFT JOIN public.clienti cl ON c.cliente_id = cl.id
            WHERE c.immobile_id = $1 ORDER BY c.data_ora DESC
        """, immobile_id)
    return [dict(r) for r in rows]

@router.get("/{immobile_id}/appuntamenti")
async def get_appuntamenti_immobile(immobile_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.*, c.nome as cliente_nome, c.cognome as cliente_cognome
            FROM public.appuntamenti a
            LEFT JOIN public.clienti c ON a.cliente_id = c.id
            WHERE a.immobile_id = $1 ORDER BY a.data_ora DESC
        """, immobile_id)
    return [dict(r) for r in rows]

@router.post("")
async def crea_immobile(immobile: ImmobileCreate, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO public.immobili
                (proprietario_id, titolo, descrizione, tipo_contratto,
                 indirizzo, zona, citta, mq, piano, camere, bagni, prezzo,
                 ascensore, balcone, terrazzo, box, stato_vendita, note_interne)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
            RETURNING *
        """, immobile.proprietario_id, immobile.titolo, immobile.descrizione,
            immobile.tipo_contratto, immobile.indirizzo, immobile.zona,
            immobile.citta, immobile.mq, immobile.piano, immobile.camere,
            immobile.bagni, immobile.prezzo, immobile.ascensore,
            immobile.balcone, immobile.terrazzo, immobile.box,
            immobile.stato_vendita, immobile.note_interne)
    return dict(row)

@router.patch("/{immobile_id}")
async def aggiorna_immobile(immobile_id: int, immobile: ImmobileUpdate, db: asyncpg.Pool = Depends(get_db)):
    updates = {k: v for k, v in immobile.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")
    set_clause = ", ".join([f"{k} = ${i+2}" for i, k in enumerate(updates.keys())])
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE public.immobili SET {set_clause}, updated_at = NOW() WHERE id = $1 RETURNING *",
            immobile_id, *list(updates.values()))
    if not row:
        raise HTTPException(status_code=404, detail="Immobile non trovato")
    return dict(row)
