"""Caccia: gli immobili seguiti da piu' agenzie, messi in ordine di importanza.

La vista v_caccia calcola per ognuno due punteggi diversi, perche' "importante"
vuol dire due cose che non coincidono:
  - punteggio          quanto conviene provare a prendere il mandato
                       (tante agenzie, prezzo fuori mercato, da mesi sul mercato)
  - punteggio_affare   quanto conviene proporlo ai nostri clienti
                       (sotto mercato, con richieste attive che lo vogliono)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncpg

from app.config import get_db

router = APIRouter(prefix="/api/caccia", tags=["caccia"])

FASI = [
    "da_lavorare",
    "ricerca_contatti",
    "visura_richiesta",
    "contattato",
    "appuntamento_fissato",
    "mandato_preso",
    "perso",
]
# Le fasi in cui un immobile occupa davvero tempo di Ilan.
FASI_APERTE = ("ricerca_contatti", "visura_richiesta", "contattato", "appuntamento_fissato")
TETTO_IN_CACCIA = 10


class CacciaPatch(BaseModel):
    fase: Optional[str] = None
    proprietario_nome: Optional[str] = None
    proprietario_cognome: Optional[str] = None
    proprietario_telefono: Optional[str] = None
    proprietario_email: Optional[str] = None
    proprietario_note: Optional[str] = None
    esito_finale: Optional[str] = None
    attivo: Optional[bool] = None


@router.get("")
async def lista(
    ordina: str = "acquisizione",
    tipo: Optional[str] = None,
    zona: Optional[str] = None,
    fase: Optional[str] = None,
    punteggio_min: Optional[int] = None,
    solo_con_clienti: bool = False,
    solo_doppi: bool = False,
    solo_attendibili: bool = True,
    prezzo_min: Optional[int] = None,
    max_agenzie: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: asyncpg.Pool = Depends(get_db),
):
    conditions = ["TRUE"]
    params = []
    i = 1

    # Qui finisce la Bacheca. Faceva una cosa sola che Caccia non faceva:
    # buttare via le righe con dati che non tornano, perche' alla seconda
    # scheda sbagliata di un elenco non ci si fida piu' e tanto vale non
    # averlo. Ora quel controllo e' una colonna della vista e vale di
    # default, ma e' un filtro e non una censura: solo_attendibili=false
    # rimette tutto, motivo_inattendibile dice perche' una riga era fuori.
    if solo_attendibili:
        conditions.append("attendibile")
    if prezzo_min is not None:
        conditions.append(f"prezzo >= ${i}"); params.append(prezzo_min); i += 1
    if max_agenzie is not None:
        conditions.append(f"COALESCE(num_agenzie, 0) <= ${i}"); params.append(max_agenzie); i += 1

    if tipo == "vendita":
        conditions.append("e_affitto = FALSE")
    elif tipo == "affitto":
        conditions.append("e_affitto = TRUE")

    if zona:
        conditions.append(f"zona ILIKE ${i}"); params.append(f"%{zona}%"); i += 1
    if fase:
        conditions.append(f"fase = ${i}"); params.append(fase); i += 1
    if punteggio_min is not None:
        colonna = "punteggio_affare" if ordina == "affare" else "punteggio"
        conditions.append(f"{colonna} >= ${i}"); params.append(punteggio_min); i += 1
    if solo_con_clienti:
        conditions.append("clienti_interessati > 0")
    if solo_doppi:
        conditions.append("doppio_vendita_affitto = TRUE")
    if search:
        conditions.append(f"(indirizzo ILIKE ${i} OR zona ILIKE ${i})")
        params.append(f"%{search}%"); i += 1

    where = " AND ".join(conditions)

    # Il 09/11/2026 Ilan ha cliccato i link di Corso Italia 16 e non si e'
    # aperto niente: gli annunci erano spariti dal portale a giugno. Su 1.529
    # schede attive, 282 non avevano un annuncio rivisto da oltre un mese, e
    # il CRM non lo diceva da nessuna parte. Una scheda ferma non e' falsa,
    # e' vecchia: non si nasconde, si manda in coda. Chi non ha nemmeno un
    # annuncio nel censimento va in fondo del tutto.
    FRESCHEZZA = (
        "CASE WHEN giorni_dall_ultimo_annuncio IS NULL THEN 2 "
        "     WHEN giorni_dall_ultimo_annuncio > 30 THEN 1 "
        "     ELSE 0 END"
    )
    order = {
        "acquisizione": "punteggio DESC, clienti_interessati DESC",
        "affare": "punteggio_affare DESC, scarto_pct ASC",
        "recenti": "primo_visto DESC",
        "prezzo": "prezzo DESC NULLS LAST",
    }.get(ordina, "punteggio DESC")
    order = f"{FRESCHEZZA}, {order}"

    query = f"""
        SELECT * FROM public.v_caccia_completa
        WHERE {where}
        ORDER BY {order}
        LIMIT ${i} OFFSET ${i+1}
    """
    params.extend([limit, offset])

    async with db.acquire() as conn:
        rows = await conn.fetch(query, *params)
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM public.v_caccia_completa WHERE {where}", *params[:-2]
        )

    return {"data": [dict(r) for r in rows], "total": total}


@router.get("/stats")
async def stats(db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        per_fase = await conn.fetch(
            "SELECT fase, COUNT(*) AS n FROM public.v_caccia_completa GROUP BY fase ORDER BY n DESC"
        )
        aperte = await conn.fetchval(
            "SELECT COUNT(*) FROM public.v_caccia_completa WHERE fase = ANY($1::text[])",
            list(FASI_APERTE),
        )
        totali = await conn.fetchrow(
            """
            SELECT COUNT(*) AS totale,
                   COUNT(*) FILTER (WHERE clienti_interessati > 0) AS con_clienti,
                   COUNT(*) FILTER (WHERE doppio_vendita_affitto)  AS doppi,
                   COUNT(*) FILTER (WHERE punteggio >= 70)         AS alta_priorita
            FROM public.v_caccia_completa
            """
        )
    return {
        "per_fase": [dict(r) for r in per_fase],
        "in_caccia": aperte,
        "tetto": TETTO_IN_CACCIA,
        **dict(totali),
    }


@router.get("/{immobile_id}")
async def dettaglio(immobile_id: int, db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM public.v_caccia_completa WHERE id = $1", immobile_id)
        if not row:
            raise HTTPException(404, "Immobile non trovato")
        simili = await conn.fetch(
            """
            SELECT id, indirizzo, zona, mq, prezzo, e_affitto, primo_visto
            FROM public.v_caccia_completa
            WHERE via_norm = $1 AND id <> $2
            ORDER BY primo_visto DESC LIMIT 10
            """,
            row["via_norm"], immobile_id,
        )
    return {"immobile": dict(row), "stessa_via": [dict(r) for r in simili]}


@router.patch("/{immobile_id}")
async def aggiorna(immobile_id: int, patch: CacciaPatch, db: asyncpg.Pool = Depends(get_db)):
    dati = patch.model_dump(exclude_unset=True, exclude_none=True)
    if not dati:
        raise HTTPException(400, "Niente da aggiornare")

    fase = dati.pop("fase", None)
    if fase and fase not in FASI:
        raise HTTPException(400, f"Fase sconosciuta: {fase}")

    async with db.acquire() as conn:
        attuale = await conn.fetchrow(
            "SELECT mandato_status FROM public.immobili_pluricondivisi WHERE id = $1",
            immobile_id,
        )
        if not attuale:
            raise HTTPException(404, "Immobile non trovato")

        # Il tetto serve a questo: finche' non chiudi quelle che hai aperto, non
        # ne entrano altre. Senza, la coda torna com'era - milleduecento aperte
        # e nessuna lavorata.
        if fase in FASI_APERTE and attuale["mandato_status"] not in FASI_APERTE:
            aperte = await conn.fetchval(
                "SELECT COUNT(*) FROM public.immobili_pluricondivisi "
                "WHERE attivo AND mandato_status = ANY($1::text[])",
                list(FASI_APERTE),
            )
            if aperte >= TETTO_IN_CACCIA:
                raise HTTPException(
                    409,
                    f"Hai gia' {aperte} immobili in caccia (tetto {TETTO_IN_CACCIA}). "
                    "Chiudine uno prima di aprirne un altro.",
                )

        campi, valori, i = [], [], 1
        if fase:
            campi.append(f"mandato_status = ${i}"); valori.append(fase); i += 1
            campi.append(f"mandato_status_updated_at = NOW()")
            if fase == "contattato":
                campi.append("contattato_at = COALESCE(contattato_at, NOW())")
        for k, v in dati.items():
            campi.append(f"{k} = ${i}"); valori.append(v); i += 1
        campi.append("updated_at = NOW()")

        valori.append(immobile_id)
        await conn.execute(
            f"UPDATE public.immobili_pluricondivisi SET {', '.join(campi)} WHERE id = ${i}",
            *valori,
        )
        row = await conn.fetchrow("SELECT * FROM public.v_caccia_completa WHERE id = $1", immobile_id)

    return dict(row) if row else {"ok": True}
