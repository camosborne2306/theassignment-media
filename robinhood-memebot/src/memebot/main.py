from __future__ import annotations

import argparse
import threading

from rich.live import Live

from .chain_reader import ChainReader
from .config import Config
from .dashboard import DashboardState
from .executor import PaperExecutor
from .monitor import run_monitor
from .pipeline import DecisionLog, Pipeline


def main() -> None:
    ap = argparse.ArgumentParser(description="Robinhood Chain memecoin monitor (paper trading)")
    ap.add_argument("--no-dashboard", action="store_true", help="plain log output instead of the TUI")
    args = ap.parse_args()

    cfg = Config.from_env()
    from . import decision_engine  # imported after .env is loaded (see memebot/__init__)

    if not decision_engine.JEV_API_KEY:
        raise SystemExit("TYPESAFE_API_KEY is not set (see .env.example)")
    if not cfg.quote_tokens:
        print("warning: QUOTE_TOKENS is empty, so liquidity_usd will be 0 and every buy will be gated out")

    reader = ChainReader(cfg)
    reader.verify_chain()

    dash = DashboardState()
    pipeline = Pipeline(
        cfg, reader, decision_engine.decide,
        PaperExecutor(cfg.data_dir / "positions.jsonl"),
        DecisionLog(cfg.data_dir / "decisions.jsonl"),
        on_result=dash.add_result if not args.no_dashboard else lambda r: print(
            r["final_action"].upper(), r["pool"], r["gate_failures"], r["error"] or ""),
    )

    stop = threading.Event()
    worker = threading.Thread(
        target=run_monitor,
        args=(reader, pipeline, cfg.poll_interval, stop, cfg.start_block, dash.set_status),
        daemon=True,
    )
    worker.start()
    try:
        if args.no_dashboard:
            worker.join()
        else:
            with Live(dash.render(), refresh_per_second=2, screen=True) as live:
                while worker.is_alive():
                    live.update(dash.render())
                    stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
