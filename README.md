# Logging Relay

A minimal HTTP-to-Loki log forwarder with Redis buffering.

## What It Does

Receives logs via HTTP → stores in Redis (buffer) → forwards to Loki (indexing).

## Quick Start

```bash
# 1. Set env
export REDIS_URL="redis://localhost:6379"
export LOKI_URL="http://localhost:3100"
export ADMIN_KEY="your-secret-key"

# 2. Run
pip install fastapi redis httpx uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Usage

**Register a project:**
```bash
curl -X POST "http://localhost:8000/admin/project?key=your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"name":"myapp","token":"random-token-123"}'
```

**Send logs:**
```bash
curl -X POST "http://localhost:8000/log" \
  -H "Authorization: Bearer random-token-123" \
  -H "Content-Type: application/json" \
  -d '{"project":"myapp","level":"error","message":"Something broke"}'
```

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/log` | POST | Send a log (needs Bearer token) |
| `/admin/project` | POST | Register new project (needs admin key) |
| `/health` | GET | Check if Redis is connected |

## How It Works

1. Client sends log with project token
2. Relay validates token against Redis
3. Log saved to Redis Stream (backup)
4. Log forwarded to Loki (indexing)
5. If Loki fails, Redis keeps the log

## Stack

- FastAPI (HTTP API)
- Redis (buffer / token store)
- Loki (log storage)
- httpx (async HTTP)
