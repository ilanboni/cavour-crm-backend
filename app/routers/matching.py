from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
import asyncpg
from app.config import get_db

router = APIRouter(prefix="/api/matching", tags=["matching"])

@router.get("")
async def lista_matching(
    richiesta_id: Optional[int] = None,
    proposto: Optional[bool] = None,
    limit: int = 50,
    db: asyncpg.Pool = Depends(get_db)
):
    conditions = ["1=1"]
    params = []
    i = 1
    if richiesta_id:
        conditions.append(f"m.richiesta_id = ${i}")
        params.append(richiesta_id)
        i += 1
    if proposto is not None:
        conditions.append(f"m.proposto = ${i}")
        params.append(proposto)
        i += 1
    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT m.*,
                   r.zona as richiesta_zona, r.budget_massimo,
                   c.nome as cliente_nome, c.cognome as cliente_cognome,
                   c.telefono as cliente_telefono,
                   i.titolo as immobile_titolo, i.indirizzo as immobile_indirizzo,
                   ie.titolo as immobile_esterno_titolo
            FROM public.matching m
            JOIN public.richieste r ON m.richiesta_id = r.id
            JOIN public.clienti c ON r.cliente_id = c.id
            LEFT JOIN public.immobili i ON m.immobile_id = i.id
            LEFT JOIN public.immobili_esterni ie ON m.immobile_esterno_id = ie.id
            WHERE {where}
            ORDER BY m.punteggio DESC LIMIT ${i}
        """, *params, limit)
    return [dict(r) for r in rows]

@router.post("/calcola")
async def calcola_matching(db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        richieste = await conn.fetch("""
            SELECT r.*, c.nome as cliente_nome FROM public.richieste r
            JOIN public.clienti c ON r.cliente_id = c.id WHERE r.attiva = true
        """)
        immobili = await conn.fetch("""
            SELECT * FROM public.immobili
            WHERE stato_vendita = 'disponibile' AND attivo = true
        """)
        immobili_esterni = await conn.fetch("""
            SELECT * FROM public.immobili_esterni
            WHERE attivo = true AND stato_contatto = 'nuovo'
            ORDER BY created_at DESC LIMIT 50
        """)

    if not richieste:
        return {"message": "Nessuna richiesta attiva", "matching_creati": 0}

    matching_creati = 0
    for richiesta in richieste:
        r = dict(richiesta)
        for imm in immobili:
            i = dict(imm)
            score = _score(r, i)
            if score >= 40:
                async with db.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO public.matching (richiesta_id, immobile_id, punteggio)
                        VALUES ($1,$2,$3) ON CONFLICT DO NOTHING
                    """, r["id"], i["id"], score)
                matching_creati += 1
        for imm in immobili_esterni:
            i = dict(imm)
            score = _score(r, i)
            if score >= 40:
                async with db.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO public.matching (richiesta_id, immobile_esterno_id, punteggio)
                        VALUES ($1,$2,$3) ON CONFLICT DO NOTHING
                    """, r["id"], i["id"], score)
                matching_creati += 1

    return {"message": "Matching completato", "matching_creati": matching_creati}

def _score(richiesta, immobile):
    score = 0
    if richiesta.get("budget_massimo") and immobile.get("prezzo"):
        if immobile["prezzo"] <= richiesta["budget_massimo"]:
            score += 30
        elif immobile["prezzo"] <= richiesta["budget_massimo"] * 1.1:
            score += 15
    if richiesta.get("mq_minimi") and immobile.get("mq"):
        if immobile["mq"] >= richiesta["mq_minimi"]:
            score += 20
    if richiesta.get("zona") and immobile.get("zona"):
        if richiesta["zona"].lower() in immobile["zona"].lower():
            score += 25
    if richiesta.get("camere_minime") and immobile.get("camere"):
        if immobile["camere"] >= richiesta["camere_minime"]:
            score += 15
    return min(score, 100)

@router.patch("/{matching_id}/feedback")
async def aggiorna_feedback(matching_id: int, accettato: bool, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE public.matching SET accettato = $2 WHERE id = $1 RETURNING *",
            matching_id, accettato)
    if not row:
        raise HTTPException(status_code=404, detail="Matching non trovato")
    return dict(row)

@router.patch("/{matching_id}/proposto")
async def segna_proposto(matching_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE public.matching SET proposto = true WHERE id = $1 RETURNING *",
            matching_id)
    if not row:
        raise HTTPException(status_code=404, detail="Matching non trovato")
    return dict(row)
