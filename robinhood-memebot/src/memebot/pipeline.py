"""Per-pool processing: state -> decide -> gates -> executor -> JSONL log."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from .chain_reader import PoolCreated
from .config import Config
from .executor import Executor
from .gates import apply_gates
from .state import Reader, build_state


class DecisionLog:
    """Append-only JSONL, one line per pool, buys and non-buys alike."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        reader: Reader,
        decide: Callable[[dict], dict],
        executor: Executor,
        log: DecisionLog,
        on_result: Callable[[dict], None] = lambda r: None,
        state_retries: int = 3,
    ):
        self.cfg, self.reader, self.decide = cfg, reader, decide
        self.executor, self.log, self.on_result = executor, log, on_result
        self.state_retries = state_retries

    def process(self, ev: PoolCreated) -> dict:
        record: dict = {
            "ts": time.time(),
            "pool": ev.pool,
            "block": ev.block_number,
            "tx": ev.tx_hash,
            "state": None,
            "jev_raw": None,
            "decision": None,
            "gate_failures": [],
            "final_action": "skip",
            "position": None,
            "error": None,
        }
        try:
            record["state"] = self._state_with_retry(ev)
            decision = self.decide(record["state"])
            record["jev_raw"] = decision.get("raw")
            record["decision"] = {k: v for k, v in decision.items() if k != "raw"}
            gate = apply_gates(record["state"], decision, self.cfg)
            record["final_action"], record["gate_failures"] = gate.final_action, gate.failed
            if gate.final_action == "buy":
                pos = self.executor.buy(record["state"], self.cfg.paper_position_usd)
                record["position"] = pos.__dict__
        except Exception as e:  # never let one pool kill the monitor; fail closed to skip
            record["final_action"] = "skip"
            record["error"] = f"{type(e).__name__}: {e}"
        self.log.write(record)
        self.on_result(record)
        return record

    def _state_with_retry(self, ev: PoolCreated) -> dict:
        for attempt in range(self.state_retries):
            try:
                return build_state(self.reader, self.cfg, ev)
            except Exception:
                if attempt == self.state_retries - 1:
                    raise
                time.sleep(1 + attempt)
        raise AssertionError("unreachable")
