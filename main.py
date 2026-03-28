"""
Logging Relay - KISS & YAGNI Implementation
Updated for Dynamic Project Registration & Appname Labeling
"""

import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, Optional

import httpx
import logging_loki
import redis.asyncio as redis
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

# --- CONFIG ---
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY")
REDIS_URL = os.getenv("REDIS_URL")
LOKI_BASE_URL = os.getenv("LOKI_BASE_URL")
LOKI_USERNAME = os.getenv("LOKI_USERNAME")
LOKI_PASSWORD = os.getenv("LOKI_PASSWORD")

if not all([REDIS_URL, LOKI_BASE_URL, MASTER_ADMIN_KEY]):
    raise ValueError("REDIS_URL, LOKI_BASE_URL, MASTER_ADMIN_KEY required")

# --- LOGGING SETUP ---
loki_handler = logging_loki.LokiHandler(
    url=f"{LOKI_BASE_URL}/loki/api/v1/push",
    tags={"service": "logging-relay"},
    auth=(LOKI_USERNAME, LOKI_PASSWORD) if LOKI_USERNAME and LOKI_PASSWORD else None,
    version="1",
)

logger = logging.getLogger("relay")
logger.addHandler(loki_handler)
logger.setLevel(logging.INFO)

# Standard console logging as well
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis.from_url(REDIS_URL, decode_responses=True)
    yield
    await app.state.redis.aclose()


app = FastAPI(title="Relay", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- SCHEMAS ---
class LogIn(BaseModel):
    project: str = Field(..., pattern=r"^[a-z0-9-]{1,32}$")
    level: str = Field(default="info", pattern=r"^(info|warn|error|critical)$")
    message: str = Field(..., max_length=10000)
    metadata: Optional[Dict] = {}


class ProjectIn(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9-]{1,32}$")
    token: str = Field(..., min_length=16, max_length=128)
    admin_key: str  # Admin key dimasukkan ke body untuk keamanan


# --- AUTH DEPENDENCY ---
async def auth(payload: LogIn, authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")

    token = authorization[7:]
    # Cek token berdasarkan project ID yang ada di body request
    stored = await app.state.redis.hget("relay:tokens", payload.project)

    if not stored or not hmac.compare_digest(token, stored):
        raise HTTPException(403, "Invalid token for this project")

    return token


# --- RELAY LOG PUSH (Async, No Standard Logging) ---
async def push_relay_log(project: str, level: str, message: str, meta: dict):
    """
    Sends relayed logs to Loki using httpx directly to avoid mixing with server logs.
    """
    payload = {
        "streams": [
            {
                "stream": {
                    "service": project,
                    "appname": project,
                    "level": level,
                    "env": meta.get("env", "prod"),
                },
                "values": [[str(int(time.time() * 1e9)), f"[{level.upper()}] {message} {json.dumps(meta)}"]],
            }
        ]
    }

    auth = (LOKI_USERNAME, LOKI_PASSWORD) if LOKI_USERNAME and LOKI_PASSWORD else None

    async with httpx.AsyncClient() as c:
        try:
            r = await c.post(
                f"{LOKI_BASE_URL}/loki/api/v1/push",
                json=payload,
                auth=auth,
                timeout=5,
            )
            r.raise_for_status()
        except Exception as e:
            # Internal server log for failures
            logger.error(f"Relay log push failed for project {project}: {e}")


# --- ENDPOINTS ---


@app.post("/log")
async def log(payload: LogIn, req: Request, _: str = Depends(auth)):
    meta = {"ip": req.client.host if req.client else None, "ua": req.headers.get("user-agent"), **payload.metadata}

    # 1. Simpan ke Redis Stream (Buffer & Real-time view)
    await app.state.redis.xadd(
        f"logs:{payload.project}",
        {"level": payload.level, "msg": payload.message[:1000], "meta": json.dumps(meta)},
        maxlen=1000,
        approximate=True,
    )

    # 2. Push ke Loki (Relayed Log - Separated from server logs)
    await push_relay_log(payload.project, payload.level, payload.message, meta)

    return {"ok": True}


# --- ADMIN ENDPOINTS (Dinamis via Body) ---


@app.post("/admin/project")
async def register(body: ProjectIn):
    if not hmac.compare_digest(body.admin_key, MASTER_ADMIN_KEY):
        raise HTTPException(403, "Invalid Admin Key")

    await app.state.redis.hset("relay:tokens", body.name, body.token)
    return {"status": "registered", "project": body.name}


@app.get("/admin/projects")
async def list_projects(admin_key: str):
    if not hmac.compare_digest(admin_key, MASTER_ADMIN_KEY):
        raise HTTPException(403)

    projects = await app.state.redis.hkeys("relay:tokens")
    return {"projects": projects}


@app.delete("/admin/project/{name}")
async def delete_project(name: str, admin_key: str):
    if not hmac.compare_digest(admin_key, MASTER_ADMIN_KEY):
        raise HTTPException(403)

    await app.state.redis.hdel("relay:tokens", name)
    # Opsional: Hapus juga stream log-nya jika ingin bersih total
    await app.state.redis.delete(f"logs:{name}")
    return {"status": "deleted", "project": name}


@app.get("/health")
async def health():
    results = {"status": "ok", "redis": "unknown", "loki": "unknown"}

    # 1. Check Redis
    try:
        await app.state.redis.ping()
        results["redis"] = "connected"
    except Exception:
        results["redis"] = "disconnected"
        results["status"] = "error"

    # 2. Check Loki
    async with httpx.AsyncClient() as client:
        try:
            # Loki usually provides a /ready endpoint
            r = await client.get(f"{LOKI_BASE_URL}/ready", timeout=2)
            if r.status_code == 200 and r.text.strip() == "ready":
                results["loki"] = "connected"
            else:
                results["loki"] = f"unhealthy ({r.status_code})"
                results["status"] = "error"
        except Exception as e:
            results["loki"] = f"error: {str(e)}"
            results["status"] = "error"

    status_code = 200 if results["status"] == "ok" else 503
    return JSONResponse(results, status_code=status_code)
