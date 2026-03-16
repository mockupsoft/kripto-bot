"""
Wallet Clustering — Same Trader, Multiple Addresses?

Some wallets are actually the same underlying trader using multiple addresses.
This can indicate:
  1. Large traders splitting positions to avoid detection
  2. Systematic bot operators running multiple accounts
  3. Fund managers with multiple wallets

Clustering signals:
  - Same markets traded within ±30s of each other
  - Same price levels (within 0.5%)
  - Same sizing pattern (similar notional ranges)
  - Similar entry timing relative to market moves

This is important for:
  - Avoiding overexposure to one "effective trader" through multiple wallets
  - Detecting potential insider coordination
  - Weighting copy signals (don't 3x-weight what is effectively one trader)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade import WalletTransaction
from app.models.wallet import Wallet

logger = logging.getLogger(__name__)

SAME_TRADE_WINDOW_SECONDS = 30
SAME_PRICE_TOLERANCE = 0.005   # 0.5% price difference = same entry level
MIN_OVERLAP_TRADES = 2         # Minimum co-occurring trades to flag as potential cluster


async def compute_wallet_clusters(
    db: AsyncSession,
    lookback_days: int = 14,
) -> dict[str, Any]:
    """
    Find wallets that likely represent the same underlying trader.

    Returns a list of clusters, each with:
      - wallet_ids: list of wallets in the cluster
      - similarity_score: 0-1
      - evidence: what signals triggered this cluster
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    # Get all tracked wallets and their trades
    wallets_result = await db.execute(
        select(Wallet).where(Wallet.is_tracked == True)  # noqa: E712
    )
    wallets = {str(w.id): w for w in wallets_result.scalars().all()}

    if len(wallets) < 2:
        return {"clusters": [], "singletons": list(wallets.keys()), "total_wallets": len(wallets)}

    # Load trades for all wallets
    trades_result = await db.execute(
        select(WalletTransaction)
        .where(
            WalletTransaction.wallet_id.in_(list(wallets.keys())),
            WalletTransaction.occurred_at >= since,
        )
        .order_by(WalletTransaction.occurred_at.asc())
    )
    all_trades = trades_result.scalars().all()

    # Group by wallet
    wallet_trades: dict[str, list] = defaultdict(list)
    for t in all_trades:
        wallet_trades[str(t.wallet_id)].append(t)

    # Compute pairwise similarity
    wallet_ids = list(wallet_trades.keys())
    similarity_matrix: dict[tuple[str, str], dict] = {}

    for i in range(len(wallet_ids)):
        for j in range(i + 1, len(wallet_ids)):
            w1, w2 = wallet_ids[i], wallet_ids[j]
            sim = _compute_pairwise_similarity(
                wallet_trades[w1], wallet_trades[w2]
            )
            if sim["score"] > 0.1:  # Only record non-trivial similarities
                similarity_matrix[(w1, w2)] = sim

    # Build clusters using union-find
    clusters = _build_clusters(wallet_ids, similarity_matrix, threshold=0.35)

    # Format output
    cluster_output = []
    singletons = []

    for cluster in clusters:
        if len(cluster["members"]) == 1:
            singletons.extend(cluster["members"])
            continue

        members_info = []
        for wid in cluster["members"]:
            w = wallets.get(wid)
            members_info.append({
                "wallet_id": wid,
                "label": w.label if w else "unknown",
                "address": w.address if w else "unknown",
                "is_real": not (w.address.startswith("0xdemo") if w else True),
            })

        cluster_output.append({
            "cluster_id": f"cluster_{len(cluster_output) + 1}",
            "members": members_info,
            "member_count": len(cluster["members"]),
            "avg_similarity": round(cluster["avg_similarity"], 4),
            "max_similarity": round(cluster["max_similarity"], 4),
            "evidence": cluster["evidence"],
            "warning": (
                "POSSIBLE_SAME_TRADER: these wallets show coordinated trading patterns. "
                "Avoid over-weighting signals from this cluster."
            ) if cluster["avg_similarity"] > 0.5 else (
                "WEAK_CORRELATION: some timing overlap, but may be coincidental."
            ),
        })

    return {
        "clusters": cluster_output,
        "singletons": singletons,
        "total_wallets": len(wallets),
        "clustered_wallets": sum(c["member_count"] for c in cluster_output),
        "lookback_days": lookback_days,
        "insight": (
            "Wallets in the same cluster should be treated as one effective trader. "
            "Copying all of them is not diversification — it is concentration."
        ),
    }


def _compute_pairwise_similarity(
    trades1: list,
    trades2: list,
) -> dict[str, Any]:
    """Compute similarity between two wallets' trading patterns."""
    if not trades1 or not trades2:
        return {"score": 0.0, "evidence": []}

    evidence = []

    # Signal 1: Same-time same-market trades
    co_trades = _find_co_occurring_trades(trades1, trades2)
    timing_score = min(len(co_trades) / max(len(trades1), len(trades2), 1), 1.0)
    if co_trades:
        evidence.append(f"{len(co_trades)} co-occurring trades within {SAME_TRADE_WINDOW_SECONDS}s")

    # Signal 2: Price level overlap
    prices1 = set(round(float(t.price), 2) for t in trades1 if t.price)
    prices2 = set(round(float(t.price), 2) for t in trades2 if t.price)
    price_overlap = len(prices1 & prices2) / max(len(prices1 | prices2), 1) if (prices1 or prices2) else 0
    if price_overlap > 0.3:
        evidence.append(f"Price level overlap: {price_overlap:.1%}")

    # Signal 3: Size similarity (do they trade similar sizes?)
    sizes1 = sorted(float(t.size) for t in trades1 if t.size)
    sizes2 = sorted(float(t.size) for t in trades2 if t.size)
    if sizes1 and sizes2:
        ratio = (sum(sizes1) / len(sizes1)) / max(sum(sizes2) / len(sizes2), 0.001)
        size_sim = 1.0 - min(abs(1 - ratio), 1.0)
        if size_sim > 0.7:
            evidence.append(f"Similar position sizes (ratio: {ratio:.2f})")
    else:
        size_sim = 0.0

    # Composite score
    score = (
        0.50 * timing_score
        + 0.30 * price_overlap
        + 0.20 * size_sim
    )

    return {"score": round(score, 4), "evidence": evidence}


def _find_co_occurring_trades(trades1: list, trades2: list) -> list[tuple]:
    """Find trades in both wallets that occurred within SAME_TRADE_WINDOW_SECONDS of each other."""
    co_trades = []
    for t1 in trades1:
        if not t1.occurred_at or not t1.market_id:
            continue
        for t2 in trades2:
            if not t2.occurred_at or not t2.market_id:
                continue
            if str(t1.market_id) != str(t2.market_id):
                continue
            delta = abs((t1.occurred_at - t2.occurred_at).total_seconds())
            if delta <= SAME_TRADE_WINDOW_SECONDS:
                co_trades.append((t1, t2))
    return co_trades


def _build_clusters(
    wallet_ids: list[str],
    similarity_matrix: dict[tuple[str, str], dict],
    threshold: float,
) -> list[dict]:
    """Union-find clustering."""
    parent = {wid: wid for wid in wallet_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        parent[find(x)] = find(y)

    for (w1, w2), sim in similarity_matrix.items():
        if sim["score"] >= threshold:
            union(w1, w2)

    # Group by root
    groups: dict[str, list[str]] = defaultdict(list)
    for wid in wallet_ids:
        groups[find(wid)].append(wid)

    clusters = []
    for root, members in groups.items():
        # Collect evidence and scores for this cluster
        all_evidence = []
        scores = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                key = (members[i], members[j])
                rev_key = (members[j], members[i])
                sim = similarity_matrix.get(key) or similarity_matrix.get(rev_key)
                if sim:
                    scores.append(sim["score"])
                    all_evidence.extend(sim["evidence"])

        clusters.append({
            "members": members,
            "avg_similarity": sum(scores) / len(scores) if scores else 0.0,
            "max_similarity": max(scores) if scores else 0.0,
            "evidence": list(set(all_evidence))[:5],  # deduplicate, limit to 5
        })

    return clusters
