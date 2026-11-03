# Vendor Gateway (V2)

All new work for the WhatsApp Vendor Gateway lives in this folder. The old Node.js project
(`../../Turabees-Whatsapp-Bot`) is left untouched and is used as reference only.

Source of truth: `VENDOR_GATEWAY_PLAN.md`. Vendor contract: `ai-contract.md`.

## Layout

| Path | What |
|---|---|
| `backend/` | FastAPI app (routers, services, repositories, models), Alembic migrations, tests |
| `extension/` | Chrome extension (MV3): popup, background, content, injected, outbound engine, realtime line, build script, Node tests, Playwright e2e |
| `spike/` | Spike kit for a human to run on a real WhatsApp test account (Phase 2) |
| `docs/MANUAL_CHECKLIST.md` | What only a human on real WhatsApp can check before a release |
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

## Tests (Phase 1, step 7)

```bash
make test        # backend: unit + API integration (Docker) + cross-language signature (needs node)
make bot-test    # reference bot
make ext-test    # extension (Node)
make e2e         # Playwright: real Chromium + extension + fake WhatsApp + docker stack, 2 profiles
```

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

## Phase 2: vendor messages to customers (outbound)

A vendor's backend calls `POST /v1/message-to-customer` (live key, `Idempotency-Key`, types `otp`,
`transactional`, `marketing`). The gateway queues the job, a Celery worker hands it to the vendor's plugin
over a Socket.IO line (`/socket.io`), the plugin sends it through WhatsApp Web and reports back. Status via
`GET /v1/message-to-customer/{jobId}` and signed webhooks. Test keys never touch WhatsApp: a built-in
virtual plugin answers (magic numbers `...0001` not on WhatsApp, `...0002` not ready then sent, `...0003`
silent/unknown, `...0004` slow).

```bash
make up                        # api + worker + beat + postgres + redis
make worker-logs
make test                      # backend (unit + integration + real worker + real sockets)
make chaos                     # Toxiproxy: Redis cut / slowed on purpose (Docker)
make soak VENDORS=20 JOBS=50   # many vendors, one real worker, prints a summary
make ext-test                  # extension Node tests (144)
make e2e                       # Playwright: real Chromium + extension + FAKE WhatsApp + docker stack
make spike-test                # the Spike kit's own safety checks
```

Admin additions: vendor page shows devices (online, WhatsApp state, last heartbeat; live view refreshes every
5 s), recent messages (number and text masked: `OTP ****13`), marketing cap usage and edit, webhook
deliveries with Replay. Suspending a vendor or revoking its last live key drops the plugin's connection at
once and cancels queued messages. Admin two-factor login (TOTP) is set up at `/admin/account`;
`ADMIN_REQUIRE_2FA=true` makes it mandatory.

### What is proven and what is not

Everything that talks to **our own** code is tested against real Postgres, Redis, Celery and Socket.IO.
Everything that touches **WhatsApp Web's internals** is tested only against a fake WhatsApp that we wrote
from our own guesses (`extension/e2e/fake-whatsapp.js`). Those guesses are marked UNVERIFIED in
`extension/injected.js` and in the plan (section 8.8): sending to a number that is not a contact (both ways),
the connection-state signal, the ack level, `@lid` handling, the invalid-number popup, and whether
`context.setOffline` reaches the extension's service worker. A human has to run `spike/` on a real test
account and fill in `spike/SPIKE_REPORT.md`, then go through `docs/MANUAL_CHECKLIST.md`.
