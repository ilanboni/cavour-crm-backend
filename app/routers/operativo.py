from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import asyncpg
from app.config import get_db

comm_router = APIRouter(prefix="/api/comunicazioni", tags=["operativo"])
appt_router = APIRouter(prefix="/api/appuntamenti", tags=["operativo"])
doc_router = APIRouter(prefix="/api/documenti", tags=["operativo"])

class ComunicazioneCreate(BaseModel):
    cliente_id: Optional[int] = None
    immobile_id: Optional[int] = None
    immobile_esterno_id: Optional[int] = None
    tipo: str = "nota"
    testo: str
    canale: str = "manuale"
    esito: Optional[str] = None
    direzione: str = "uscente"

@comm_router.get("")
async def lista_comunicazioni(cliente_id: Optional[int] = None, immobile_id: Optional[int] = None, limit: int = 50, db: asyncpg.Pool = Depends(get_db)):
    conditions = ["1=1"]
    params = []
    i = 1
    if cliente_id:
        conditions.append(f"c.cliente_id = \"); params.append(cliente_id); i += 1
    if immobile_id:
        conditions.append(f"c.immobile_id = \"); params.append(immobile_id); i += 1
    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT c.*, cl.nome as cliente_nome, cl.cognome as cliente_cognome,
                   i.titolo as immobile_titolo
            FROM public.comunicazioni c
            LEFT JOIN public.clienti cl ON c.cliente_id = cl.id
            LEFT JOIN public.immobili i ON c.immobile_id = i.id
            WHERE {where} ORDER BY c.data_ora DESC LIMIT \
        """, *params, limit)
    return [dict(r) for r in rows]

@comm_router.post("")
async def crea_comunicazione(comm: ComunicazioneCreate, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO public.comunicazioni (cliente_id, immobile_id, immobile_esterno_id, tipo, testo, canale, esito, direzione, creato_da)
            VALUES (\,\,\,\,\,\,\,\,'ilan') RETURNING *
        """, comm.cliente_id, comm.immobile_id, comm.immobile_esterno_id, comm.tipo, comm.testo, comm.canale, comm.esito, comm.direzione)
    return dict(row)

@comm_router.delete("/{comm_id}")
async def elimina_comunicazione(comm_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM public.comunicazioni WHERE id = \", comm_id)
    return {"success": True}

class AppuntamentoCreate(BaseModel):
    cliente_id: int
    immobile_id: Optional[int] = None
    immobile_esterno_id: Optional[int] = None
    data_ora: datetime
    luogo: Optional[str] = None
    tipo: str = "visita"
    note: Optional[str] = None

class AppuntamentoUpdate(BaseModel):
    data_ora: Optional[datetime] = None
    luogo: Optional[str] = None
    note: Optional[str] = None
    confermato: Optional[bool] = None
    completato: Optional[bool] = None
    esito: Optional[str] = None

@appt_router.get("")
async def lista_appuntamenti(completato: Optional[bool] = None, limit: int = 50, db: asyncpg.Pool = Depends(get_db)):
    conditions = ["1=1"]
    params = []
    i = 1
    if completato is not None:
        conditions.append(f"a.completato = \"); params.append(completato); i += 1
    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT a.*, c.nome as cliente_nome, c.cognome as cliente_cognome,
                   i.titolo as immobile_titolo
            FROM public.appuntamenti a
            LEFT JOIN public.clienti c ON a.cliente_id = c.id
            LEFT JOIN public.immobili i ON a.immobile_id = i.id
            WHERE {where} ORDER BY a.data_ora ASC LIMIT \
        """, *params, limit)
    return [dict(r) for r in rows]

@appt_router.post("")
async def crea_appuntamento(appt: AppuntamentoCreate, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO public.appuntamenti (cliente_id, immobile_id, immobile_esterno_id, data_ora, luogo, tipo, note)
            VALUES (\,\,\,\,\,\,\) RETURNING *
        """, appt.cliente_id, appt.immobile_id, appt.immobile_esterno_id, appt.data_ora, appt.luogo, appt.tipo, appt.note)
    return dict(row)

@appt_router.patch("/{appt_id}")
async def aggiorna_appuntamento(appt_id: int, appt: AppuntamentoUpdate, db: asyncpg.Pool = Depends(get_db)):
    updates = {k: v for k, v in appt.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")
    set_clause = ", ".join([f"{k} = \" for i, k in enumerate(updates.keys())])
    async with db.acquire() as conn:
        row = await conn.fetchrow(f"UPDATE public.appuntamenti SET {set_clause} WHERE id = \ RETURNING *", appt_id, *list(updates.values()))
    if not row:
        raise HTTPException(status_code=404, detail="Appuntamento non trovato")
    return dict(row)

class DocumentoCreate(BaseModel):
    immobile_id: Optional[int] = None
    cliente_id: Optional[int] = None
    tipo: str = "altro"
    nome: str
    url: Optional[str] = None
    note: Optional[str] = None

@doc_router.get("")
async def lista_documenti(immobile_id: Optional[int] = None, cliente_id: Optional[int] = None, db: asyncpg.Pool = Depends(get_db)):
    conditions = ["1=1"]
    params = []
    i = 1
    if immobile_id:
        conditions.append(f"d.immobile_id = \"); params.append(immobile_id); i += 1
    if cliente_id:
        conditions.append(f"d.cliente_id = \"); params.append(cliente_id); i += 1
    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT d.*, i.titolo as immobile_titolo, c.nome as cliente_nome
            FROM public.documenti d
            LEFT JOIN public.immobili i ON d.immobile_id = i.id
            LEFT JOIN public.clienti c ON d.cliente_id = c.id
            WHERE {where} ORDER BY d.created_at DESC
        """, *params)
    return [dict(r) for r in rows]

@doc_router.post("")
async def crea_documento(doc: DocumentoCreate, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO public.documenti (immobile_id, cliente_id, tipo, nome, url, note)
            VALUES (\,\,\,\,\,\) RETURNING *
        """, doc.immobile_id, doc.cliente_id, doc.tipo, doc.nome, doc.url, doc.note)
    return dict(row)

@doc_router.delete("/{doc_id}")
async def elimina_documento(doc_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM public.documenti WHERE id = \", doc_id)
    return {"success": True}
