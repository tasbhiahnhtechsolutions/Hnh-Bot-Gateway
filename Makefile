.PHONY: help up down logs migrate seed gateway lint format typecheck test test-unit

COMPOSE ?= docker compose
BACKEND := cd backend &&

help:
	@echo "make up | down | logs | migrate | seed | gateway ARGS=... | lint | format | typecheck | test | test-unit"

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

# These run inside the api container, so they use the compose database.
migrate:
	$(COMPOSE) exec api alembic upgrade head

seed:
	$(COMPOSE) exec api gateway seed

# Example: make gateway ARGS="create-vendor --name Ali --number 923001234567 --email a@b.com --bot-url http://localhost:9000"
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
