"""Trade execution. Paper only: the live swap is deliberately unimplemented."""
from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Position:
    token_address: str
    symbol: str | None
    pool_address: str
    entry_price: float | None  # quote-token units per memecoin
    quote_symbol: str | None
    size_usd: float
    opened_at: float
    paper: bool = True


class Executor(ABC):
    @abstractmethod
    def buy(self, state: dict, size_usd: float) -> Position: ...


class PaperExecutor(Executor):
    """Records a simulated position (entry price + timestamp). Sends no transactions."""

    def __init__(self, ledger_path: Path):
        self.ledger_path = ledger_path
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.positions: list[Position] = []
        self._lock = threading.Lock()

    def buy(self, state: dict, size_usd: float) -> Position:
        pos = Position(
            token_address=state["token_address"],
            symbol=state.get("symbol"),
            pool_address=state["pool_address"],
            entry_price=state.get("price"),
            quote_symbol=state.get("quote_symbol"),
            size_usd=size_usd,
            opened_at=time.time(),
        )
        with self._lock:
            self.positions.append(pos)
            with self.ledger_path.open("a") as f:
                f.write(json.dumps(asdict(pos)) + "\n")
        return pos


class LiveExecutor(Executor):
    """Placeholder for real swaps (e.g. Uniswap V3 SwapRouter exactInputSingle)."""

    def buy(self, state: dict, size_usd: float) -> Position:
        raise NotImplementedError("Live trading is not implemented; use PaperExecutor.")
