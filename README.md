# Infusion Screener

Realtime NSE market intelligence system. Event-driven microservice architecture with Redis Streams.

## Quick Start

```bash
# 1. Setup environment
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD at minimum

# 2. Start everything
make up

# 3. Verify
make status
curl http://localhost:8000/health
```

## Architecture

```
Broker WS → Ingestion → Normalizer → Feature Engine → Scanner → Conviction
                                                                      ↓
                                              Dashboard ← WS Gateway ← Alerter
```

## Commands

| Command | Description |
|---|---|
| `make up` | Build and start all services |
| `make down` | Stop all services |
| `make logs` | Tail all service logs |
| `make infra` | Start only Redis + PostgreSQL |
| `make status` | Show service status + stream info |
| `make clean` | Stop and wipe all data volumes |
| `make test` | Run unit tests |
| `make lint` | Run linter |
