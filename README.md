# Vendor Gateway (V2)

All new work for the WhatsApp Vendor Gateway lives in this folder. The old Node.js project
(`../../Turabees-Whatsapp-Bot`) is left untouched and is used as reference only.

Source of truth: `VENDOR_GATEWAY_PLAN.md`. Vendor contract: `ai-contract.md`.

## Layout

| Path | What |
|---|---|
| `backend/` | FastAPI app (routers, services, repositories, models), Alembic migrations, tests |
| `docker-compose.yml` | Dev stack: Postgres, Redis, API |
| `Makefile` | `make up`, `make test`, `make lint`, `make typecheck` |

## Quick start

```bash
make up                  # creates .env (fresh SECRET_ENCRYPTION_KEY), builds, runs migrations
curl localhost:8000/ready
make seed                # 3 demo vendors (demo-canned, demo-echo, demo-mockbot); keys printed once
make gateway ARGS=list-vendors
make test                # needs Docker (testcontainers)
make lint typecheck
```

Host ports: API `8000`, Postgres `5433`, Redis `6380` (so they do not clash with local installs).
