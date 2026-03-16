from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import assert_demo_mode, get_settings
from app.logging_config import setup_logging
from app.api.router import api_router
from app.api.websocket.gateway import websocket_endpoint

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    assert_demo_mode(settings)
    setup_logging()

    # Wrap polling in a resilient supervisor that restarts on crash
    async def _supervised_polling() -> None:
        backoff = 5
        while True:
            try:
                from app.ingestion.live_ingestion import start_live_polling
                await start_live_polling(market_interval=20, wallet_interval=45)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Live polling crashed — restarting in %ds. Error: %s",
                    backoff, exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)  # exponential back-off, cap at 2 min
            else:
                backoff = 5  # reset on clean exit

    polling_task = asyncio.create_task(_supervised_polling())
    logger.info("Live Polymarket polling started (markets every 20s, wallets every 45s) — supervised mode")

    yield

    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Polymarket Arbitrage Simulator",
    description="DEMO-ONLY paper-trading research platform. Never places real orders.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3002", "http://127.0.0.1:3002"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
app.websocket("/ws/live")(websocket_endpoint)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "demo_mode": True, "live_data": True}
