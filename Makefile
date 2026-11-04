.PHONY: help up down logs migrate seed gateway lint format typecheck test test-unit ext-test ext-dev ext-prod bot bot-local bot-test bot-lint e2e spike-test worker-logs chaos soak

COMPOSE ?= docker compose
BACKEND := cd backend &&

help:
	@echo "make up | down | logs | migrate | seed | gateway ARGS=... | lint | format | typecheck | test | test-unit | ext-test | ext-dev | ext-prod URL=... | bot BOT_SECRET=... | bot-local | bot-test | bot-lint | e2e | spike-test | worker-logs | chaos | soak"

.env:
	@cp .env.example .env
	@key=$$(cd backend && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"); \
	  sed -i "s|^SECRET_ENCRYPTION_KEY=.*|SECRET_ENCRYPTION_KEY=$$key|" .env
	@echo "Created .env with a fresh SECRET_ENCRYPTION_KEY"

up: .env
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api

worker-logs:
	$(COMPOSE) logs -f worker beat

# These run inside the api container, so they use the compose database.
migrate:
	$(COMPOSE) exec api alembic upgrade head

seed:
	$(COMPOSE) exec api gateway seed

# Example: make gateway ARGS="create-vendor --name Ali --number 923001234567 --email a@b.com --bot-url http://localhost:9000/ai-reply"
gateway:
	$(COMPOSE) exec api gateway $(ARGS)

lint:
	$(BACKEND) uv run ruff check . && uv run ruff format --check .

format:
	$(BACKEND) uv run ruff check --fix . && uv run ruff format .

typecheck:
	$(BACKEND) uv run mypy

test-unit:
	$(BACKEND) uv run pytest tests/unit -q

# Integration tests start Postgres/Redis with testcontainers, so Docker must be running.
test:
	$(BACKEND) uv run pytest -q --cov --cov-report=term-missing

# Chrome extension (Node is used for the build script and tests only).
ext-test:
	cd extension && npm run -s check && npm test

# Load extension/dist/dev as an unpacked extension in Chrome.
ext-dev:
	cd extension && node build.mjs --env dev

# make ext-prod URL=https://api.example.com  (staging: node build.mjs --env staging --url ...)
ext-prod:
	cd extension && node build.mjs --env prod --url "$(URL)"

# Reference bot (reference-bot/). BOT_SECRET is the whsec_... printed when the vendor was created.
bot:
	@test -n "$(BOT_SECRET)" || { echo "usage: make bot BOT_SECRET=whsec_..."; exit 1; }
	BOT_SECRET="$(BOT_SECRET)" $(COMPOSE) --profile bot up -d --build reference-bot

# Same bot on the host, port 9000 (no Docker): make bot-local BOT_SECRET=whsec_...
bot-local:
	@test -n "$(BOT_SECRET)" || { echo "usage: make bot-local BOT_SECRET=whsec_..."; exit 1; }
	cd reference-bot && BOT_SECRET="$(BOT_SECRET)" uv run python -m app

bot-test:
	cd reference-bot && uv run pytest -q

bot-lint:
	cd reference-bot && uv run ruff check . && uv run ruff format --check . && uv run mypy

# Phase 2 Spike kit: only checks the kit's own safety promises (no real WhatsApp involved).
spike-test:
	cd spike && node --check spike.js && node --test "tests/*.test.mjs"

# Phase 1 smoke test: a fresh docker stack, real Chromium with the built extension, a fake
# web.whatsapp.com, two browser profiles with two vendors. Needs Docker and Node; ~3 minutes.
e2e:
	$(COMPOSE) down -v
	$(MAKE) up
	@until curl -fs localhost:4149/ready >/dev/null; do sleep 2; done
	cd extension/e2e && npm ci && npx playwright install chromium
	@cd extension/e2e && npx playwright test; status=$$?; cd ../.. && $(COMPOSE) down -v; exit $$status

# Failure tests with Toxiproxy (Redis and Postgres cut / slowed on purpose). Needs Docker.
chaos:
	cd backend && uv run pytest -q -m chaos tests/integration/chaos

# Many vendors, many messages, one real worker. Prints a summary. Usage: make soak VENDORS=20 JOBS=50
soak:
	cd backend && uv run python -m scripts.soak --vendors $(or $(VENDORS),10) --jobs $(or $(JOBS),30)
