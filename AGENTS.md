# AGENTS.md

## Cursor Cloud specific instructions

### Mission and safety (non-negotiable)

Kripto-Bot is a **Polymarket paper-trading research platform**. It is **DEMO-ONLY**.

- Never add or enable real-money order execution.
- Keep demo constraints enforced (`DEMO_MODE_ONLY = True` in backend settings).
- If a change appears to introduce real execution, block it explicitly.

### Architecture overview

The stack is Docker Compose with four services:

- PostgreSQL 16
- Redis 7
- Python 3.12 / FastAPI backend
- Next.js 14 frontend dashboard

No private API keys are required for normal local operation (public Polymarket APIs).

See `README.md` and `SETUP.md` for setup flow. See `.cursor/rules/` for deep architecture and operations guidance.

### Service ports

| Service    | Host Port | Container Port |
|------------|-----------|----------------|
| Backend    | 8002      | 8000           |
| Frontend   | 3002      | 3000           |
| PostgreSQL | 5433      | 5432           |
| Redis      | 6380      | 6379           |

### Running services

```bash
cp .env.example .env   # only once
docker compose up --build -d
```

Backend startup auto-runs `alembic upgrade head` and seed routines.

### Nested container cgroup workaround (required in Cursor Cloud)

The VM itself runs inside a container. `deploy.resources` can fail under threaded cgroup v2 unless Docker daemon settings are adjusted. Keep `docker-compose.override.yml` in place and do not delete it.

Run once before `docker compose` if the daemon is not already configured:

```bash
sudo mkdir -p /etc/docker
printf '%s\n' '{' '  "storage-driver": "fuse-overlayfs",' '  "exec-opts": ["native.cgroupdriver=cgroupfs"]' '}' | sudo tee /etc/docker/daemon.json > /dev/null
sudo dockerd &>/tmp/dockerd.log &
sudo chmod 666 /var/run/docker.sock
```

### Testing guidance

Prefer focused checks for touched areas instead of broad full-suite runs.

- Backend tests: `docker compose exec backend pytest tests/ -v`
  - Current suite is mostly pure-computation strategy/math tests.
- Frontend lint: `docker compose exec frontend npm run lint`
  - Requires `eslint@8` and `eslint-config-next@14`.

If UI behavior changes, perform manual browser validation in addition to terminal checks.

### Development gotchas

- Frontend container keeps its own `node_modules` and `.next`; container-only installs are ephemeral. Persist dependency changes in `frontend/package.json`.
- `.eslintrc.json` should exist in `/workspace/frontend/` with:
  - `{"extends": "next/core-web-vitals"}`
- Backend bind mounts can lag in Docker Desktop / nested environments. If code appears stale, sync explicitly and clear bytecode:

```bash
docker cp backend/app/<path>.py kripto-bot-backend-1:/app/app/<path>.py
docker exec kripto-bot-backend-1 sh -c "find /app -name '__pycache__' -type d | xargs rm -rf 2>/dev/null"
```

### Troubleshooting references

For known production-like paper-trading failure patterns (signal generation, kill switch behavior, sizing, stale data exits, etc.), use `.cursor/rules/00-known-issues.mdc` as the primary diagnostic playbook.
