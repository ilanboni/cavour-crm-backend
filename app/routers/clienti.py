from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import asyncpg
from app.config import get_db

router = APIRouter(prefix="/api/clienti", tags=["clienti"])

class ClienteCreate(BaseModel):
    appellativo: Optional[str] = None
    nome: str
    cognome: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    compleanno: Optional[str] = None
    religione: Optional[str] = None
    note: Optional[str] = None
    ruolo: str = "acquirente"
    fonte_acquisizione: Optional[str] = None
    stato_trattativa: str = "attivo"
    rating: int = 3
    cliente_amico: bool = False

class ClienteUpdate(BaseModel):
    appellativo: Optional[str] = None
    nome: Optional[str] = None
    cognome: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    note: Optional[str] = None
    ruolo: Optional[str] = None
    stato_trattativa: Optional[str] = None
    rating: Optional[int] = None
    cliente_amico: Optional[bool] = None
    attivo: Optional[bool] = None

@router.get("")
async def lista_clienti(
    ruolo: Optional[str] = None,
    attivo: Optional[bool] = True,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: asyncpg.Pool = Depends(get_db)
):
    conditions = ["1=1"]
    params = []
    i = 1
    if attivo is not None:
        conditions.append(f"attivo = ${i}")
        params.append(attivo)
        i += 1
    if ruolo:
        conditions.append(f"ruolo = ${i}")
        params.append(ruolo)
        i += 1
    if search:
        conditions.append(f"(nome ILIKE ${i} OR cognome ILIKE ${i} OR telefono ILIKE ${i})")
        params.append(f"%{search}%")
        i += 1
    where = " AND ".join(conditions)
    query = f"SELECT * FROM public.clienti WHERE {where} ORDER BY updated_at DESC LIMIT ${i} OFFSET ${i+1}"
    params.extend([limit, offset])
    async with db.acquire() as conn:
        rows = await conn.fetch(query, *params)
        total = await conn.fetchval(f"SELECT COUNT(*) FROM public.clienti WHERE {where}", *params[:-2])
    return {"data": [dict(r) for r in rows], "total": total}

@router.get("/{cliente_id}")
async def get_cliente(cliente_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM public.clienti WHERE id = $1", cliente_id)
    if not row:
        raise HTTPException(status_code=404, detail="Cliente non trovato")
    return dict(row)

@router.get("/{cliente_id}/comunicazioni")
async def get_comunicazioni_cliente(cliente_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.*, i.titolo as immobile_titolo, i.indirizzo as immobile_indirizzo
            FROM public.comunicazioni c
            LEFT JOIN public.immobili i ON c.immobile_id = i.id
            WHERE c.cliente_id = $1 ORDER BY c.data_ora DESC
        """, cliente_id)
    return [dict(r) for r in rows]

@router.get("/{cliente_id}/appuntamenti")
async def get_appuntamenti_cliente(cliente_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.*, i.titolo as immobile_titolo
            FROM public.appuntamenti a
            LEFT JOIN public.immobili i ON a.immobile_id = i.id
            WHERE a.cliente_id = $1 ORDER BY a.data_ora DESC
        """, cliente_id)
    return [dict(r) for r in rows]

@router.get("/{cliente_id}/documenti")
async def get_documenti_cliente(cliente_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT d.*, i.titolo as immobile_titolo
            FROM public.documenti d
            LEFT JOIN public.immobili i ON d.immobile_id = i.id
            WHERE d.cliente_id = $1 ORDER BY d.created_at DESC
        """, cliente_id)
    return [dict(r) for r in rows]

@router.post("")
async def crea_cliente(cliente: ClienteCreate, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO public.clienti
                (appellativo, nome, cognome, telefono, email, compleanno,
                 religione, note, ruolo, fonte_acquisizione, stato_trattativa,
                 rating, cliente_amico)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING *
        """, cliente.appellativo, cliente.nome, cliente.cognome,
            cliente.telefono, cliente.email, cliente.compleanno,
            cliente.religione, cliente.note, cliente.ruolo,
            cliente.fonte_acquisizione, cliente.stato_trattativa,
            cliente.rating, cliente.cliente_amico)
    return dict(row)

@router.patch("/{cliente_id}")
async def aggiorna_cliente(cliente_id: int, cliente: ClienteUpdate, db: asyncpg.Pool = Depends(get_db)):
    updates = {k: v for k, v in cliente.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")
    set_clause = ", ".join([f"{k} = ${i+2}" for i, k in enumerate(updates.keys())])
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE public.clienti SET {set_clause}, updated_at = NOW() WHERE id = $1 RETURNING *",
            cliente_id, *list(updates.values()))
    if not row:
        raise HTTPException(status_code=404, detail="Cliente non trovato")
    return dict(row)

@router.delete("/{cliente_id}")
async def elimina_cliente(cliente_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        await conn.execute("UPDATE public.clienti SET attivo = false WHERE id = $1", cliente_id)
    return {"success": True}

@router.get("/{cliente_id}/matching-esterni")
async def get_matching_esterni(
    cliente_id: int,
    classificazione: Optional[str] = None,
    db: asyncpg.Pool = Depends(get_db),
):
    """Immobili del mercato che combaciano con la richiesta attiva del cliente.

    Tre provenienze, tenute distinte perche' si lavorano in modo diverso:
      - privato        -> si tratta con il proprietario, nessuna provvigione da dividere
      - multiagenzia   -> piu' agenzie sullo stesso immobile: il venditore e' gia' stanco
      - esclusiva      -> una sola agenzia: probabile incarico in esclusiva, si collabora

    Il multiagenzia NON viene dal flag immobili_esterni.multi_agenzia, che e' un
    residuo fermo a 110 righe: viene da immobili_pluricondivisi, ricostruita ogni
    notte confrontando gli annunci, che di righe ne ha oltre 1.400.
    """
    async with db.acquire() as conn:
        richiesta = await conn.fetchrow("""
            SELECT * FROM public.richieste
            WHERE cliente_id = $1 AND COALESCE(attiva, true)
            ORDER BY COALESCE(rating_richiesta, 0) DESC, id DESC
            LIMIT 1
        """, cliente_id)
        if not richiesta:
            return {"richiesta": None, "immobili": [], "conteggi": {}}

        mq_min = (richiesta["mq_minimi"] or 0) - 15
        mq_max = (richiesta["mq_massimi"] or 100000) + 15
        pr_min = float(richiesta["budget_minimo"] or 0) * 0.85
        pr_max = float(richiesta["budget_massimo"] or 10**9) * 1.10
        zone = [z.strip().lower() for z in (richiesta["zona"] or "").split(",")
                if len(z.strip()) >= 3]
        if not zone:
            zone = [""]

        esterni = await conn.fetch("""
            SELECT e.id, e.indirizzo, e.zona, e.mq, e.prezzo, e.locali, e.camere, e.bagni,
                   e.piano, e.titolo, e.url_annuncio, e.portale, e.contatto_telefono,
                   e.created_at,
                   CASE WHEN e.tipo_fonte = 'privato' THEN 'privato'
                        ELSE 'esclusiva' END AS classificazione,
                   1 AS num_agenzie, NULL::jsonb AS lista_agenzie,
                   NULL::int AS giorni_sul_mercato
            FROM public.immobili_esterni e
            WHERE COALESCE(e.attivo, true)
              AND COALESCE(e.multi_agenzia, false) = false
              AND e.mq BETWEEN $1 AND $2
              AND e.prezzo BETWEEN $3 AND $4
              AND EXISTS (
                    SELECT 1 FROM unnest($5::text[]) AS z
                    WHERE lower(COALESCE(e.zona,'') || ' ' || COALESCE(e.indirizzo,''))
                          LIKE '%' || z || '%')
            ORDER BY e.created_at DESC NULLS LAST
            LIMIT 300
        """, mq_min, mq_max, pr_min, pr_max, zone)

        plur = await conn.fetch("""
            SELECT p.id, p.indirizzo, p.zona, p.mq, p.prezzo, p.locali, p.camere, p.bagni,
                   p.piano, p.titolo, NULL::text AS url_annuncio, p.fonte AS portale,
                   p.proprietario_telefono AS contatto_telefono, p.created_at,
                   'multiagenzia' AS classificazione,
                   p.num_agenzie, p.lista_agenzie, p.giorni_sul_mercato
            FROM public.immobili_pluricondivisi p
            WHERE COALESCE(p.attivo, true)
              AND COALESCE(p.num_agenzie, 0) >= 2
              AND p.mq BETWEEN $1 AND $2
              AND p.prezzo BETWEEN $3 AND $4
              AND EXISTS (
                    SELECT 1 FROM unnest($5::text[]) AS z
                    WHERE lower(COALESCE(p.zona,'') || ' ' || COALESCE(p.indirizzo,''))
                          LIKE '%' || z || '%')
            ORDER BY COALESCE(p.num_agenzie,0) DESC, p.created_at DESC NULLS LAST
            LIMIT 300
        """, mq_min, mq_max, pr_min, pr_max, zone)

    immobili = [dict(r) for r in esterni] + [dict(r) for r in plur]
    conteggi = {"privato": 0, "multiagenzia": 0, "esclusiva": 0}
    for i in immobili:
        conteggi[i["classificazione"]] = conteggi.get(i["classificazione"], 0) + 1
    if classificazione:
        immobili = [i for i in immobili if i["classificazione"] == classificazione]
    immobili.sort(key=lambda i: (i.get("created_at") is None, i.get("created_at")), reverse=True)

    return {
        "richiesta": {
            "id": richiesta["id"], "zona": richiesta["zona"],
            "mq_minimi": richiesta["mq_minimi"], "mq_massimi": richiesta["mq_massimi"],
            "budget_minimo": float(richiesta["budget_minimo"] or 0),
            "budget_massimo": float(richiesta["budget_massimo"] or 0),
        },
        "conteggi": conteggi,
        "immobili": immobili,
    }
