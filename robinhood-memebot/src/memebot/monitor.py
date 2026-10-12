"""Poll loop: watch the factory for PoolCreated and feed each pool to the pipeline."""
from __future__ import annotations

import threading
from typing import Callable

from .chain_reader import ChainReader
from .pipeline import Pipeline


def run_monitor(
    reader: ChainReader,
    pipeline: Pipeline,
    poll_interval: float,
    stop: threading.Event,
    start_block: int | None = None,
    on_status: Callable[[str], None] = lambda s: None,
) -> None:
    next_block = start_block if start_block is not None else reader.head() + 1
    on_status(f"watching from block {next_block}")
    while not stop.is_set():
        try:
            head = reader.head()
            if head >= next_block:
                # materialize first so a mid-range RPC error doesn't skip already-yielded logs
                events = list(reader.pool_created_events(next_block, head))
                next_block = head + 1
                for ev in events:
                    pipeline.process(ev)
            on_status(f"head {head}")
        except Exception as e:
            on_status(f"rpc error: {type(e).__name__}: {e}")
        stop.wait(poll_interval)
