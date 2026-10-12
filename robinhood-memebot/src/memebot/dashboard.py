"""Rich terminal dashboard: launch feed + decisions, action log, paper positions."""
from __future__ import annotations

import threading
import time
from collections import deque

from rich.console import Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

STYLE = {"buy": "bold black on green", "hold": "yellow", "skip": "dim"}


def _short(a: str | None) -> str:
    return f"{a[:6]}…{a[-4:]}" if a else "-"


def _t(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


class DashboardState:
    def __init__(self, cap: int = 200):
        self.lock = threading.Lock()
        self.launches: deque[dict] = deque(maxlen=cap)
        self.actions: deque[tuple[float, str, str]] = deque(maxlen=cap)
        self.status = "starting"
        self.positions: list[dict] = []

    def add_result(self, rec: dict) -> None:
        with self.lock:
            self.launches.appendleft(rec)
            st = rec.get("state") or {}
            who = st.get("symbol") or _short(st.get("token_address") or rec["pool"])
            if rec["final_action"] == "buy":
                p = rec["position"]
                self.positions.append(p)
                self.actions.appendleft((rec["ts"], "buy", f"PAPER BUY {who} ${p['size_usd']:g} @ {p['entry_price']}"))
            elif rec.get("error"):
                self.actions.appendleft((rec["ts"], "skip", f"ERROR {who}: {rec['error']}"))
            else:
                why = "; ".join(rec["gate_failures"]) or "engine said so"
                self.actions.appendleft((rec["ts"], rec["final_action"], f"{rec['final_action'].upper()} {who}: {why}"))

    def set_status(self, s: str) -> None:
        with self.lock:
            self.status = s

    def render(self) -> Layout:
        with self.lock:
            feed = Table(expand=True, header_style="bold")
            for c in ("Time", "Blk", "Token", "Liq $", "Price", "Engine", "Conf", "Risk", "Final"):
                feed.add_column(c, no_wrap=True)
            for r in list(self.launches)[:15]:
                st, d = r.get("state") or {}, r.get("decision") or {}
                fin = r["final_action"]
                sty = STYLE[fin]
                liq = st.get("liquidity_usd")
                feed.add_row(
                    _t(r["ts"]), str(r["block"]),
                    st.get("symbol") or _short(st.get("token_address")),
                    f"{liq:,.0f}" if liq is not None else "-",
                    f"{st['price']:.6g}" if st.get("price") else "-",
                    str(d.get("action", "-")),
                    f"{d['confidence']:.2f}" if isinstance(d.get("confidence"), (int, float)) else "-",
                    str(d.get("risk_score", "-")),
                    Text(fin.upper(), style=sty),
                    style=sty if fin == "buy" else None,
                )
            log = Group(*[Text(f"{_t(ts)}  {msg}", style=STYLE[k]) for ts, k, msg in list(self.actions)[:12]])
            pos = Table(expand=True, header_style="bold")
            for c in ("Opened", "Token", "Entry", "Size $"):
                pos.add_column(c)
            for p in self.positions[-8:][::-1]:
                pos.add_row(_t(p["opened_at"]), p["symbol"] or _short(p["token_address"]),
                            str(p["entry_price"]), f"{p['size_usd']:g}")
            status = self.status

        lay = Layout()
        lay.split_column(Layout(Panel(Text(f"PAPER TRADING ONLY · {status}"), title="Robinhood Chain memebot"), size=3),
                         Layout(name="body"))
        lay["body"].split_row(
            Layout(Panel(feed, title="New pools + decisions"), ratio=3),
            Layout(name="side", ratio=2),
        )
        lay["side"].split_column(Layout(Panel(log, title="Action log")),
                                 Layout(Panel(pos, title=f"Paper positions ({len(self.positions)})")))
        return lay
