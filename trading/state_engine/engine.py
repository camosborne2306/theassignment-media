"""Deterministic state engine.

Turns the live order book plus our own fills into one compact numeric snapshot.
Everything is computed in code. The decision model only ever sees the snapshot.

Causality rules:
  * Every input carries an event timestamp `ts` (seconds, float).
  * Inputs must arrive in non-decreasing `ts` order, or they are rejected.
  * A snapshot at time T is built only from inputs with ts <= T.
  * Every snapshot field is derived from that data, so none leaks from the future.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional, Sequence, Tuple

Level = Tuple[float, float]  # (price, size)


class StateEngineError(ValueError):
    pass


@dataclass(frozen=True)
class BookUpdate:
    ts: float
    bids: Sequence[Level]  # best (highest) first
    asks: Sequence[Level]  # best (lowest) first


@dataclass(frozen=True)
class Fill:
    ts: float
    side: str  # "buy" or "sell"
    price: float
    size: float
    fee: float = 0.0


@dataclass(frozen=True)
class StateEngineConfig:
    imbalance_levels: int = 5
    vol_window_s: float = 300.0
    max_gap_s: float = 5.0  # larger gap between book updates marks the snapshot stale
    starting_cash: float = 10_000.0


@dataclass(frozen=True)
class Snapshot:
    ts: float
    mid: float
    spread_bps: float
    imbalance: float  # (bid_size - ask_size) / total over top N levels, in [-1, 1]
    realized_vol: float  # sqrt(sum of squared log returns) over the window
    vol_obs: int  # number of returns behind realized_vol
    inventory: float  # signed base-asset position
    equity: float
    drawdown: float  # fraction below peak equity, in [0, 1]
    stale: bool
    gap_s: float  # time since the previous book update

    def to_compact(self) -> str:
        """One-line, fixed-field text for the model. Well under 400 tokens."""
        return (
            f"t={self.ts:.3f} mid={self.mid:.6g} spr_bps={self.spread_bps:.2f} "
            f"imb={self.imbalance:+.3f} vol={self.realized_vol:.6f} n={self.vol_obs} "
            f"inv={self.inventory:.6g} eq={self.equity:.2f} dd={self.drawdown:.4f} "
            f"stale={int(self.stale)} gap={self.gap_s:.2f}"
        )


@dataclass
class StateEngine:
    config: StateEngineConfig = field(default_factory=StateEngineConfig)

    def __post_init__(self) -> None:
        self._cash = self.config.starting_cash
        self._inventory = 0.0
        self._peak_equity = self.config.starting_cash
        self._last_ts: Optional[float] = None
        self._last_book_ts: Optional[float] = None
        self._last_mid: Optional[float] = None
        self._returns: Deque[Tuple[float, float]] = deque()  # (ts, log return)

    # -- inputs -------------------------------------------------------------

    def on_fill(self, fill: Fill) -> None:
        if fill.side not in ("buy", "sell"):
            raise StateEngineError(f"bad side {fill.side!r}")
        if fill.size <= 0 or fill.price <= 0:
            raise StateEngineError("fill size and price must be positive")
        self._check_order(fill.ts)
        signed = fill.size if fill.side == "buy" else -fill.size
        self._inventory += signed
        self._cash -= signed * fill.price + fill.fee
        self._last_ts = fill.ts

    def on_book(self, book: BookUpdate) -> Snapshot:
        self._check_order(book.ts)
        mid, spread_bps, imbalance = self._book_stats(book)

        gap = 0.0 if self._last_book_ts is None else book.ts - self._last_book_ts
        if self._last_mid is not None:
            self._returns.append((book.ts, math.log(mid / self._last_mid)))
        cutoff = book.ts - self.config.vol_window_s
        while self._returns and self._returns[0][0] < cutoff:
            self._returns.popleft()
        realized_vol = math.sqrt(sum(r * r for _, r in self._returns))

        equity = self._cash + self._inventory * mid
        self._peak_equity = max(self._peak_equity, equity)
        drawdown = (
            max(0.0, (self._peak_equity - equity) / self._peak_equity)
            if self._peak_equity > 0
            else 0.0
        )

        self._last_ts = book.ts
        self._last_book_ts = book.ts
        self._last_mid = mid
        return Snapshot(
            ts=book.ts,
            mid=mid,
            spread_bps=spread_bps,
            imbalance=imbalance,
            realized_vol=realized_vol,
            vol_obs=len(self._returns),
            inventory=self._inventory,
            equity=equity,
            drawdown=drawdown,
            stale=gap > self.config.max_gap_s,
            gap_s=gap,
        )

    # -- internals ----------------------------------------------------------

    def _check_order(self, ts: float) -> None:
        if not math.isfinite(ts):
            raise StateEngineError("timestamp must be finite")
        if self._last_ts is not None and ts < self._last_ts:
            raise StateEngineError(
                f"out-of-order input: ts={ts} < last_ts={self._last_ts}"
            )

    def _book_stats(self, book: BookUpdate) -> Tuple[float, float, float]:
        if not book.bids or not book.asks:
            raise StateEngineError("book needs at least one bid and one ask")
        for price, size in list(book.bids) + list(book.asks):
            if not (math.isfinite(price) and math.isfinite(size)) or price <= 0 or size < 0:
                raise StateEngineError("invalid book level")
        best_bid, best_ask = book.bids[0][0], book.asks[0][0]
        if best_bid >= best_ask:
            raise StateEngineError(f"crossed book: bid {best_bid} >= ask {best_ask}")
        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 1e4
        n = self.config.imbalance_levels
        bid_size = sum(s for _, s in book.bids[:n])
        ask_size = sum(s for _, s in book.asks[:n])
        total = bid_size + ask_size
        imbalance = 0.0 if total == 0 else (bid_size - ask_size) / total
        return mid, spread_bps, imbalance
