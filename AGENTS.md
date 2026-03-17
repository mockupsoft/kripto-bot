# AGENTS.md

## Cursor Cloud specific instructions

### Architecture overview

Kripto-Bot is a **Polymarket paper-trading research platform** (DEMO-ONLY). It uses Docker Compose to orchestrate four services: PostgreSQL 16, Redis 7, a Python 3.12 / FastAPI backend, and a Next.js 14 frontend dashboard. No external API keys are needed — it uses public Polymarket APIs.

See `README.md` and `SETUP.md` for standard setup instructions. See `.cursor/rules/` for detailed architecture rules.

### Service ports

| Service   | Host Port | Container Port |
|-----------|-----------|----------------|
| Backend   | 8002      | 8000           |
| Frontend  | 3002      | 3000           |
| PostgreSQL| 5433      | 5432           |
| Redis     | 6380      | 6379           |

### Running services

```bash
cp .env.example .env   # only needed once
docker compose up --build -d
```

The backend auto-runs `alembic upgrade head` and seed data on startup.

### Nested container cgroup workaround

The VM is itself a Docker container inside Firecracker. The `deploy.resources` limits in `docker-compose.yml` fail because cgroup v2 is in threaded mode. A `docker-compose.override.yml` that zeroes out resource limits is required. The override must also be present when `docker compose` commands are run. Do not delete `docker-compose.override.yml`.

Before starting Docker, the daemon must be configured:

```bash
sudo mkdir -p /etc/docker
printf '%s\n' '{' '  "storage-driver": "fuse-overlayfs",' '  "exec-opts": ["native.cgroupdriver=cgroupfs"]' '}' | sudo tee /etc/docker/daemon.json > /dev/null
sudo dockerd &>/tmp/dockerd.log &
sudo chmod 666 /var/run/docker.sock
```

### Running tests

- **Backend (pytest):** `docker compose exec backend pytest tests/ -v` — 30 pure-computation tests (Kelly, Bayesian, spread, edge, Monte Carlo, Stoikov, book walker). No DB required.
- **Frontend (lint):** `docker compose exec frontend npm run lint` — requires `eslint` and `eslint-config-next` as devDependencies and `.eslintrc.json` in the frontend directory.

### Key gotchas

- ESLint is not included in the base `frontend/package.json`. You must install `eslint@8` and `eslint-config-next@14` for `npm run lint` to work (ESLint 9 is incompatible with Next.js 14).
- The `.eslintrc.json` file (`{"extends": "next/core-web-vitals"}`) must exist in `/workspace/frontend/`.
- The frontend volume mount in docker-compose.yml excludes `node_modules` and `.next` (they live in the container only). Packages installed inside the container are ephemeral; for persistent changes, update `package.json` and rebuild.
- Backend code changes on a bind mount may not sync instantly. Use `docker cp` and pycache cleanup as described in `.cursor/rules/03-docker-deployment.mdc`.
