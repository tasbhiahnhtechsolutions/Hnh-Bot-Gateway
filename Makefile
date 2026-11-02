.PHONY: help up down logs build migrate lint format typecheck test test-unit key

COMPOSE ?= docker compose
BACKEND := cd backend &&

help:
	@echo "make up | down | logs | migrate | lint | format | typecheck | test | test-unit | key"

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

migrate:
	$(BACKEND) uv run alembic upgrade head

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
