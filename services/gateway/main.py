from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.config import settings
from core.database import init_db_async, run_migrations_async
from services.gateway.routers import meetings, tasks, prompts, documents


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_async()
    await run_migrations_async()
    yield


app = FastAPI(
    title="Meeting Assistant API",
    version="0.1.0",
    description="AI-powered meeting assistant REST API",
    lifespan=lifespan,
)

# Serve local storage files at /storage/{key}
_storage_dir = Path(settings.local_storage_path)
_storage_dir.mkdir(parents=True, exist_ok=True)
app.mount("/storage", StaticFiles(directory=str(_storage_dir)), name="local-storage")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meetings.router, prefix="/meetings", tags=["meetings"])
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(prompts.router, prefix="/prompts", tags=["prompts"])
app.include_router(documents.router, prefix="/documents", tags=["documents"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/info")
def info():
    return {
        "stt_model": settings.stt_model,
        "stt_service_url": settings.stt_service_url,
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
    }


@app.get("/probe/stt")
async def probe_stt(url: str = Query(...)):
    """Proxy-probe the STT service health endpoint to avoid CORS issues."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{url.rstrip('/')}/health")
        if r.status_code == 200:
            return {"ok": True, "detail": "reachable"}
        return {"ok": False, "detail": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


@app.get("/probe/llm")
async def probe_llm(url: str = Query(...), api_key: Optional[str] = Query(None)):
    """Probe LLM server via GET /v1/models. Returns available model IDs on success."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # Normalize: strip trailing /v1 so we always append /v1/models once
    base = url.rstrip('/')
    if base.endswith('/v1'):
        base = base[:-3]
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(base + "/v1/models", headers=headers)
        if r.status_code == 200:
            body = r.json()
            models = [m["id"] for m in body.get("data", [])]
            return {"ok": True, "detail": "reachable", "models": models}
        return {"ok": False, "detail": f"HTTP {r.status_code}", "models": []}
    except Exception as e:
        return {"ok": False, "detail": str(e), "models": []}


@app.get("/")
def serve_frontend():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
