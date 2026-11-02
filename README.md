# Vendor Gateway (V2)

All new work for the WhatsApp Vendor Gateway lives in this folder. The old Node.js project
(`../../Turabees-Whatsapp-Bot`) is left untouched and is used as reference only.

Source of truth: `VENDOR_GATEWAY_PLAN.md`. Vendor contract: `ai-contract.md`.

## Layout

| Path | What |
|---|---|
| `backend/` | FastAPI app (routers, services, repositories, models), Alembic migrations, tests |
| `extension/` | Chrome extension (MV3): popup, background, content, injected, build script, Node tests |
| `reference-bot/` | Example vendor bot (FastAPI) that follows `ai-contract.md`; vendors can copy it |
| `docker-compose.yml` | Dev stack: Postgres, Redis, API (+ reference bot with `make bot`) |
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

## Reference bot (Phase 1, step 6)

`reference-bot/` is a standalone FastAPI app that follows `ai-contract.md`: it checks the HMAC
signature, understands every media type and answers with `{"replies": [...]}`. Vendors can copy it.

```bash
make gateway ARGS="create-vendor --name 'Ref Bot Shop' --number 923001230099 --email r@x.com --bot-url http://reference-bot:9000"
make bot BOT_SECRET=whsec_...          # the Bot secret printed above; starts it on :9000
make gateway ARGS="test-bot <vendor-id>"
make bot-test bot-lint
```

See `reference-bot/README.md`.

## Admin panel and Test Lab (Phase 1B, step 5)

```bash
make seed                      # also prints a dev admin (admin@example.com) password, once
make gateway ARGS="create-admin --email you@example.com --generate"   # real admins
```

Open http://localhost:8000/admin. Pages: dashboard, vendors, create vendor (keys shown once),
vendor detail (modes, suspend, rotate/revoke keys, canned rules, recent requests), Test Lab
(text/file simulation, optional real bot), bot connection test, request logs, audit log.
No JavaScript, no CDN. Roles: `superadmin` (everything) and `support` (view + Test Lab).
`ADMIN_IP_ALLOWLIST` restricts the panel to given IPs/CIDRs. Sandbox for vendor developers:
`POST /v1/sandbox/simulate-incoming` with a TEST key.

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
