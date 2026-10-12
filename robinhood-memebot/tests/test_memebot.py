import json

import pytest
from web3 import Web3

from memebot.chain_reader import (POOL_CREATED_TOPIC, ChainReader, PoolCreated, Slot0, TokenInfo,
                                  price_token1_per_token0)
from memebot.config import Config
from memebot.executor import LiveExecutor, PaperExecutor
from memebot.gates import apply_gates
from memebot.pipeline import DecisionLog, Pipeline
from memebot.state import build_state

TOKEN = "0x" + "11" * 20
USDC = "0x" + "22" * 20
POOL = "0x" + "33" * 20
Q96 = 2**96


def cfg(**kw):
    return Config(quote_tokens={USDC.lower(): "usd"}, **kw)


GOOD_STATE = {"liquidity_usd": 50000, "owner_renounced": True, "contract_has_code": True}
GOOD_DEC = {"action": "buy", "confidence": 0.9, "risk_score": 3}


def test_topic_matches_uniswap_v3():
    assert POOL_CREATED_TOPIC == "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"


def test_price_math():
    # sqrtPrice = 2^96 -> ratio 1; with 18 vs 6 decimals, 1 raw = 1e12 human
    assert price_token1_per_token0(Q96, 18, 18) == pytest.approx(1.0)
    assert price_token1_per_token0(Q96, 18, 6) == pytest.approx(1e12)


@pytest.mark.parametrize("state_patch,dec_patch,expect", [
    ({}, {}, "buy"),
    ({"liquidity_usd": 19999.99}, {}, "skip"),
    ({}, {"confidence": 0.69}, "skip"),
    ({}, {"risk_score": 5}, "skip"),
    ({"owner_renounced": False}, {}, "skip"),
    ({"contract_has_code": False}, {}, "skip"),
    ({"liquidity_usd": None}, {}, "skip"),
    ({}, {"risk_score": None}, "skip"),
    ({}, {"confidence": "0.99"}, "skip"),
    ({"liquidity_usd": 20000}, {"confidence": 0.70, "risk_score": 4}, "buy"),
    ({}, {"action": "sell"}, "skip"),
])
def test_gates(state_patch, dec_patch, expect):
    r = apply_gates({**GOOD_STATE, **state_patch}, {**GOOD_DEC, **dec_patch}, cfg())
    assert r.final_action == expect
    assert bool(r.failed) == (expect == "skip")


def test_gates_pass_through_hold_and_skip():
    assert apply_gates({}, {"action": "hold"}, cfg()).final_action == "hold"
    assert apply_gates({}, {"action": "skip"}, cfg()).final_action == "skip"


class FakeReader:
    def __init__(self, renounced=True, has_code=True, usdc_bal=30_000 * 10**6, tok_bal=0):
        self.renounced, self.has_code, self.usdc_bal, self.tok_bal = renounced, has_code, usdc_bal, tok_bal

    def token_info(self, a):
        if a == USDC:
            return TokenInfo(a, True, 10**12, 6, "USDC", None, None)
        return TokenInfo(a, self.has_code, 10**9 * 10**18, 18, "MEME", "0x" + "0" * 40, self.renounced)

    def slot0(self, pool):
        return Slot0(int(1e-9 * Q96), 0, True)  # memecoin is token0: price = 1e-6 USDC per MEME

    def balance_of(self, token, holder):
        return self.usdc_bal if token == USDC else self.tok_bal


EV = PoolCreated(POOL, TOKEN, USDC, 3000, 60, 123, "ab")


def test_build_state_liquidity_and_price():
    s = build_state(FakeReader(tok_bal=5 * 10**8 * 10**18), cfg(), EV)
    assert s["token_address"] == TOKEN and s["owner_renounced"] and s["contract_has_code"]
    assert s["price"] == pytest.approx(1e-6, rel=1e-6)
    assert s["liquidity_usd"] == pytest.approx(30_000 + 5e8 * 1e-6, rel=1e-6)  # both sides valued


def test_build_state_quote_first_and_unknown_quote():
    ev = PoolCreated(POOL, USDC, TOKEN, 3000, 60, 1, "ab")  # USDC is token0 now
    s = build_state(FakeReader(), cfg(), ev)
    assert s["token_address"] == TOKEN
    s = build_state(FakeReader(), Config(), EV)  # no quote tokens configured -> fail closed
    assert s["liquidity_usd"] == 0.0


def run_pipeline(tmp_path, reader, decide):
    c = cfg()
    ex = PaperExecutor(tmp_path / "pos.jsonl")
    log = DecisionLog(tmp_path / "dec.jsonl")
    seen = []
    p = Pipeline(c, reader, decide, ex, log, on_result=seen.append, state_retries=1)
    return p.process(EV), ex, log, seen


def lines(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_pipeline_buy_logged_and_paper_position(tmp_path):
    dec = lambda s: {**GOOD_DEC, "raw": {"action": {"choice": "buy"}}}
    rec, ex, log, seen = run_pipeline(tmp_path, FakeReader(), dec)
    assert rec["final_action"] == "buy" and len(ex.positions) == 1
    assert ex.positions[0].entry_price == pytest.approx(1e-6, rel=1e-6) and ex.positions[0].opened_at
    (row,) = lines(log.path)
    assert row["jev_raw"] == {"action": {"choice": "buy"}} and row["state"]["token_address"] == TOKEN
    assert len(lines(tmp_path / "pos.jsonl")) == 1 and seen == [rec]


def test_pipeline_gated_buy_is_logged_as_skip(tmp_path):
    rec, ex, log, _ = run_pipeline(tmp_path, FakeReader(renounced=False), lambda s: dict(GOOD_DEC))
    assert rec["final_action"] == "skip" and not ex.positions
    assert "owner not renounced" in lines(log.path)[0]["gate_failures"]
    assert lines(log.path)[0]["decision"]["action"] == "buy"  # engine's view preserved


def test_pipeline_engine_error_fails_closed(tmp_path):
    def boom(s): raise TimeoutError("jev down")
    rec, ex, log, _ = run_pipeline(tmp_path, FakeReader(), boom)
    assert rec["final_action"] == "skip" and "TimeoutError" in rec["error"] and not ex.positions
    assert len(lines(log.path)) == 1


def test_live_executor_unimplemented():
    with pytest.raises(NotImplementedError):
        LiveExecutor().buy({}, 1)


def test_pool_created_decoding():
    def topic(addr): return bytes.fromhex(addr[2:].rjust(64, "0"))
    data = (60).to_bytes(32, "big") + bytes.fromhex(POOL[2:].rjust(64, "0"))

    class W3:
        class eth:
            @staticmethod
            def get_logs(f):
                assert f["fromBlock"] <= f["toBlock"]
                return [{"topics": [bytes.fromhex(POOL_CREATED_TOPIC[2:]), topic(TOKEN), topic(USDC),
                                    (3000).to_bytes(32, "big")],
                         "data": data, "blockNumber": f["fromBlock"], "transactionHash": b"\x01" * 32}]

    r = ChainReader(Config(max_block_range=10), w3=W3)
    evs = list(r.pool_created_events(100, 125))
    assert len(evs) == 3  # 100-109, 110-119, 120-125
    e = evs[0]
    assert (e.token0, e.token1, e.fee, e.tick_spacing, e.pool) == (
        Web3.to_checksum_address(TOKEN), Web3.to_checksum_address(USDC), 3000, 60, Web3.to_checksum_address(POOL))
