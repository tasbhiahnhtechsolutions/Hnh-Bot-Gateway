# Vendor Gateway (V2)

All new work for the WhatsApp Vendor Gateway lives in this folder. The old Node.js project
(`../../Turabees-Whatsapp-Bot`) is left untouched and is used as reference only.

Source of truth: `VENDOR_GATEWAY_PLAN.md`. Vendor contract: `ai-contract.md`.

## Layout

| Path | What |
|---|---|
| `backend/` | FastAPI app (routers, services, repositories, models), Alembic migrations, tests |
| `extension/` | Chrome extension (MV3): popup, background, content, injected, build script, Node tests |
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

## What works (Phase 1, step 3)

```bash
make seed                                   # prints one LIVE key per demo vendor
curl localhost:8000/v1/plugin/whoami -H "X-API-Key: wab_live_..."
curl localhost:8000/v1/ai-reply -H "X-API-Key: wab_live_..." -H 'Content-Type: application/json' \
  -d '{"chatId":"923001112222@c.us","incomingMessages":[{"text":"price?","timestamp":"19:30:12"}]}'
make gateway ARGS="simulate-incoming <vendor-id> --text 'hello' --scenario error500"
```

Inbound modes: `forward`, `canned`, `echo`, `mock_bot`, `paused`. Bot failures answer the plugin with
502/504 and nothing is sent to the customer.

Host ports: API `8000`, Postgres `5433`, Redis `6380` (so they do not clash with local installs).

## Chrome extension (Phase 1, step 4)

```bash
make ext-test                 # node --check + 65 Node tests (no browser needed)
make ext-dev                  # builds extension/dist/dev (gateway http://localhost:8000)
make ext-prod URL=https://api.example.com
```

1. `make up && make seed`, copy a demo vendor's LIVE key.
2. Chrome -> `chrome://extensions` -> Developer mode -> Load unpacked -> `extension/dist/dev`.
3. Open the popup, paste the key, Save. It shows "Connected as <business> +92...".
4. Open web.whatsapp.com and message that number from another phone.

staging/prod builds need an https public URL (`--url` or `GATEWAY_STAGING_URL` / `GATEWAY_PROD_URL`)
and contain no localhost permission.
