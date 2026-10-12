# Robinhood Chain memecoin monitor (paper trading only)

Watches the Uniswap V3 factory on Robinhood Chain (chain ID 4663) for new pools, builds a state
snapshot for each, asks the decision engine (Jev) what to do, applies hard gates in code, and
records simulated buys. **No transactions are ever sent**: `LiveExecutor.buy` raises `NotImplementedError`.

## Run

```bash
cd robinhood-memebot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
cp .env.example .env     # then edit: TYPESAFE_API_KEY, QUOTE_TOKENS, ETH_USD_PRICE
python -m memebot            # TUI dashboard (Ctrl+C to quit)
python -m memebot --no-dashboard   # plain output
pytest                       # offline tests (no RPC needed)
```

## Flow per new pool

1. `chain_reader` decodes `PoolCreated` from the factory.
2. `state.build_state` assembles: `token_address`, `total_supply`, `owner_renounced`,
   `contract_has_code`, `price` (quote units per token), `liquidity_usd`, plus symbol, pool, block, etc.
3. `decision_engine.decide(state)` returns `{action, confidence, risk_score, raw}`.
4. `gates.apply_gates` downgrades a `buy` to `skip` unless **all** pass:
   liquidity ≥ `MIN_LIQUIDITY_USD` (20000), confidence ≥ `MIN_CONFIDENCE` (0.70),
   risk ≤ `MAX_RISK_SCORE` (4), owner renounced, contract has code.
   Missing/malformed values fail closed. Decision-engine exceptions (timeout, bad key) become a logged `skip`.
5. Only a passing buy reaches `PaperExecutor`, which appends a position (entry price, timestamp, size) to
   `data/positions.jsonl` and is highlighted green in the dashboard.
6. Every pool, buy or not, is appended to `data/decisions.jsonl` with the full state, Jev's raw answer,
   the normalized decision, gate failures, final action and any error.

## Things you must know

- **`QUOTE_TOKENS` is required for `liquidity_usd`.** I did not hard-code any Robinhood Chain token
  addresses. List the stablecoin(s)/WETH you trust as `address:usd` or `address:eth`; ETH-quoted pools
  are valued with the static `ETH_USD_PRICE`. A pool with no recognised quote token gets
  `liquidity_usd = 0` and is gated out.
- `liquidity_usd` = pool TVL: the pool's balance of the quote token plus its balance of the memecoin valued at the
  pool price (read via `balanceOf(pool)`, so donated tokens inflate it).
- **Owner check:** renounced = `owner()` returns the zero or `0x…dEaD` address. A token with no `owner()` function
  is treated as *not* renounced (fail closed); set `TREAT_NO_OWNER_AS_RENOUNCED=true` to change that. This does not
  detect other admin patterns (AccessControl, proxies, mint/blacklist functions), so it's a weak rug signal.
- New pools may be uninitialized or have no liquidity yet at detection time (`pool_initialized` is in the state).
- `decision_engine.py` is your file with one addition: `decide` also returns `"raw": answers` so the JSONL log
  can keep Jev's raw answer. `state` is sent to `api.typesafe.ai`; it contains only public on-chain data.
- `decide` runs synchronously (10s timeout) on the monitor thread, so a slow API delays processing of later pools.
- The code was not run against the live RPC in the build environment (network blocked); the event topic/ABI
  decoding and math are covered by offline tests. Do a first run with `--no-dashboard` and `START_BLOCK` set to
  a block with known pool creations to confirm.

## Layout

```
src/memebot/  config, chain_reader, state, decision_engine, gates, executor, pipeline, monitor, dashboard, main
tests/        offline tests
```
