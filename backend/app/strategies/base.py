"""Base strategy interface for all paper strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import TradeSignal, SignalDecision
from app.models.paper import PaperPosition


class BaseStrategy(ABC):
    """All strategies implement this interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def evaluate(
        self,
        db: AsyncSession,
        signal: TradeSignal,
        current_price: float,
        current_spread: float,
        available_depth: float,
        book_levels: dict | None,
        bankroll: float,
        exposure_pct: float,
    ) -> SignalDecision | None:
        """Evaluate a signal and decide whether to act on it."""
        ...

    @abstractmethod
    async def execute(
        self,
        db: AsyncSession,
        signal: TradeSignal,
        decision: SignalDecision,
        book_levels: dict | None,
        book_snapshot_id: int | None,
    ) -> PaperPosition | None:
        """Execute the paper trade if the decision was 'accept'."""
        ...
