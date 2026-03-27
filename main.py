"""
Logging Relay - KISS & YAGNI Implementation
Single file, minimal dependencies, essential features only.
"""

import logging
import os
import json
import hmac
import time
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import redis.asyncio as redis
import httpx

load_dotenv()


# --- CONFIG: Fail fast on startup ---
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY")
REDIS_URL = os.getenv("REDIS_URL")
LOKI_BASE_URL = os.getenv("LOKI_BASE_URL")
LOKI_USERNAME = os.getenv("LOKI_USERNAME")
LOKI_PASSWORD = os.getenv("LOKI_PASSWORD")

if not all([REDIS_URL, LOKI_BASE_URL, MASTER_ADMIN_KEY]):
    raise ValueError("REDIS_URL, LOKI_BASE_URL, MASTER_ADMIN_KEY required")


# --- LIFESPAN: Simple resource management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis.from_url(REDIS_URL, decode_responses=True)
    yield
    await app.state.redis.close()


app = FastAPI(title="Relay", lifespan=lifespan)


# --- SCHEMAS: Only what we need ---
class LogIn(BaseModel):
    project: str = Field(pattern=r'^[a-z0-9-]{1,32}$')
    level: str = Field(default="info", pattern=r'^(info|warn|error)$')
    message: str = Field(max_length=10000)


class ProjectIn(BaseModel):
    name: str = Field(pattern=r'^[a-z0-9-]{1,32}$')
    token: str = Field(min_length=16, max_length=128)


# --- DEPENDENCIES: Simple auth ---
async def auth(project: str, authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")

    token = authorization[7:]
    stored = await app.state.redis.hget("tokens", project)

    if not stored or not hmac.compare_digest(token, stored):
        raise HTTPException(403, "Invalid token")

    return token


# --- LOKI: Async push with auth ---
async def push_loki(project: str, level: str, message: str, meta: dict):
    payload = {
        "streams": [{
            "stream": {"project": project, "level": level},
            "values": [[str(int(time.time() * 1e9)), message, json.dumps(meta)]]
        }]
    }

    auth = (LOKI_USERNAME, LOKI_PASSWORD) if LOKI_USERNAME and LOKI_PASSWORD else None

    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{LOKI_BASE_URL}/loki/api/v1/push",
            json=payload,
            auth=auth,  # Basic auth jika ada
            timeout=10
        )
        r.raise_for_status()


# --- ENDPOINTS ---
@app.post("/log")
async def log(payload: LogIn, req: Request, _: str = Depends(auth)):
    meta = {
        "ip": req.client.host if req.client else None,
        "ua": req.headers.get("user-agent"),
        "ts": time.time()
    }

    # Fire-and-forget: Redis (buffer) + Loki (index)
    pipe = app.state.redis.pipeline()
    pipe.xadd(f"logs:{payload.project}", {
        "level": payload.level,
        "msg": payload.message[:1000],  # Truncate for Redis
        "meta": json.dumps(meta)
    }, maxlen=1000, approximate=True)
    pipe.expire(f"logs:{payload.project}", 86400)  # 1 day retention
    await pipe.execute()

    # Async push to Loki (fail silently, Redis already has it)
    try:
        await push_loki(payload.project, payload.level, payload.message, meta)
    except Exception:
        pass  # Redis is source of truth

    return {"ok": True}


@app.post("/admin/project")
async def register(body: ProjectIn, key: str):
    if not hmac.compare_digest(key, MASTER_ADMIN_KEY):
        raise HTTPException(403)

    await app.state.redis.hset("tokens", body.name, body.token)
    return {"project": body.name}


@app.get("/health")
async def health():
    try:
        await app.state.redis.ping()
        return {"status": "ok"}
    except Exception:
        return JSONResponse({"status": "down"}, status_code=503)
