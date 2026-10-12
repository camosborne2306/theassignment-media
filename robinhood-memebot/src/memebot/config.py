from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def _quote_tokens(raw: str) -> dict[str, str]:
    """'0xabc:usd,0xdef:eth' -> {'0xabc': 'usd', '0xdef': 'eth'} (lowercased addresses)."""
    out: dict[str, str] = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        addr, _, kind = item.partition(":")
        kind = kind.strip().lower()
        if kind not in ("usd", "eth"):
            raise ValueError(f"QUOTE_TOKENS entry {item!r}: kind must be 'usd' or 'eth'")
        out[addr.strip().lower()] = kind
    return out


@dataclass(frozen=True)
class Config:
    rpc_url: str = "https://rpc.mainnet.chain.robinhood.com"
    chain_id: int = 4663
    factory: str = "0x1f7d7550B1b028f7571E69A784071F0205FD2EfA"
    poll_interval: float = 5.0
    max_block_range: int = 500
    start_block: int | None = None
    quote_tokens: dict[str, str] = field(default_factory=dict)
    eth_usd_price: float = 0.0
    treat_no_owner_as_renounced: bool = False
    min_liquidity_usd: float = 20000.0
    min_confidence: float = 0.70
    max_risk_score: int = 4
    paper_position_usd: float = 100.0
    data_dir: Path = Path("data")

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        start = e("START_BLOCK", "").strip()
        return cls(
            rpc_url=e("RPC_URL", cls.rpc_url),
            chain_id=int(e("CHAIN_ID", cls.chain_id)),
            factory=e("UNISWAP_V3_FACTORY", cls.factory),
            poll_interval=float(e("POLL_INTERVAL", cls.poll_interval)),
            max_block_range=int(e("MAX_BLOCK_RANGE", cls.max_block_range)),
            start_block=int(start) if start else None,
            quote_tokens=_quote_tokens(e("QUOTE_TOKENS", "")),
            eth_usd_price=float(e("ETH_USD_PRICE", 0) or 0),
            treat_no_owner_as_renounced=_bool(e("TREAT_NO_OWNER_AS_RENOUNCED", "false")),
            min_liquidity_usd=float(e("MIN_LIQUIDITY_USD", cls.min_liquidity_usd)),
            min_confidence=float(e("MIN_CONFIDENCE", cls.min_confidence)),
            max_risk_score=int(e("MAX_RISK_SCORE", cls.max_risk_score)),
            paper_position_usd=float(e("PAPER_POSITION_USD", cls.paper_position_usd)),
            data_dir=Path(e("DATA_DIR", "data")),
        )
