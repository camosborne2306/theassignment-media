"""Hard gates, enforced in code after the decision engine returns.

The engine can only *recommend* a buy; a buy happens only if every gate passes.
Anything missing or malformed fails closed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config

ACTIONS = ("buy", "hold", "skip")


@dataclass(frozen=True)
class GateResult:
    final_action: str  # what the bot actually does
    failed: list[str]  # human-readable reasons a buy was downgraded


def _num(x) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return float(x)


def apply_gates(state: dict, decision: dict, cfg: Config) -> GateResult:
    action = decision.get("action")
    if action not in ACTIONS:
        return GateResult("skip", [f"invalid action {action!r}"])
    if action != "buy":
        return GateResult(action, [])  # hold/skip pass straight through

    failed: list[str] = []

    liq = _num(state.get("liquidity_usd"))
    if liq is None or liq < cfg.min_liquidity_usd:
        failed.append(f"liquidity_usd {liq} < {cfg.min_liquidity_usd:g}")

    conf = _num(decision.get("confidence"))
    if conf is None or conf < cfg.min_confidence:
        failed.append(f"confidence {conf} < {cfg.min_confidence:g}")

    risk = _num(decision.get("risk_score"))
    if risk is None or risk > cfg.max_risk_score:
        failed.append(f"risk_score {risk} > {cfg.max_risk_score}")

    if state.get("owner_renounced") is not True:
        failed.append("owner not renounced")

    if state.get("contract_has_code") is not True:
        failed.append("contract has no code")

    return GateResult("skip" if failed else "buy", failed)
