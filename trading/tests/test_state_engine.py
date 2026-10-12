import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from state_engine import BookUpdate, Fill, StateEngine, StateEngineConfig, StateEngineError


def book(ts, bid=100.0, ask=100.2, bsz=(1, 1, 1), asz=(1, 1, 1)):
    return BookUpdate(
        ts,
        [(bid - i * 0.1, s) for i, s in enumerate(bsz)],
        [(ask + i * 0.1, s) for i, s in enumerate(asz)],
    )


class StateEngineTests(unittest.TestCase):
    def test_hand_computed_values(self):
        s = StateEngine().on_book(book(1.0, bsz=(3, 1, 0), asz=(1, 1, 0)))
        self.assertAlmostEqual(s.mid, 100.1)
        self.assertAlmostEqual(s.spread_bps, 0.2 / 100.1 * 1e4)
        self.assertAlmostEqual(s.imbalance, (4 - 2) / 6)
        self.assertEqual(s.vol_obs, 0)
        self.assertEqual(s.realized_vol, 0.0)

    def test_realized_vol(self):
        e = StateEngine()
        e.on_book(book(1.0, bid=100.0, ask=100.0 + 0.2))
        s = e.on_book(book(2.0, bid=101.0, ask=101.2))
        self.assertAlmostEqual(s.realized_vol, abs(math.log(101.1 / 100.1)))
        self.assertEqual(s.vol_obs, 1)

    def test_no_lookahead_replay_prefix_matches(self):
        """A snapshot must not change when later data is appended."""
        feed = [book(float(t), bid=100 + (t % 3), ask=100.2 + (t % 3)) for t in range(1, 30)]
        full = StateEngine()
        full_snaps = [full.on_book(b) for b in feed]
        for k in (5, 12, 20):
            prefix = StateEngine()
            prefix_snaps = [prefix.on_book(b) for b in feed[:k]]
            self.assertEqual(prefix_snaps[-1], full_snaps[k - 1])

    def test_rejects_out_of_order(self):
        e = StateEngine()
        e.on_book(book(5.0))
        with self.assertRaises(StateEngineError):
            e.on_book(book(4.0))
        with self.assertRaises(StateEngineError):
            e.on_fill(Fill(3.0, "buy", 100, 1))

    def test_rejects_bad_books(self):
        e = StateEngine()
        with self.assertRaises(StateEngineError):
            e.on_book(BookUpdate(1.0, [(101, 1)], [(100, 1)]))  # crossed
        with self.assertRaises(StateEngineError):
            e.on_book(BookUpdate(1.0, [], [(100, 1)]))
        with self.assertRaises(StateEngineError):
            e.on_book(BookUpdate(1.0, [(float("nan"), 1)], [(100, 1)]))

    def test_inventory_equity_drawdown(self):
        e = StateEngine(StateEngineConfig(starting_cash=1000.0))
        e.on_book(book(1.0, bid=99.9, ask=100.1))  # mid 100
        e.on_fill(Fill(1.5, "buy", 100.0, 5.0, fee=1.0))
        s = e.on_book(book(2.0, bid=109.9, ask=110.1))  # mid 110
        self.assertAlmostEqual(s.inventory, 5.0)
        self.assertAlmostEqual(s.equity, 1000 - 500 - 1 + 5 * 110)  # 1049
        self.assertEqual(s.drawdown, 0.0)
        s = e.on_book(book(3.0, bid=89.9, ask=90.1))  # mid 90
        self.assertAlmostEqual(s.equity, 1000 - 501 + 450)  # 949
        self.assertAlmostEqual(s.drawdown, (1049 - 949) / 1049)

    def test_stale_flag(self):
        e = StateEngine(StateEngineConfig(max_gap_s=2.0))
        self.assertFalse(e.on_book(book(1.0)).stale)
        self.assertFalse(e.on_book(book(2.5)).stale)
        s = e.on_book(book(10.0))
        self.assertTrue(s.stale)
        self.assertAlmostEqual(s.gap_s, 7.5)

    def test_vol_window_expires_old_returns(self):
        e = StateEngine(StateEngineConfig(vol_window_s=10.0))
        e.on_book(book(0.0))
        e.on_book(book(1.0, bid=110, ask=110.2))
        s = e.on_book(book(20.0, bid=110, ask=110.2))
        self.assertEqual(s.vol_obs, 1)  # the 1.0s return expired; only the zero return at 20.0 remains
        self.assertEqual(s.realized_vol, 0.0)

    def test_compact_is_small(self):
        s = StateEngine().on_book(book(1.0))
        text = s.to_compact()
        self.assertLess(len(text), 400)  # chars, well under the 400-token budget
        self.assertNotIn("\n", text)


if __name__ == "__main__":
    unittest.main()
