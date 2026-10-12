"""Builds the state dict handed to the decision engine."""
from __future__ import annotations

import time
from typing import Protocol

from .chain_reader import PoolCreated, Slot0, TokenInfo, price_token1_per_token0
from .config import Config


class Reader(Protocol):
    def token_info(self, address: str) -> TokenInfo: ...
    def slot0(self, pool: str) -> Slot0 | None: ...
    def balance_of(self, token: str, holder: str) -> int | None: ...


def _human(raw: int | None, decimals: int | None) -> float | None:
    if raw is None or decimals is None:
        return None
    return raw / 10**decimals


def build_state(reader: Reader, cfg: Config, ev: PoolCreated) -> dict:
    """Snapshot of one new pool.

    The memecoin is whichever side is NOT a configured quote token (token0 if neither/both are).
    `price` is quote-token units per 1 memecoin. `liquidity_usd` is the pool's TVL:
    both sides' balances held by the pool, valued in USD via the quote token.
    Anything that can't be determined is None (liquidity_usd falls back to 0.0 so the gate fails closed).
    """
    q = cfg.quote_tokens
    t0_quote, t1_quote = ev.token0.lower() in q, ev.token1.lower() in q
    token_is_0 = not (t0_quote and not t1_quote)
    token_addr, quote_addr = (ev.token0, ev.token1) if token_is_0 else (ev.token1, ev.token0)

    token = reader.token_info(token_addr)
    quote = reader.token_info(quote_addr)
    slot0 = reader.slot0(ev.pool)

    price = None
    if slot0 and slot0.initialized and token.decimals is not None and quote.decimals is not None:
        d0, d1 = (token.decimals, quote.decimals) if token_is_0 else (quote.decimals, token.decimals)
        p01 = price_token1_per_token0(slot0.sqrt_price_x96, d0, d1)
        price = p01 if token_is_0 else (1 / p01 if p01 else None)

    quote_kind = q.get(quote_addr.lower())
    quote_usd = 1.0 if quote_kind == "usd" else (cfg.eth_usd_price if quote_kind == "eth" else 0.0)

    liquidity_usd = 0.0
    quote_bal = _human(reader.balance_of(quote_addr, ev.pool), quote.decimals)
    token_bal = _human(reader.balance_of(token_addr, ev.pool), token.decimals)
    if quote_kind and quote_usd > 0 and quote_bal is not None:
        liquidity_usd = quote_bal * quote_usd
        if token_bal is not None and price is not None:
            liquidity_usd += token_bal * price * quote_usd

    owner_renounced = token.owner_renounced
    if owner_renounced is None:  # no owner() function
        owner_renounced = cfg.treat_no_owner_as_renounced

    return {
        "token_address": token_addr,
        "symbol": token.symbol,
        "pool_address": ev.pool,
        "pool_fee": ev.fee,
        "block_number": ev.block_number,
        "detected_at": time.time(),
        "total_supply": _human(token.total_supply_raw, token.decimals),
        "decimals": token.decimals,
        "owner_renounced": bool(owner_renounced),
        "owner_address": token.owner,
        "contract_has_code": token.has_code,
        "pool_initialized": bool(slot0 and slot0.initialized),
        "price": price,
        "quote_token": quote_addr,
        "quote_symbol": quote.symbol,
        "liquidity_usd": liquidity_usd,
    }
