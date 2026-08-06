.PHONY: up down logs restart clean build test lint infra bootstrap

# ═══════════════════════════════════════════════
# INFUSION SCREENER — Makefile
# ═══════════════════════════════════════════════

COMPOSE = docker compose
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml

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

# ── Quality ──

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .

typecheck:
	mypy libs/ services/

test:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

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
