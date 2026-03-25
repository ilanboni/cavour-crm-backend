from fastapi import APIRouter, Depends
import anthropic
import asyncpg
from app.config import get_db, settings

router = APIRouter(prefix="/api/coach", tags=["coach"])

@router.get("/briefing")
async def get_briefing(db: asyncpg.Pool = Depends(get_db)):
    async with db.acquire() as conn:
        n_clienti = await conn.fetchval("SELECT COUNT(*) FROM public.clienti WHERE attivo = true")
        n_privati = await conn.fetchval("SELECT COUNT(*) FROM public.immobili_esterni WHERE tipo_fonte = 'privato' AND stato_contatto = 'nuovo' AND attivo = true")
        n_matching = await conn.fetchval("SELECT COUNT(*) FROM public.matching WHERE proposto = false")
        n_immobili = await conn.fetchval("SELECT COUNT(*) FROM public.immobili WHERE attivo = true")

    from datetime import datetime
    ora = datetime.now().hour
    saluto = "Buongiorno" if ora < 12 else "Buon pomeriggio" if ora < 18 else "Buonasera"

    if not settings.anthropic_api_key:
        return {"message": f"{saluto} Ilan. {n_privati} privati da contattare. {n_matching} matching pronti. Ogni contatto rimandato è un mandato perso."}

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": f"""Sei il coach personale di Ilan Boni, agente immobiliare boutique a Milano (Cavour Immobiliare).
Scrivi UN messaggio motivazionale breve (2-3 frasi) in italiano, diretto e concreto, basato su questi dati:
- {n_privati} immobili da privati nuovi da contattare
- {n_matching} matching nuovi da proporre
- {n_clienti} clienti attivi totali
- {n_immobili} immobili in gestione
- Ora: {ora}:00

Tono: coach duro ma supportivo. Usa il nome Ilan. Cita i numeri concreti. NO emoji. Solo il messaggio."""
            }]
        )
        return {"message": response.content[0].text}
    except Exception as e:
        return {"message": f"{saluto} Ilan. {n_privati} privati da contattare oggi. {n_matching} matching pronti da proporre. Non perdere tempo."}
