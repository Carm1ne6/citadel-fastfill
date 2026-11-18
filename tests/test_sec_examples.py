"""The SEC order's own examples, rebuilt with synthetic books (prices in dollars, sizes in shares).

    python -X utf8 -m pytest tests -q        (or: python -X utf8 -m unittest discover tests)

Venues A, B, C stand for three exchanges. Every test states the paragraph it reproduces.
"""
import gzip
import os
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from citadel.ces import CES  # noqa: E402
from citadel.depth import Market  # noqa: E402
from citadel.params import Params  # noqa: E402
from citadel.venues import Coinbase, Venue, iter_records, kraken_checksum  # noqa: E402

MS = 1_000_000          # one millisecond in ns
S = 1_000_000_000       # one second in ns


class FakeTape:
    cur_mid = None

    def mid_at(self, m):
        return None

    def print_rate(self, venue, m):
        return 0.0


def venue(name, bids=(), asks=()):
    v = Venue()
    v.name = name
    v.bids.load(bids)
    v.asks.load(asks)
    v.valid = True
    return v


def engine(vens, **kw):
    p = dict(venues=tuple(vens), fastfill_feeds=tuple(vens), smartprovide_feeds=("B",), sp_post_venue="B",
             preset="sip", sip_latency_ms=5.0, round_lot_btc=100, min_order_btc=1, tick_usd=0.01,
             venue_ticks=(("A", 0.01), ("B", 0.01), ("C", 0.01)), trade_out_latency_ms=1.0,
             basis_adjust=False, clip_crossed=False, tox_min_train=10 ** 9, sp_small_btc=100, sp_large_btc=1000)
    p.update(kw)
    params = Params(**p)
    m = Market(vens, ref=None, clip=False)
    return CES(params, m, FakeTape(), record=True)


def sip_publish(e, t, quotes):
    """Give the SIP each venue's round-lot quote at time t and let the SIP latency pass."""
    for name, q in quotes.items():
        e.on_venue_update(t, name, q)
    e.advance(t + int(e.p.sip_latency_ms * MS))
    return t + int(e.p.sip_latency_ms * MS)


def rules(e):
    return [ev["rule"] for ev in e.events]


class FastFill(unittest.TestCase):
    def test_para_11_buy_filled_at_sip_offer(self):
        """Para 11: buy, SIP best offer $10.01, depth-of-book best offer $10.00 -> internalized at $10.01."""
        vens = {"A": venue("A", bids=[(9.99, 500)], asks=[(10.01, 500)]),
                "B": venue("B", bids=[(9.99, 300)], asks=[(10.00, 50), (10.02, 400)])}
        e = engine(vens)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400)})
        self.assertEqual(e.sip.nbo, 10.01)
        e.on_order(t + MS, "A", "BUY", 10.01, 40)
        ev = e.events[0]
        self.assertEqual(ev["rule"], "FASTFILL")
        self.assertAlmostEqual(ev["fill_px"], 10.01)
        self.assertAlmostEqual(ev["sip_px"], 10.01)
        self.assertAlmostEqual(ev["depth_px"], 10.00)
        self.assertAlmostEqual(ev["gap_top_usd"], 0.01)
        self.assertAlmostEqual(ev["gap_usd"], 0.01 * 40)        # 40 shares could have had $10.00
        self.assertTrue(ev["odd_lot"])                           # para 29: the $10.00 quote is an odd lot (50 < 100)

    def test_para_29_round_lot_trigger_is_not_odd_lot(self):
        """Para 22 + 29: the direct feed shows a round lot at $10.00 the SIP has not published yet (latency)."""
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.02, 400)], bids=[(9.99, 300)])}
        e = engine(vens)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400)})
        vens["B"].asks.set(10.00, 200)                           # a new round-lot offer, not yet on the SIP
        e.on_venue_update(t, "B", (9.99, 300, 10.00, 200))       # reaches the SIP only 5 ms later
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        ev = e.events[0]
        self.assertEqual(ev["rule"], "FASTFILL")
        self.assertAlmostEqual(ev["fill_px"], 10.01)
        self.assertFalse(ev["odd_lot"])
        e.advance(t + 6 * MS)
        self.assertEqual(e.sip.nbo, 10.00)                       # published after the latency, no look-ahead

    def test_para_32_entire_quantity_at_sip_regardless_of_size(self):
        """Para 32: FastFill filled the entire unexecuted portion at the SIP price regardless of displayed sizes;
        larger orders fared better than walking the displayed book, smaller ones worse."""
        vens = {"A": venue("A", asks=[(10.01, 100), (10.05, 1000)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.00, 50)], bids=[(9.98, 300)])}
        e = engine(vens)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 100), "B": (9.98, 300, None, 0.0)})
        e.on_order(t + MS, "A", "BUY", 10.01, 500)               # large: 500 shares vs 150 displayed below 10.05
        e.on_order(t + 2 * MS, "A", "BUY", 10.01, 20)            # small: fits in the $10.00 odd lot
        big, small = e.events[0], e.events[1]
        self.assertEqual(big["size"], 500)
        self.assertAlmostEqual(big["fill_px"], 10.01)
        self.assertFalse(big["displayed_better"])                # walking the book: 50@10.00, 100@10.01, 350@10.05
        self.assertTrue(small["displayed_better"])               # 20 shares were available at $10.00
        self.assertEqual(e.st["ff_small_worse"], 1)

    def test_fn_11_price_improvement(self):
        """Fn 11: NBO $10.00 -> CES might execute the buy at $9.999 (price improvement, para 27)."""
        vens = {"A": venue("A", asks=[(10.00, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(9.99, 10)], bids=[(9.98, 300)])}
        e = engine(vens, price_improvement_usd=0.001)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.00, 500), "B": (9.98, 300, None, 0.0)})
        e.on_order(t + MS, "A", "BUY", 10.00, 100)
        self.assertAlmostEqual(e.events[0]["fill_px"], 9.999)


class TradeOut(unittest.TestCase):
    def _setup(self, qty_at_best):
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.00, qty_at_best)], bids=[(9.98, 300)])}
        e = engine(vens)
        e.tox.quotient = lambda x: 0.99                          # likely toxic -> no principal interest (para 26)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.98, 300, None, 0.0)})
        return vens, e, t

    def test_fn_13_trade_out_at_better_depth_price(self):
        """Para 31 + fn 13: no principal interest -> FastFill internalizes at the SIP and sends a proprietary order
        at the better depth-of-book price; eligible when it is no greater than 90% of the best quantity."""
        vens, e, t = self._setup(qty_at_best=100)
        e.on_order(t + MS, "A", "BUY", 10.01, 90)                # 90 <= 0.9 x 100
        e.advance(t + 3 * MS)
        e.on_book(t + 3 * MS, "B")                               # B's first book message after the 1 ms delay
        self.assertEqual(rules(e), ["FASTFILL", "TRADE_OUT"])
        to = e.events[1]
        self.assertAlmostEqual(to["px"], 10.00)
        self.assertEqual(to["venue"], "B")
        self.assertAlmostEqual(to["filled"], 90)                 # the level was still there 1 ms later
        self.assertFalse(e.events[0]["principal_interest"])

    def test_fn_13_ninety_percent_rule(self):
        """Fn 13: a proprietary order above 90% of the displayed best quantity is not eligible -> no FastFill."""
        vens, e, t = self._setup(qty_at_best=100)
        e.on_order(t + MS, "A", "BUY", 10.01, 95)                # 95 > 0.9 x 100
        e.advance(t + 3 * MS)
        self.assertEqual(rules(e), [])
        self.assertEqual(e.st["ff_ineligible_trade_out"], 1)

    def test_fn_13_no_fill_when_level_gone(self):
        """Fn 13: about 45% of trade out attempts got no fill; here the level is gone before the order arrives."""
        vens, e, t = self._setup(qty_at_best=100)
        e.on_order(t + MS, "A", "BUY", 10.01, 50)
        vens["B"].asks.set(10.00, 0)                             # someone else took the $10.00 offer
        e.advance(t + 3 * MS)
        e.on_book(t + 3 * MS, "B")
        self.assertEqual(e.events[1]["filled"], 0)
        self.assertIn("NO FILL", e.events[1]["outcome"])


class SmartProvide(unittest.TestCase):
    def _setup(self):
        # A sets the SIP NBO at $10.01; B (the only feed SmartProvide reads) shows $10.02
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.02, 400)], bids=[(9.99, 300)])}
        e = engine(vens)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400)})
        return vens, e, t

    def test_para_13_37_display_bid_below_sip_offer(self):
        """Para 13, 37: buy, SIP NBO $10.01, referenced feed $10.02 -> display a buy order at $10.00 for up to 1 to 5
        seconds."""
        vens, e, t = self._setup()
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        ev = e.events[0]
        self.assertEqual(ev["rule"], "SMARTPROVIDE")
        self.assertAlmostEqual(ev["sip_px"], 10.01)
        self.assertAlmostEqual(ev["depth_px"], 10.02)
        self.assertAlmostEqual(ev["post_px"], 10.00)
        self.assertAlmostEqual(ev["window_s"], 1.0)              # small order: one second (para 38)

    def test_para_35_triggers_although_another_feed_shows_sip_price(self):
        """Para 34-35: SmartProvide triggers on its one feed ($10.02) although exchange A's feed shows $10.01."""
        vens, e, t = self._setup()
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        self.assertEqual(rules(e), ["SMARTPROVIDE"])
        self.assertAlmostEqual(e.market.best("ask", ("A",))[0], 10.01)

    def test_para_37_fill_at_better_price(self):
        """Para 37: if the displayed order is executed at $10.00, the customer gets the better price."""
        vens, e, t = self._setup()
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        e.on_print(t + 500 * MS, "B", "SELL", 10.00, 100)       # a seller hits the displayed bid on B
        self.assertIn("SP_FILLED", rules(e))
        f = [x for x in e.events if x["rule"] == "SP_FILLED"][0]
        self.assertAlmostEqual(f["fill_px"], 10.00)
        self.assertAlmostEqual(e.st["sp_filled_btc"], 100)

    def test_para_39_reevaluation_worse_than_immediate(self):
        """Para 39: unfilled shares are re-evaluated at window end; internalizing then can be worse than at once."""
        vens, e, t = self._setup()
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        vens["A"].asks.set(10.01, 0)
        vens["A"].asks.set(10.03, 500)                           # the market moves up while the bid is displayed
        e.on_venue_update(t + 100 * MS, "A", (9.99, 500, 10.03, 500))
        e.advance(t + 2 * S)
        rv = [x for x in e.events if x["rule"] == "SP_REEVAL"][0]
        self.assertAlmostEqual(rv["fill_px"], 10.02)             # NBO is now B's $10.02
        self.assertEqual(e.st["sp_worse_than_immediate"], 1)

    def test_para_30_fastfill_takes_over_smartprovide(self):
        """Para 30: the FastFill trigger arises while SmartProvide handles the order -> FastFill immediately
        internalizes the remaining quantity at the SIP price."""
        vens, e, t = self._setup()
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        vens["C"] = venue("C", asks=[(10.00, 30)], bids=[(9.98, 100)])   # an odd lot better than the SIP appears
        e.p.fastfill_feeds = ("A", "B", "C")
        e.on_venue_update(t + 200 * MS, "C", (9.98, 100, None, 0.0))
        self.assertEqual(rules(e), ["SMARTPROVIDE", "FASTFILL"])
        ff = e.events[1]
        self.assertEqual(ff["phase"], "during_sp")
        self.assertAlmostEqual(ff["fill_px"], 10.01)
        self.assertEqual(ff["size"], 100)
        self.assertNotIn(e.events[0]["id"], [o.id for o in e.active_sp])


class Feeds(unittest.TestCase):
    def test_coinbase_match_side_is_maker(self):
        cb = Coinbase()
        _, trades = cb.on_msg({"type": "match", "product_id": "BTC-USD", "side": "sell", "price": "100.5",
                               "size": "0.2"})
        self.assertEqual(trades[0][:3], ("BUY", 100.5, 0.2))     # maker sold -> the taker (the order) bought

    def test_kraken_checksum_format(self):
        """Kraken v2: price and qty at pair precision, '.' and leading zeros removed, asks then bids, CRC32."""
        asks = [(64184.7, 0.5)]
        bids = [(64184.6, 0.00006120)]
        s = "641847" + "50000000" + "641846" + "6120"
        self.assertEqual(kraken_checksum(asks, bids), zlib.crc32(s.encode()) & 0xFFFFFFFF)

    def test_truncated_gzip_stops_cleanly(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.jsonl.gz")
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            for i in range(2000):
                fh.write('{"t": %d, "m": %d, "raw": "{}"}\n' % (i, i))
        data = open(p, "rb").read()
        open(p, "wb").write(data[: len(data) - 40])             # no trailer, last block cut
        recs = list(iter_records(p))
        self.assertGreater(len(recs), 0)
        self.assertLessEqual(len(recs), 2000)


if __name__ == "__main__":
    unittest.main()
