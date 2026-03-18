# AGENTS.md

## Cursor Cloud specific instructions

### Architecture overview

Kripto-Bot is a **Polymarket paper-trading research platform** (DEMO-ONLY). It tracks prediction-market wallets, scores them, generates copy-trading signals, and executes simulated (paper) trades with realistic book-walking, fees, and slippage. No real orders are ever placed.

**Stack:** Docker Compose orchestrating PostgreSQL 16, Redis 7, a Python 3.12 / FastAPI backend, and a Next.js 14 / TypeScript frontend dashboard. No external API keys needed — all Polymarket data is fetched via public APIs.

### Service ports

| Service    | Host Port | Container Port |
|------------|-----------|----------------|
| Backend    | 8002      | 8000           |
| Frontend   | 3002      | 3000           |
| PostgreSQL | 5433      | 5432           |
| Redis      | 6380      | 6379           |

### Running services

```bash
cp .env.example .env   # only needed once
docker compose up --build -d
```

The backend startup command (defined in `docker-compose.yml`) clears `__pycache__`, runs `alembic upgrade head`, seeds data via `python -m app.seed.run_seed`, then launches `uvicorn app.main:app`.

### Nested container cgroup workaround

The Cloud Agent VM is itself a container inside Firecracker. The `deploy.resources` limits in `docker-compose.yml` fail because cgroup v2 is in threaded mode. `docker-compose.override.yml` zeroes out all resource limits to work around this. **Do not delete** `docker-compose.override.yml`.

Before starting Docker, configure the daemon:

```bash
sudo mkdir -p /etc/docker
printf '%s\n' '{' '  "storage-driver": "fuse-overlayfs",' '  "exec-opts": ["native.cgroupdriver=cgroupfs"]' '}' | sudo tee /etc/docker/daemon.json > /dev/null
sudo dockerd &>/tmp/dockerd.log &
sudo chmod 666 /var/run/docker.sock
```

---

### Repository structure

```
/workspace
├── backend/                    # Python 3.12 / FastAPI backend
│   ├── app/
│   │   ├── main.py             # FastAPI app + 3 supervised background tasks
│   │   ├── config.py           # Settings (pydantic-settings), DEMO_MODE_ONLY
│   │   ├── dependencies.py     # DB session factory
│   │   ├── ingestion/          # Polymarket data fetching (REST + WS)
│   │   ├── intelligence/       # Wallet scoring, tracking, alpha, influence graph
│   │   ├── signals/            # Signal generation, Bayesian model, edge, spread, filtering
│   │   ├── strategies/         # Strategy engine: runner, direct_copy, high_conviction, leader_copy, dislocation, shadow
│   │   ├── execution/          # Paper executor, book walker, exit engine, fee/slippage/fill models
│   │   ├── risk/               # Kelly criterion, exposure manager, kill switch, position sizer
│   │   ├── models/             # SQLAlchemy ORM models (20 tables)
│   │   ├── analytics/          # PnL, metrics, reports, clustering, timing analysis
│   │   ├── api/                # FastAPI router + endpoints + WebSocket gateway
│   │   ├── schemas/            # Pydantic schemas
│   │   ├── seed/               # DB seeding (markets, wallets, events, relationships)
│   │   └── simulation/         # Monte Carlo, replay engine, scenario engine
│   ├── alembic/                # DB migrations (4 migration files)
│   ├── tests/                  # pytest tests (7 test files, pure-computation)
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/                   # Next.js 14 / TypeScript / Tailwind dashboard
│   ├── src/
│   │   ├── app/                # Pages: overview, wallets, markets, trades, signals, analytics, research, replay, settings
│   │   ├── components/layout/  # Header, Sidebar, StatusBar
│   │   ├── hooks/              # useWebSocket
│   │   └── lib/                # api.ts, format.ts, types.ts, ws.ts
│   ├── .eslintrc.json
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
├── docker-compose.override.yml # Zeroes out resource limits for nested containers
├── .env.example
├── .cursor/rules/              # 11 Cursor rule files (.mdc) for architecture guidance
├── AGENTS.md                   # This file
├── README.md
└── SETUP.md
```

### Backend pipeline (data flow)

```
Polymarket public API (REST + WS)
  → ingestion/        raw_events → wallet_transactions, market_snapshots
  → intelligence/     wallet scoring, alpha detection, influence graph
  → signals/          Bayesian estimation, edge calc, signal filtering (FilterResult)
  → strategies/       runner.py evaluates each strategy per signal
  → risk/             Kelly sizing, exposure caps, kill switch
  → execution/        paper_executor (book walking, fees, slippage simulation)
  → analytics/        PnL tracking, metrics, strategy health
  → api/endpoints/    REST API for dashboard
```

### Background tasks (main.py lifespan)

Three supervised asyncio tasks run continuously:

1. **`_supervised_polling`** — Market snapshots every 5s, wallet ingestion every 180s.
2. **`_supervised_runner`** — Strategy cycle every 30s (15s startup delay). Generates signals, evaluates strategies, executes paper trades.
3. **`_supervised_exit`** — Exit engine every 60s (20s startup delay). Closes positions on stop-loss, target-hit, stale data, wallet reversal, etc.

All three have automatic crash recovery with exponential backoff.

---

### Running tests

**Backend (pytest):**

```bash
docker compose exec backend pytest tests/ -v
```

7 pure-computation test files (no DB required): Kelly criterion, Bayesian estimation, spread calculations, edge model, Monte Carlo simulation, Stoikov model, book walker. ~30 tests total.

**Frontend (lint):**

```bash
docker compose exec frontend npm run lint
```

Requires `eslint@8` and `eslint-config-next@14` (already in `devDependencies`) and `.eslintrc.json` (exists at `frontend/.eslintrc.json`). ESLint 9 is incompatible with Next.js 14 — do not upgrade.

**Adding new backend tests:** Place test files in `backend/tests/`. Use `pytest` with `asyncio_mode = "auto"` (configured in `pyproject.toml`). Tests should be pure-computation where possible (no DB sessions).

---

### Key environment variables

All env vars are defined in `.env.example`. Important ones:

| Variable | Default | Effect |
|----------|---------|--------|
| `DEMO_MODE_ONLY` | `true` | **Must stay true.** Blocks any real order path. |
| `STRATEGY_RESEARCH_MODE` | `true` | Disables kill switch so strategies keep trading for data collection. |
| `MIN_CONF_EDGE` | `0.005` | Minimum confidence edge to accept a signal. |
| `MAX_SPREAD_ABS` | `0.08` | Maximum absolute spread filter gate. |
| `MAX_OPEN_POSITIONS_GLOBAL` | `40` | Cap on total open paper positions. |
| `STOP_LOSS_PCT` | `0.10` | Stop-loss threshold (10%). |
| `STALE_SNAPSHOT_HOURS` | `4.0` | Hours before a market snapshot is considered stale. |
| `STALE_EXIT_DISABLED` | `false` | Set `true` to disable stale-data exits entirely. |
| `STALE_SOFT_GUARD` | `false` | Stale data skips new entry but doesn't force-close. |

Environment variables set in `docker-compose.yml`'s `environment:` block **override** code defaults. When changing a threshold in code, also update `docker-compose.yml` or remove the env override.

---

### Key API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (returns demo_mode status) |
| GET | `/api/overview` | Dashboard summary: balance, positions, strategies |
| GET | `/api/wallets` | Tracked wallets with scores and alpha |
| GET | `/api/markets` | Markets with snapshots and actionability |
| GET | `/api/trades` | Paper trade log |
| GET | `/api/signals` | Generated signals |
| GET | `/api/analytics/daily-digest` | Daily PnL, profit factor, expectancy |
| GET | `/api/analytics/strategy-health` | Strategy modes, kill switch status |
| GET | `/api/analytics/edge-calibration` | Edge calibration metrics |
| GET | `/api/analytics/metrics` | System-wide metrics |
| WS  | `/ws/live` | Live WebSocket updates |

---

### Database

- **ORM:** SQLAlchemy 2 (async via asyncpg). Models in `backend/app/models/`.
- **Migrations:** Alembic, auto-run on startup. Migration files in `backend/alembic/versions/`.
- **20 tables** covering: raw events, wallet transactions, market snapshots, wallet scores, trade signals, signal decisions, paper positions, portfolio snapshots, strategy configs, and more.
- **Credentials:** `polybot` / `polybot_dev` / `polybot` (user/password/db).

### Code conventions

**Python (backend):**
- All async; each background task opens its own DB session via `async_session_factory()`.
- Import paths matter: signals are in `app.signals`, strategies in `app.strategies`, risk in `app.risk`, execution in `app.execution`.
- `FilterResult.decision` (not `.passed`), `TradeSignal.source_wallet_id` (not `.wallet_id`), `KellyResult.proposed_size_usd` (not `.recommended_size`).
- Every strategy must implement both `evaluate()` and `execute()` from `BaseStrategy`.
- Never duplicate imports at both global and local scope (causes `UnboundLocalError`).

**TypeScript (frontend):**
- Next.js 14 App Router. Pages in `frontend/src/app/`. Tailwind CSS for styling.
- API calls go through `frontend/src/lib/api.ts` hitting `localhost:8002`.
- Volume mount excludes `node_modules` and `.next` — they live inside the container only. For persistent dependency changes, update `package.json` and rebuild.

### Key gotchas

- `docker-compose.override.yml` **must exist** and zero out resource limits, or services won't start in the Cloud Agent VM.
- Backend bind mount may not sync instantly. For guaranteed updates: `docker cp <file> kripto-bot-backend-1:/app/app/...` then clear `__pycache__`.
- `STRATEGY_RESEARCH_MODE=true` in `docker-compose.yml` disables strategy kill switches. Set to `false` for production behavior.
- The `.cursor/rules/` directory contains 11 rule files covering architecture, known issues, backend standards, Docker deployment, DB schema, strategy system, ingestion, frontend standards, wallet intelligence, risk management, and analytics. These provide detailed guidance for each subsystem.

### Troubleshooting

```bash
# Check all container statuses
docker compose ps

# Backend logs (last 30 lines)
docker compose logs backend --tail 30

# Check if runner cycle is producing signals
docker compose logs backend --tail 50 2>&1 | grep -E "signals=|trades="

# Check for crashes
docker compose logs backend --tail 50 2>&1 | grep -iE "error|traceback|exception"

# Database query
docker compose exec postgres psql -U polybot -d polybot -c "SELECT COUNT(*) FROM paper_positions WHERE status='open';"

# Health check
curl -s http://localhost:8002/health
curl -s http://localhost:8002/api/overview | head -c 500
```
