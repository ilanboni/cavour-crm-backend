from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import get_db, close_db
from app.routers import clienti, immobili, scouting, matching, richieste
from app.routers.operativo import comm_router, appt_router, doc_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_db()
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
)

app.include_router(clienti.router)
app.include_router(immobili.router)
app.include_router(richieste.router)
app.include_router(matching.router)
app.include_router(scouting.router)
app.include_router(scouting.scouting_router)
app.include_router(comm_router)
app.include_router(appt_router)
app.include_router(doc_router)

@app.get("/")
async def root():
    return {
        "app": "Cavour Immobiliare CRM",
        "version": "2.0.0",
        "status": "online"

@"
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import get_db, close_db
from app.routers import clienti, immobili, scouting, matching, richieste
from app.routers.operativo import comm_router, appt_router, doc_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_db()
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
)

app.include_router(clienti.router)
app.include_router(immobili.router)
app.include_router(richieste.router)
app.include_router(matching.router)
app.include_router(scouting.router)
app.include_router(scouting.scouting_router)
app.include_router(comm_router)
app.include_router(appt_router)
app.include_router(doc_router)

@app.get("/")
async def root():
    return {
        "app": "Cavour Immobiliare CRM",
        "version": "2.0.0",
        "status": "online"
    }

@app.get("/health")
async def health():
    db = await get_db()
    async with db.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "healthy", "database": "connected"}
