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

    

    # Task 1: Market + wallet ingestion loop
    async def _supervised_polling() -> None:
        backoff = 5
        while True:
            try:
                from app.ingestion.live_ingestion import start_live_polling
                await start_live_polling(market_interval=5, wallet_interval=180)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Live polling crashed — restarting in %ds. Error: %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)
            else:
                backoff = 5

    # Task 2: Strategy runner — fully independent, every 30s
    async def _supervised_runner() -> None:
        backoff = 5
        # Brief startup delay so DB/Redis are ready before first run
        await asyncio.sleep(15)
        from app.strategies.runner import StrategyRunner
        from app.dependencies import async_session_factory
        runner = StrategyRunner()
        while True:
            try:
                async with async_session_factory() as db:
                    stats = await runner.run_cycle(db)
                    await db.commit()
                logger.info(
                    "Runner cycle: signals=%s trades=%s modes=%s",
                    stats.get("signals_generated"),
                    stats.get("trades_executed"),
                    stats.get("strategy_modes"),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Strategy runner crashed — restarting in %ds. Error: %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            else:
                backoff = 5
            await asyncio.sleep(30)

    # Task 3: Exit engine — every 60s
    async def _supervised_exit() -> None:
        await asyncio.sleep(20)
        while True:
            try:
                from app.execution.exit_engine import run_exit_cycle
                from app.dependencies import async_session_factory
                async with async_session_factory() as db:
                    result = await run_exit_cycle(db)
                    await db.commit()
                if result.get("closed", 0) > 0:
                    logger.info("Exit engine closed %d positions", result["closed"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Exit engine crashed: %s", exc)
            await asyncio.sleep(60)

    polling_task = asyncio.create_task(_supervised_polling())
    runner_task = asyncio.create_task(_supervised_runner())
    exit_task = asyncio.create_task(_supervised_exit())
    logger.info("Live Polymarket polling started — supervised mode (polling + runner + exit engine)")

    yield

    for task in (polling_task, runner_task, exit_task):
        task.cancel()
    for task in (polling_task, runner_task, exit_task):
        try:
            await task
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
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3002",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3002",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(api_router, prefix="/api")
app.websocket("/ws/live")(websocket_endpoint)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "demo_mode": True, "live_data": True}
