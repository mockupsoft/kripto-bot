"""Paper executor: orchestrates the full simulation of a paper trade.

Combines delay model, book walker / slippage model, fill model, fee model,
and Stoikov execution heuristic into one simulated trade lifecycle.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.book_walker import walk_book, BookWalkResult
from app.execution.delay_model import simulate_delay, DelaySimulation
from app.execution.fee_model import compute_fee
from app.execution.fill_model import simulate_fill, FillSimulation
from app.execution.slippage_model import estimate_slippage, SlippageResult
from app.execution.stoikov import compute_execution_bias, ExecutionBias
from app.models.paper import PaperFill, PaperOrder, PaperPosition


@dataclass
class ExecutionResult:
    order: PaperOrder
    fill: PaperFill | None
    position: PaperPosition | None
    delay: DelaySimulation
    fill_sim: FillSimulation
    book_walk: BookWalkResult | None
    execution_bias: ExecutionBias


async def execute_paper_trade(
    db: AsyncSession,
    signal_id: UUID,
    decision_id: UUID,
    market_id: UUID,
    side: str,
    outcome: str,
    requested_size: float,
    market_price: float,
    strategy: str,
    source_wallet_id: UUID | None = None,
    book_levels: dict | None = None,
    book_snapshot_id: int | None = None,
    fees_enabled: bool = True,
    fee_rate_bps: int = 250,
    available_depth: float = 100.0,
    current_volatility: float = 0.05,
    detection_delay_ms: int = 500,
    decision_delay_ms: int = 200,
    execution_delay_ms: int = 300,
    position_group_id: UUID | None = None,
    leg_index: int = 0,
    is_hedge_leg: bool = False,
    target_structure: str = "single",
    rng_seed: int | None = None,
) -> ExecutionResult:
    rng = random.Random(rng_seed) if rng_seed else random.Random()

    # 1. Simulate delay
    delay = simulate_delay(
        detection_base_ms=detection_delay_ms,
        decision_base_ms=decision_delay_ms,
        execution_base_ms=execution_delay_ms,
        rng=rng,
    )

    # 2. Compute execution bias (Stoikov)
    inventory_imbalance = 0.0
    if position_group_id and leg_index > 0:
        inventory_imbalance = -0.5 if side == "BUY" else 0.5

    exec_bias = compute_execution_bias(
        mid_price=market_price,
        inventory_imbalance=inventory_imbalance,
        sigma=current_volatility,
        time_since_signal_ms=delay.total_delay_ms,
    )

    # 3. Simulate fill
    fill_sim = simulate_fill(
        requested_size=requested_size,
        available_depth=available_depth,
        urgency_bias=exec_bias.bias,
        rng=rng,
    )

    actual_size = requested_size * fill_sim.actual_fill_pct

    # 4. Determine fill price
    book_walk_result = None
    if book_levels and actual_size > 0:
        staleness = min(0.3, delay.total_delay_ms / 5000)
        book_walk_result = walk_book(
            book_levels=book_levels,
            side=side,
            requested_size=actual_size,
            staleness_haircut=staleness,
        )
        fill_price = book_walk_result.wavg_fill_price
        actual_size = book_walk_result.filled_size
    elif actual_size > 0:
        slip = estimate_slippage(
            base_price=market_price,
            side=side,
            current_volatility=current_volatility,
            available_depth=available_depth,
            requested_size=actual_size,
            rng=rng,
        )
        fill_price = slip.adjusted_price
    else:
        fill_price = 0

    # 5. Compute fee
    fee = compute_fee(fill_price, actual_size, fees_enabled=fees_enabled, fee_rate=fee_rate_bps / 1000) if actual_size > 0 else 0

    # 6. Create order
    status = "filled" if fill_sim.is_full_fill else ("partial" if actual_size > 0 else "failed")
    order = PaperOrder(
        signal_id=signal_id,
        decision_id=decision_id,
        market_id=market_id,
        position_group_id=position_group_id,
        leg_index=leg_index,
        is_hedge_leg=is_hedge_leg,
        side=side,
        outcome=outcome,
        order_type="market",
        requested_price=market_price,
        requested_size=requested_size,
        simulated_delay_ms=delay.total_delay_ms,
        status=status,
        failure_reason=fill_sim.failure_reason,
    )
    db.add(order)
    await db.flush()

    # 7. Create fill (if any)
    fill = None
    if actual_size > 0:
        slippage = abs(fill_price - market_price)
        fill_quality = 1.0 - (slippage / max(market_price, 0.01))

        fill = PaperFill(
            order_id=order.id,
            fill_price=fill_price,
            fill_size=actual_size,
            slippage=slippage,
            fee=fee,
            fill_quality_score=max(0, min(1, fill_quality)),
            book_snapshot_id=book_snapshot_id,
            levels_consumed=book_walk_result.levels_consumed if book_walk_result else None,
            wavg_fill_price=book_walk_result.wavg_fill_price if book_walk_result else None,
        )
        db.add(fill)
        await db.flush()

    # 8. Create position
    position = None
    if actual_size > 0:
        notional = fill_price * actual_size
        position = PaperPosition(
            position_group_id=position_group_id,
            leg_index=leg_index,
            is_hedge_leg=is_hedge_leg,
            target_structure=target_structure,
            market_id=market_id,
            strategy=strategy,
            source_wallet_id=source_wallet_id,
            side=side,
            outcome=outcome,
            avg_entry_price=fill_price,
            total_size=actual_size,
            total_cost=notional,
            total_fees=fee,
            total_slippage=slippage,
            status="open",
        )
        db.add(position)
        await db.flush()

    return ExecutionResult(
        order=order,
        fill=fill,
        position=position,
        delay=delay,
        fill_sim=fill_sim,
        book_walk=book_walk_result,
        execution_bias=exec_bias,
    )
