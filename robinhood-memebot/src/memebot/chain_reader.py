"""Read-only access to Robinhood Chain: pool launches, token facts, pool price."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from web3 import Web3
from web3.exceptions import ContractLogicError

from .config import Config

POOL_CREATED_TOPIC = "0x" + Web3.keccak(
    text="PoolCreated(address,address,uint24,int24,address)"
).hex().removeprefix("0x")

# 4-byte selectors
SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_DECIMALS = "0x313ce567"
SEL_SYMBOL = "0x95d89b41"
SEL_OWNER = "0x8da5cb5b"
SEL_BALANCE_OF = "0x70a08231"
SEL_SLOT0 = "0x3850c7bd"
SEL_LIQUIDITY = "0x1a686502"

RENOUNCED_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}


@dataclass(frozen=True)
class PoolCreated:
    pool: str
    token0: str
    token1: str
    fee: int
    tick_spacing: int
    block_number: int
    tx_hash: str


@dataclass(frozen=True)
class TokenInfo:
    address: str
    has_code: bool
    total_supply_raw: int | None
    decimals: int | None
    symbol: str | None
    owner: str | None  # None => no owner() function
    owner_renounced: bool | None  # None => unknown (no owner() function)


@dataclass(frozen=True)
class Slot0:
    sqrt_price_x96: int
    tick: int
    initialized: bool


def price_token1_per_token0(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    """Human-unit price of token0 denominated in token1, from slot0.sqrtPriceX96."""
    ratio = (sqrt_price_x96 / 2**96) ** 2
    return ratio * 10 ** (decimals0 - decimals1)


def _addr_from_topic(topic: bytes) -> str:
    return Web3.to_checksum_address(topic[-20:])


def _signed(word: bytes, bits: int) -> int:
    return int.from_bytes(word[-(bits // 8):], "big", signed=True)


def _as_bytes(x) -> bytes:
    return bytes(x) if not isinstance(x, str) else bytes.fromhex(x.removeprefix("0x"))


class ChainReader:
    def __init__(self, cfg: Config, w3: Web3 | None = None):
        self.cfg = cfg
        self.w3 = w3 or Web3(Web3.HTTPProvider(cfg.rpc_url, request_kwargs={"timeout": 15}))
        self.factory = Web3.to_checksum_address(cfg.factory)

    # --- connection -------------------------------------------------------
    def verify_chain(self) -> None:
        got = self.w3.eth.chain_id
        if got != self.cfg.chain_id:
            raise RuntimeError(f"RPC reports chain id {got}, expected {self.cfg.chain_id}")

    def head(self) -> int:
        return self.w3.eth.block_number

    # --- factory events ---------------------------------------------------
    def pool_created_events(self, from_block: int, to_block: int) -> Iterator[PoolCreated]:
        """PoolCreated logs in [from_block, to_block], chunked to MAX_BLOCK_RANGE."""
        step = self.cfg.max_block_range
        for start in range(from_block, to_block + 1, step):
            end = min(start + step - 1, to_block)
            logs = self.w3.eth.get_logs(
                {
                    "address": self.factory,
                    "topics": [POOL_CREATED_TOPIC],
                    "fromBlock": start,
                    "toBlock": end,
                }
            )
            for log in logs:
                topics = [_as_bytes(t) for t in log["topics"]]
                data = _as_bytes(log["data"])
                yield PoolCreated(
                    token0=_addr_from_topic(topics[1]),
                    token1=_addr_from_topic(topics[2]),
                    fee=int.from_bytes(topics[3], "big"),
                    tick_spacing=_signed(data[0:32], 24),
                    pool=_addr_from_topic(data[32:64]),
                    block_number=log["blockNumber"],
                    tx_hash=_as_bytes(log["transactionHash"]).hex(),
                )

    # --- low-level call ---------------------------------------------------
    def _call(self, to: str, data: str) -> bytes | None:
        """eth_call; None if it reverts or returns nothing. Transport errors propagate."""
        try:
            out = self.w3.eth.call({"to": Web3.to_checksum_address(to), "data": data})
        except ContractLogicError:
            return None
        return bytes(out) or None

    def _uint(self, to: str, selector: str, arg_addr: str | None = None) -> int | None:
        data = selector
        if arg_addr:
            data += arg_addr.lower().removeprefix("0x").rjust(64, "0")
        out = self._call(to, data)
        return int.from_bytes(out[:32], "big") if out and len(out) >= 32 else None

    # --- token facts ------------------------------------------------------
    def has_code(self, address: str) -> bool:
        return len(self.w3.eth.get_code(Web3.to_checksum_address(address))) > 0

    def token_info(self, address: str) -> TokenInfo:
        address = Web3.to_checksum_address(address)
        if not self.has_code(address):
            return TokenInfo(address, False, None, None, None, None, None)

        owner_raw = self._call(address, SEL_OWNER)
        owner = _addr_from_topic(owner_raw[:32]) if owner_raw and len(owner_raw) >= 32 else None
        renounced = None if owner is None else owner.lower() in RENOUNCED_ADDRESSES

        return TokenInfo(
            address=address,
            has_code=True,
            total_supply_raw=self._uint(address, SEL_TOTAL_SUPPLY),
            decimals=self._uint(address, SEL_DECIMALS),
            symbol=self._symbol(address),
            owner=owner,
            owner_renounced=renounced,
        )

    def _symbol(self, address: str) -> str | None:
        out = self._call(address, SEL_SYMBOL)
        if not out:
            return None
        try:
            if len(out) >= 96:  # ABI-encoded string
                n = int.from_bytes(out[32:64], "big")
                return out[64:64 + n].decode("utf-8", "replace")
            return out.rstrip(b"\x00").decode("utf-8", "replace")  # bytes32 style
        except Exception:
            return None

    def balance_of(self, token: str, holder: str) -> int | None:
        return self._uint(token, SEL_BALANCE_OF, holder)

    # --- pool -------------------------------------------------------------
    def slot0(self, pool: str) -> Slot0 | None:
        out = self._call(pool, SEL_SLOT0)
        if not out or len(out) < 64:
            return None
        sqrt_price = int.from_bytes(out[0:32], "big")
        return Slot0(sqrt_price, _signed(out[32:64], 24), sqrt_price != 0)

    def pool_liquidity(self, pool: str) -> int | None:
        return self._uint(pool, SEL_LIQUIDITY)
