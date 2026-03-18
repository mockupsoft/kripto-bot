# AGENTS.md

## Cursor Cloud specific instructions

### Architecture overview

Kripto-Bot is a **Polymarket paper-trading research platform** (DEMO-ONLY, `DEMO_MODE_ONLY = True` in `backend/app/config.py`). It uses Docker Compose to orchestrate four services: PostgreSQL 16, Redis 7, a Python 3.12 / FastAPI backend, and a Next.js 14 frontend dashboard. No external API keys are needed — it uses public Polymarket APIs.

Layer flow: `ingestion/` → `intelligence/` → `signals/` → `strategies/` → `execution/` → `analytics/` → `api/endpoints/`

See `README.md` and `SETUP.md` for standard setup instructions. See `.cursor/rules/` for detailed rules on each subsystem.

### Service ports

| Service    | Host Port | Container Port |
|------------|-----------|----------------|
| Backend    | 8002      | 8000           |
| Frontend   | 3002      | 3000           |
| PostgreSQL | 5433      | 5432           |
| Redis      | 6380      | 6379           |

### Starting Docker daemon (required before any docker command)

The VM is a Docker container inside Firecracker. Run these once per session before any `docker compose` commands:

```bash
sudo mkdir -p /etc/docker
printf '%s\n' '{' '  "storage-driver": "fuse-overlayfs",' '  "exec-opts": ["native.cgroupdriver=cgroupfs"]' '}' | sudo tee /etc/docker/daemon.json > /dev/null
sudo dockerd &>/tmp/dockerd.log &
sudo chmod 666 /var/run/docker.sock
```

### Running services

```bash
cp .env.example .env   # only needed once
docker compose up --build -d
```

The backend auto-runs `alembic upgrade head` and seed data on startup. The `docker-compose.override.yml` zeroes out resource limits to work around cgroup v2 threaded mode — **do not delete it**.

### Applying backend code changes

Docker bind mounts may not sync instantly. After editing any backend Python file:

```bash
# 1. Copy the changed file into the running container
docker cp backend/app/xyz/file.py kripto-bot-backend-1:/app/app/xyz/file.py

# 2. Clear pycache so Python picks up the new file
docker exec kripto-bot-backend-1 sh -c "find /app -name '__pycache__' -type d | xargs rm -rf 2>/dev/null"

# 3. Verify the import works
docker exec kripto-bot-backend-1 python3 -c "from app.xyz.file import MyClass; print('ok')"
```

For permanent changes (new files, dependency updates): `docker compose build --no-cache backend && docker compose up -d backend`

### Running tests

- **Backend (pytest):** `docker compose exec backend pytest tests/ -v` — 30 pure-computation tests (Kelly, Bayesian, spread, edge, Monte Carlo, Stoikov, book walker). No DB required.
- **Frontend (lint):** `docker compose exec frontend npm run lint` — requires `eslint@8` and `eslint-config-next@14` as devDependencies and `.eslintrc.json` (`{"extends": "next/core-web-vitals"}`) in `frontend/`. ESLint 9 is incompatible with Next.js 14.

### Health-check commands

```bash
# System metrics (signals_generated, trades_executed, bankroll, etc.)
curl -s http://localhost:8002/api/analytics/metrics | python3 -m json.tool

# Strategy health (rolling PF, kill-switch status per strategy)
curl -s http://localhost:8002/api/analytics/strategy-health | python3 -m json.tool

# Last few cycle log lines
docker logs kripto-bot-backend-1 2>&1 | grep -E "cycle|signals_generated|trades_executed" | tail -5

# Recent reject reasons (why signals are not becoming trades)
docker exec kripto-bot-postgres-1 psql -U polybot -d polybot -c \
  "SELECT reject_reason, COUNT(*) FROM signal_decisions
   WHERE decided_at > NOW()-INTERVAL '5 minutes' AND decision='reject'
   GROUP BY reject_reason ORDER BY COUNT(*) DESC LIMIT 10;"
```

### Key environment variables (docker-compose.yml)

ENV vars in `docker-compose.yml` override code defaults. Always update the compose file when changing a threshold in code:

| Variable | Default | Effect |
|---|---|---|
| `MIN_CONF_EDGE` | 0.005 | Signal quality gate — raise to reduce trade volume |
| `MAX_SPREAD_ABS` | 0.08 | Reject signals with spread wider than this |
| `STRATEGY_RESEARCH_MODE` | false | `true` disables strategy-level kill-switch (data collection mode) |
| `STALE_EXIT_DISABLED` | false | `true` disables stale_data force-close exits |
| `STALE_SOFT_GUARD` | false | `true` skips stale positions instead of closing them |
| `STALE_MARKET_BLACKLIST` | (empty) | Comma-separated market UUIDs to reject at Gate 0 |
| `MAX_RAW_EDGE` | 0.15 | Caps raw_edge to prevent Bayesian probability divergence |

### Key gotchas for agents

- **`FilterResult` has no `.passed` attribute** — use `filter_result.decision != "accept"` (decision is `"accept"` / `"reject"` / `"shadow"`).
- **`TradeSignal` uses `source_wallet_id`**, not `wallet_id`.
- **`PaperPosition` uses `source_wallet_id`**, not `signal_id`.
- **`KellyResult` uses `proposed_size_usd`**, not `recommended_size`.
- **`SignalDecision` has no `action` field** — use `decision="accept"` and call `self._filter.record_decision()` (see `direct_copy.py` for the pattern).
- **`Wallet` model has no `composite_score`** — read from `WalletScore` table via `wallet_scores.composite_score`.
- **Do not add local imports inside functions** that already have the same module imported globally — causes `UnboundLocalError`.
- **`strategy_manager.get_rolling_stats()` must filter `NON_TRADING_EXITS`** (`stale_data`, `position_cap_cleanup`, `demo_cleanup`) or PF calculations will be skewed and strategies will incorrectly enter shadow mode.
- **Position sizer requires `side` parameter** — BUY and SELL use different Kelly calculations; omitting it causes `proposed_size = 0` for SELL signals.
- **`signal_filter` SELL edge must use `abs(raw_directional)`** — raw signed edge is negative for SELL, which produces `conf_edge < 0` and rejects all SELL signals.
- **`check_exposure()` division-by-zero**: if `total_bankroll = 0`, `current_exposure_pct` blows up. Guard: skip the exposure gate when `bankroll <= 0`.
- Frontend volume mount excludes `node_modules` and `.next` — packages installed inside the container are ephemeral; update `package.json` and rebuild for persistence.

### trades_executed: 0 — systematic diagnosis order

```
1. kill_switch active?           → GET /api/analytics/strategy-health (kill_switch_active)
2. strategy shadow/paused?       → strategy rolling_pf < 0.8 → bankroll = 0 → no trades
3. all signals rejected?         → check signal_decisions reject_reason distribution
4. proposed_size = 0?            → Kelly = 0 (side mismatch, bankroll = 0, or edge = 0)
5. SELL accept = 0, BUY > 0?     → signal_filter abs() missing for SELL edge
6. accept + size > 0, trades = 0 → execute() error or strategy in shadow mode
```

### Adding a new strategy

1. Subclass `BaseStrategy` in `backend/app/strategies/`. Implement both `evaluate()` and `execute()` (both are abstract). Reference `direct_copy.py`.
2. Register in `runner.py`: `self.strategies["my_strategy"] = MyStrategy()` and add to `copy_strategies`.
3. Add default config in `strategy_manager.py` under `DEFAULT_CONFIGS`.
4. Add to `NON_TRADING_EXITS` filter in both `kill_switch.py` and `strategy_manager.py` if any of its exits are infrastructure-only (not real trade outcomes).

### Database schema quick reference

```
wallets          → wallet_transactions, wallet_scores
markets          → market_snapshots, market_relationships
trade_signals    → signal_decisions
paper_positions  → position_events
```

Run migrations: `docker exec kripto-bot-backend-1 alembic upgrade head`

Emergency column fix without migration:
```sql
ALTER TABLE wallet_scores ALTER COLUMN max_drawdown TYPE NUMERIC(16,4);
ALTER TABLE wallet_transactions ALTER COLUMN outcome TYPE VARCHAR(255);
ALTER TABLE wallet_transactions ALTER COLUMN side TYPE VARCHAR(32);
```

### Key API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/analytics/metrics` | Cycle stats, bankroll, signal counts |
| `GET /api/analytics/strategy-health` | Per-strategy PF, kill-switch status |
| `GET /api/analytics/daily-digest` | Cost/gross ratio, expectancy, precision grade |
| `GET /api/analytics/edge-calibration` | Pearson-r edge vs PnL correlation |
| `GET /api/analytics/alpha-backtest` | Quintile IQ test (wallet alpha score validation) |
| `GET /api/markets/actionability` | TRADEABLE / MARGINAL / NO_EDGE verdicts |
| `GET /api/wallets/intelligence/alpha-leaderboard` | Copyable alpha ranking |
| `GET /api/wallets/intelligence/influence-graph` | Leader/follower graph |
| `GET /api/wallets/{id}/alpha-decay` | Wallet decay alert |
