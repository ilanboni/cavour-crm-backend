from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from decimal import Decimal
from datetime import date
import asyncpg
from app.config import get_db

router = APIRouter(prefix="/api/richieste", tags=["richieste"])

class RichiestaCreate(BaseModel):
    cliente_id: int
    descrizione_libera: Optional[str] = None
    tipo_contratto: str = "vendita"
    budget_minimo: Optional[Decimal] = None
    budget_massimo: Optional[Decimal] = None
    mq_minimi: Optional[int] = None
    zona: Optional[str] = None
    ascensore: bool = False
    balcone: bool = False
    terrazzo: bool = False
    box: bool = False
    camere_minime: Optional[int] = None
    urgenza: str = "normale"
    priorita: int = 3
    note_agente: Optional[str] = None

@router.get("")
async def lista_richieste(
    cliente_id: Optional[int] = None,
    attiva: Optional[bool] = True,
    db: asyncpg.Pool = Depends(get_db)
):
    conditions = ["1=1"]
    params = []
    i = 1
    if cliente_id:
        conditions.append(f"r.cliente_id = \"); params.append(cliente_id); i += 1
    if attiva is not None:
        conditions.append(f"r.attiva = \"); params.append(attiva); i += 1
    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT r.*, c.nome as cliente_nome, c.cognome as cliente_cognome,
                   c.telefono as cliente_telefono
            FROM public.richieste r
            JOIN public.clienti c ON r.cliente_id = c.id
            WHERE {where}
            ORDER BY r.priorita DESC, r.created_at DESC
        """, *params)
    return [dict(r) for r in rows]

@router.get("/{richiesta_id}")
async def get_richiesta(richiesta_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT r.*, c.nome as cliente_nome, c.cognome as cliente_cognome
            FROM public.richieste r
            JOIN public.clienti c ON r.cliente_id = c.id
            WHERE r.id = \
        """, richiesta_id)
    if not row:
        raise HTTPException(status_code=404, detail="Richiesta non trovata")
    return dict(row)

@router.post("")
async def crea_richiesta(richiesta: RichiestaCreate, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO public.richieste
                (cliente_id, descrizione_libera, tipo_contratto,
                 budget_minimo, budget_massimo, mq_minimi, zona,
                 ascensore, balcone, terrazzo, box, camere_minime,
                 urgenza, priorita, note_agente)
            VALUES (\,\,\,\,\,\,\,\,\,\,\,\,\,\,\)
            RETURNING *
        """, richiesta.cliente_id, richiesta.descrizione_libera,
            richiesta.tipo_contratto, richiesta.budget_minimo,
            richiesta.budget_massimo, richiesta.mq_minimi, richiesta.zona,
            richiesta.ascensore, richiesta.balcone, richiesta.terrazzo,
            richiesta.box, richiesta.camere_minime,
            richiesta.urgenza, richiesta.priorita, richiesta.note_agente)
    return dict(row)

@router.patch("/{richiesta_id}/disattiva")
async def disattiva_richiesta(richiesta_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        await conn.execute("UPDATE public.richieste SET attiva = false WHERE id = \", richiesta_id)
    return {"success": True}
