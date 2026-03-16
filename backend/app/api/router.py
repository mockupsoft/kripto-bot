from fastapi import APIRouter

from app.api.endpoints import overview, wallets, markets, trades, signals, analytics, replay, settings, ingestion

api_router = APIRouter()
api_router.include_router(overview.router, tags=["overview"])
api_router.include_router(wallets.router, prefix="/wallets", tags=["wallets"])
api_router.include_router(markets.router, prefix="/markets", tags=["markets"])
api_router.include_router(trades.router, prefix="/trades", tags=["trades"])
api_router.include_router(signals.router, prefix="/signals", tags=["signals"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(replay.router, prefix="/replay", tags=["replay"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
