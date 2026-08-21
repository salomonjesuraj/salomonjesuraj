.PHONY: up down logs restart clean build test lint infra bootstrap setup-dev test-all compile-check compose-check

# ═══════════════════════════════════════════════
# INFUSION SCREENER — Makefile
# ═══════════════════════════════════════════════

COMPOSE = docker compose
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml
PY = .venv/Scripts/python
PIP = .venv/Scripts/pip
RUFF = .venv/Scripts/ruff
MYPY = .venv/Scripts/mypy

# ── Stack Management ──

up:
	$(COMPOSE) up -d --build

up-dev:
	$(COMPOSE_DEV) up -d --build

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v --remove-orphans

build:
	$(COMPOSE) build

# ── Logs ──

logs:
	$(COMPOSE) logs -f --tail=100

logs-service:
	$(COMPOSE) logs -f --tail=100 $(SERVICE)

# ── Service Management ──

restart:
	$(COMPOSE) restart $(SERVICE)

# ── Infrastructure ──

infra:
	$(COMPOSE) up -d redis postgres

bootstrap:
	python scripts/validate_streams.py

migrate:
	cd migrations && alembic upgrade head

# ── Local developer environment ──

setup-dev:
	python -m venv .venv
	$(PY) -m pip install -U pip setuptools wheel
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m pip install -e libs/infusion-models -e libs/infusion-streams -e libs/infusion-common
	$(PY) -m pip install -e services/feature-engine -e services/api -e services/scanner -e services/archiver

# ── Quality ──

lint:
	$(RUFF) check .
	$(RUFF) format --check .

format:
	$(RUFF) format .

typecheck:
	$(MYPY) libs/ services/

test:
	$(PY) -m pytest tests/unit/ -v

test-all:
	$(PY) -m pytest tests/ -v

test-integration:
	$(PY) -m pytest tests/integration/ -v

compile-check:
	$(PY) -m compileall -q libs services scripts tests

compose-check:
	$(COMPOSE) config --quiet

# ── Utility ──

redis-cli:
	$(COMPOSE) exec redis redis-cli

psql:
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-infusion} -d $${POSTGRES_DB:-infusion}

status:
	$(COMPOSE) ps
	@echo "───────────────────"
	@echo "Redis streams:"
	$(COMPOSE) exec redis redis-cli KEYS "infusion:stream:*"
	@echo "───────────────────"
	@echo "Consumer group lag:"
	$(COMPOSE) exec redis redis-cli XINFO GROUPS infusion:stream:tick:raw 2>/dev/null || true
