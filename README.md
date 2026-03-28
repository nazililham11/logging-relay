# Logging Relay

A minimal HTTP-to-Loki log forwarder with Redis buffering.

## What It Does

Receives logs via HTTP → stores in Redis (buffer) → forwards to Loki (indexing).

## Quick Start

```bash
# 1. Set env
export ADMIN_KEY="your-secret-key"  
export REDIS_URL="redis://localhost:6379"
export LOKI_URL="http://localhost:3100"
export LOKI_USERNAME="your-loki-username"
export LOKI_PASSWORD="your-loki-password"

# 2. Run
pip install fastapi redis httpx uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Usage

**Register a project:**
```bash
curl -X POST "http://localhost:8000/admin/project" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "myapp",
    "token": "your-secret-token-32-chars",
    "admin_key": "your-master-admin-key"
  }'
```

**Send logs:**
```bash
curl -X POST "http://localhost:8000/log" \
  -H "Authorization: Bearer your-secret-token-32-chars" \
  -H "Content-Type: application/json" \
  -d '{
    "project": "myapp",
    "level": "error",
    "message": "Something broke!",
    "metadata": {"env": "production", "user_id": 123}
  }'
```

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/log` | POST | Send log context (Bearer token auth) |
| `/admin/project` | POST | Register project (body: name, token, admin_key) |
| `/admin/projects` | GET | List projects (query: admin_key) |
| `/admin/project/{name}` | DELETE | Delete project (query: admin_key) |
| `/health` | GET | Redis & System health check |

## Client Examples

### Python
```python
import requests

def relay_log(level, message, meta=None):
    requests.post(
        "http://localhost:8000/log",
        headers={"Authorization": "Bearer your-project-token"},
        json={
            "project": "myapp",
            "level": level,
            "message": message,
            "metadata": meta or {}
        }
    )

relay_log("info", "Hello from Python", {"user": "admin"})
```

### JavaScript (Node.js / Browser)
```javascript
async function relayLog(level, message, meta = {}) {
  await fetch("http://localhost:8000/log", {
    method: "POST",
    headers: {
      "Authorization": "Bearer your-project-token",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      project: "myapp",
      level: level,
      message: message,
      metadata: meta
    })
  });
}

relayLog("error", "Something went wrong", { page: "/home" });
```

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
- python-logging-loki (internal server logs)
- httpx (async log relay)
