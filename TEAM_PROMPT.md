# Team Prompt: Build the WhatsApp Vendor Gateway

How to use this file:

1. Extract the zip and open the folder in Cursor.
2. Open a new Agent chat.
3. Paste the block in **"Prompt to paste"** below.

Everything else in this file is for humans (what is in the folder, what is not).

---

## Prompt to paste

```text
You are a senior backend engineer. Build the project described in VENDOR_GATEWAY_PLAN.md.

Read these first, in this order:
1. VENDOR_GATEWAY_PLAN.md   -> the single source of truth. Follow it. Do not redesign it.
2. ai-contract.md           -> the vendor contract for /ai-reply (inbound). Your code must match it exactly.
3. TEAM_PROMPT.md           -> the rules in the section "Rules for the agent".
4. cursor-chat.md           -> background only. Read it ONLY when the plan is unclear. If the plan and the chat disagree, the plan wins.
5. Turabees-Whatsapp-Bot/   -> the OLD project (Chrome extension + old Node server). The extension is reused and changed (plan section 10). The old Node server is only a reference and is replaced by the new backend.

Start with Phase 1 (plan section 15, Phase 1 list). Work in small steps. After each step:
- run the tests and the linters,
- tick the item in the "Todo checklist" in plan section 15,
- show me a short summary (what you built, what you ran, what failed),
- then wait for my "go" before the next step.

Stop and ask me if something is unclear or if the plan seems wrong. Do not guess. Do not start Phase 2 until I say so.
```

---

## What is in this folder

| Path | What it is |
|---|---|
| `VENDOR_GATEWAY_PLAN.md` | The full plan (architecture, DB, API, Celery, Socket.IO, admin, plugin, tests, order of work). **Source of truth.** |
| `ai-contract.md` | Vendor-facing contract for the customer-message flow (`POST /ai-reply`). Written in easy English so it can be sent to vendors. |
| `cursor-chat.md` | Export of the planning chat. Background only. |
| `Turabees-Whatsapp-Bot/extension/` | The existing Chrome extension. Changed in Phase 1 (plan section 10). |
| `Turabees-Whatsapp-Bot/server/` | The old Node server. Reference only. It is replaced by the new FastAPI backend. |
| `Turabees-Whatsapp-Bot/README.md` | Description of the old project. |

Not in the zip on purpose: `node_modules/`, `.git/`, the real `server/config.json` (it holds real API keys), and unrelated files.

---

## Rules for the agent

### Stack (fixed)

- **Backend:** Python, FastAPI, async SQLAlchemy 2 + asyncpg, Alembic, pydantic-settings, structlog.
- **Data:** Postgres (source of truth), Redis (Celery broker, Socket.IO manager, pacing keys, counters).
- **Jobs:** Celery (as in plan section 8).
- **Realtime:** python-socketio (as in plan section 8.4).
- **Admin panel:** FastAPI + Jinja2 + HTMX (plan section 9). No React.
- **Reference test bot:** FastAPI, in `reference-bot/` (plan section 7.5). **No Node.js** for the bot or the backend.
- Node is allowed only for the extension build script (`extension/build.mjs`) and the Playwright tests.

### Code quality

- Professional structure: routers, services, repositories, schemas, config. No business logic inside route functions.
- Type hints everywhere. `ruff` and `mypy` must pass.
- Tests with `pytest` (plan section 14). New code comes with tests. Do not mark a step done if its tests fail.
- No secrets in code or git. Config comes from environment variables. Provide `.env.example`.
- Secrets, API keys, base64 media and OTP codes must never appear in logs (plan section 14).
- Small commits with clear messages, one step per commit.

### Follow the plan, do not improvise

- Do not change the design. If you think something is wrong, stop and ask.
- If the plan and `ai-contract.md` disagree, ask. Do not choose silently.
- Do not build anything from plan section 12.D (Phase 3): templates, buttons, STOP/opt-out, per-vendor concurrency cap, circuit breaker, vendor portal, Cloud API channel.
- If you must deviate from the plan, update `VENDOR_GATEWAY_PLAN.md` in the same change and tell me why.

### Order of work

1. **Phase 1** (plan section 15, steps 1 to 7): backend skeleton, vendors and API keys, `/v1/plugin/whoami`, `/v1/ai-reply` (modes, SSRF protection, signature), Test Lab and admin shell, plugin changes, reference bot, Phase 1 tests.
2. **Phase 2 starts with the Spike** (plan section 11). The Spike needs a real, logged-in WhatsApp Web, so a human must run it. Give the human a small test script and instructions. Do not build outbound sending on assumptions.
3. After the Spike report, adjust plan section 8.8 and continue with Phase 2.

### Things that are not verified yet (do not pretend they work)

- Sending to an unsaved number: primary method (internal function) and fallback (`web.whatsapp.com/send?phone=NUMBER`) are defined in plan section 8.8 step 5. Neither is verified on the real WhatsApp Web.
- WhatsApp connection-state signal, message ack level, and the `@lid` mapping (plan section 11).
- Whether Playwright `context.setOffline` affects the extension service worker.

If one of these fails, report it. Do not hide it behind a mock.

### Local setup

1. The old project has `Turabees-Whatsapp-Bot/server/config.example.json` and `Turabees-Whatsapp-Bot/.env.example` as examples only. Create your own keys. The real keys are **not** in this zip. The new backend gets its own `.env.example` (created in step 1 of the plan).
2. The new backend runs with Docker Compose (Postgres, Redis, API, Celery worker, Celery beat, reference bot). Plan section 3 and 15 describe the layout. Use `make` targets (`make up`, `make test`, `make bot`).
3. For manual tests without real vendors, use the Test Lab and the `echo`, `canned`, `mock_bot` modes (plan section 7), and the reference bot at `localhost:9000/ai-reply`.

### What "done" means for each step

- Code written and reviewed by you.
- `ruff`, `mypy`, `pytest` pass.
- The checklist item in plan section 15 is ticked.
- You show the commands you ran and their results.

---

## For the human lead

- Real keys and secrets are not in the zip. Create them separately for each developer.
- Phase 2 outbound (OTP, marketing) is risky: it depends on unofficial WhatsApp Web behaviour and can lead to number bans. Run the Spike first and test only with test numbers.
- `ai-contract.md` is safe to send to vendors. `VENDOR_GATEWAY_PLAN.md` is internal.
