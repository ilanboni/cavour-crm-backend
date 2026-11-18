from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import get_db, close_db, prova_connessione
from app.resilienza import degrada_con_grazia
from app.routers import clienti, immobili, scouting, matching, richieste, coach, voice, paolo_voice, caccia
from app.routers.operativo import comm_router, appt_router, doc_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Non blocca l'avvio: se il database non risponde l'app parte lo stesso.
    await prova_connessione()
    yield
    await close_db()

app = FastAPI(
    title="Cavour Immobiliare CRM",
    description="API backend per CRM immobiliare Cavour",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Senza expose_headers il browser NON fa vedere a React le intestazioni
    # nostre: la fascia "dati vecchi" non comparirebbe mai, e il bug sarebbe
    # invisibile perche' la risposta arriva comunque.
    expose_headers=["X-Degradato", "X-Dati-Vecchi"],
)

# Quando il database non risponde, i GET servono l'ultima copia buona con le
# intestazioni X-Degradato e X-Dati-Vecchi invece di una schermata vuota.
# Il perche' sta in cima a app/resilienza.py.
app.middleware("http")(degrada_con_grazia)

app.include_router(clienti.router)
app.include_router(immobili.router)
app.include_router(richieste.router)
app.include_router(matching.router)
app.include_router(scouting.router)
app.include_router(scouting.scouting_router)
app.include_router(caccia.router)
app.include_router(comm_router)
app.include_router(appt_router)
app.include_router(doc_router)
app.include_router(coach.router)
app.include_router(voice.router)
app.include_router(paolo_voice.router)

@app.get("/")
async def root():
    return {"app": "Cavour Immobiliare CRM", "version": "2.0.0", "status": "online"}

@app.get("/health")
async def health():
    try:
        db = await get_db()
        async with db.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "healthy", "database": "connected"}
    except Exception as exc:
        return {"status": "degraded", "database": "unreachable", "detail": str(exc)[:200]}
