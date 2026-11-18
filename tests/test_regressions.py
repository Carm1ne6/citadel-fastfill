"""Regression tests: each class pins one behaviour of the engine, the report or the film data; RULES.md and NOTES.md
describe the rule it checks (NOTES.md, "Behaviours the regression tests pin")."""
import json
import os
import random
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import export_film  # noqa: E402
from citadel import ces as ces_mod  # noqa: E402
from citadel import toxicity as tox_mod  # noqa: E402
from citadel.depth import Market  # noqa: E402
from citadel.params import Params  # noqa: E402
from citadel.pipeline import run_all  # noqa: E402
from citadel.report import engine_stats, half_stats  # noqa: E402
from citadel.sim import Session  # noqa: E402
from citadel.toxicity import FEATURE_NAMES, single_feature_auc  # noqa: E402
from test_sec_examples import MS, S, engine, rules, sip_publish, venue  # noqa: E402

PROBE = os.path.join(ROOT, "data", "probe")


# ------------------------------------------------------------------------ the crossed-quote clip (RULES.md section 2)
class CrossedQuoteClip(unittest.TestCase):
    """A level of another venue that locks or crosses the reference venue's best OPPOSITE quote is not counted:
    its bids must stay below the reference ask, its asks above the reference bid."""

    def _market(self, bids=(), asks=()):
        ref = venue("R", bids=[(100.00, 1.0)], asks=[(100.10, 1.0)])
        other = venue("O", bids=list(bids), asks=list(asks))
        return Market({"R": ref, "O": other}, ref="R", clip=True), other

    def test_bids(self):
        for bid, kept in ((100.05, True), (100.00, True), (100.10, False), (100.20, False)):
            m, o = self._market(bids=[(bid, 1.0)])
            self.assertEqual(m.vbest(o, "bid")[0] is not None, kept, bid)

    def test_asks(self):
        for ask, kept in ((100.05, True), (100.10, True), (100.00, False), (99.90, False)):
            m, o = self._market(asks=[(ask, 1.0)])
            self.assertEqual(m.vbest(o, "ask")[0] is not None, kept, ask)

    def test_another_venue_can_set_or_join_the_best_price(self):
        m, _ = self._market(bids=[(100.05, 2.0)], asks=[(100.10, 3.0)])
        self.assertEqual(m.best("bid", ("R", "O"))[0], 100.05)                     # inside the reference spread
        px, size, at = m.best("ask", ("R", "O"))
        self.assertEqual((px, size), (100.10, 4.0))                                 # ties the reference ask
        self.assertEqual(sorted(v for v, _ in at), ["O", "R"])


# ------------------------------------------------------------- trade outs read on the venue's next book (fn 13)
class TradeOutReadOnTheVenuesNextBook(unittest.TestCase):
    """The trade out reaches its venue TRADE_OUT_LATENCY_MS after it is sent and is read on the first book message of
    that venue received from then on, not on whatever book was last seen."""

    def _setup(self):
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.00, 100)], bids=[(9.98, 300)])}
        e = engine(vens)                                          # trade_out_latency_ms = 1
        e.tox.quotient = lambda x: 0.99                           # no principal interest -> trade out (SEC 31)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.98, 300, None, 0.0)})
        e.on_order(t + MS, "A", "BUY", 10.01, 50)
        return vens, e, t

    def test_waits_for_a_book_message_of_its_venue(self):
        vens, e, t = self._setup()
        e.on_book(t + MS + MS // 2, "B")                          # before the order reaches B
        e.advance(t + 10 * MS)
        e.on_book(t + 10 * MS, "A")                               # another venue's book says nothing about B
        self.assertEqual(rules(e), ["FASTFILL"])
        vens["B"].asks.set(10.00, 0)                              # the level goes before B's next book message
        e.on_book(t + 20 * MS, "B")
        to = e.events[-1]
        self.assertEqual(to["rule"], "TRADE_OUT")
        self.assertIn("NO FILL", to["outcome"])
        self.assertAlmostEqual(to["resolved_ms"], 19.0)

    def test_read_once_on_the_first_book_after_the_delay(self):
        vens, e, t = self._setup()
        e.on_book(t + 20 * MS, "B")
        self.assertEqual(e.events[-1]["filled"], 50)
        vens["B"].asks.set(10.00, 0)
        e.on_book(t + 30 * MS, "B")
        self.assertEqual(rules(e), ["FASTFILL", "TRADE_OUT"])


# ------------------------------------------------------------------------------------------ raw venue prices
class RawVenuePrices(unittest.TestCase):
    """The trade-out and SmartProvide checks compare the venue's own prices fixed when the order was sent or
    displayed; a basis that moves meanwhile must not change the outcome."""

    def test_trade_out_check_uses_the_raw_price_sent(self):
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.00, 100)], bids=[(9.98, 300)])}
        e = engine(vens)
        e.tox.quotient = lambda x: 0.99
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.98, 300, None, 0.0)})
        e.on_order(t + MS, "A", "BUY", 10.01, 50)                 # trade out at B's raw $10.00
        vens["B"].basis = -0.05                                   # B's basis moves before the check
        e.on_book(t + 3 * MS, "B")
        self.assertEqual(e.events[-1]["filled"], 50)              # B still shows $10.00: filled

    def test_smartprovide_fill_uses_the_raw_displayed_price(self):
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.02, 400)], bids=[(9.99, 300)])}
        e = engine(vens)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400)})
        e.on_order(t + MS, "A", "BUY", 10.01, 100)                # bid displayed on B at raw $10.00
        vens["B"].basis = -0.05
        e.on_print(t + 500 * MS, "B", "SELL", 10.00, 100)         # a seller hits it at B's raw $10.00
        f = [x for x in e.events if x["rule"] == "SP_FILLED"]
        self.assertEqual(len(f), 1)
        self.assertAlmostEqual(f[0]["fill_px"], 10.00)            # the customer gets the displayed price


# ---------------------------------------------------------------------- taker orders (RULES.md section 3)
def _cb(t, msg):
    return {"t": t, "m": t, "raw": json.dumps(msg)}


def _match(taker, size, price="100.01", side="sell"):
    return {"type": "match", "product_id": "BTC-USD", "side": side, "price": price, "size": size,
            "taker_order_id": taker, "maker_order_id": "M"}


SNAP = {"type": "snapshot", "product_id": "BTC-USD", "bids": [["100.00", "1.0"]], "asks": [["100.01", "1.0"]]}


def _session(**kw):
    p = dict(venues=("coinbase", "kraken"), preset="latency", warmup_s=0.0)
    p.update(kw)
    return Session([Params(**p)], ["coinbase", "kraken"])


class TakerOrders(unittest.TestCase):
    """One marketable order per public taker order: its fills on one venue are grouped, its size is their sum, and
    the dust floor applies to the sum."""

    def test_fills_of_one_taker_are_one_order(self):
        ses = _session()
        ses.feed(0, "coinbase", _cb(0, SNAP))
        ses.feed(10 * MS, "coinbase", _cb(10 * MS, _match("T1", "0.0003")))
        ses.feed(10 * MS, "coinbase", _cb(10 * MS, _match("T1", "0.0003")))     # same taker: one order of 0.0006
        ses.feed(11 * MS, "coinbase", _cb(11 * MS, _match("T2", "0.002")))
        ses.feed(12 * MS, "coinbase", _cb(12 * MS, {"type": "heartbeat"}))
        ses.feed(20 * MS, "coinbase", _cb(20 * MS, _match("T3", "0.0002")))
        ses.feed(20 * MS, "coinbase", _cb(20 * MS, _match("T3", "0.0002")))     # 0.0004 together: still dust
        ses.finish()
        st = ses.head.st
        self.assertEqual(ses.n_fills, 5)
        self.assertEqual(ses.n_takers, 3)
        self.assertEqual(st["orders"], 2)
        self.assertEqual(st["orders_fills"], 3)
        self.assertAlmostEqual(st["orders_btc"], 0.0026)
        self.assertEqual(st["prints_dust"], 1)

    def test_one_message_can_carry_several_taker_orders(self):
        """Kraken publishes no order ids: fills of one taker share the side and the exchange timestamp."""
        ses = _session()
        ses.feed(0, "coinbase", _cb(0, SNAP))
        ses.feed(0, "kraken", _cb(0, {"channel": "book", "type": "snapshot", "data": [
            {"symbol": "BTC/USD", "bids": [{"price": 100.0, "qty": 1.0}], "asks": [{"price": 100.1, "qty": 1.0}]}]}))
        ts = "2026-11-18T03:14:18.000001Z"
        ses.feed(10 * MS, "kraken", _cb(10 * MS, {"channel": "trade", "type": "update", "data": [
            {"symbol": "BTC/USD", "side": "buy", "price": 100.1, "qty": 0.01, "timestamp": ts},
            {"symbol": "BTC/USD", "side": "buy", "price": 100.1, "qty": 0.02, "timestamp": ts},
            {"symbol": "BTC/USD", "side": "sell", "price": 100.0, "qty": 0.01, "timestamp": ts}]}))
        ses.finish()
        self.assertEqual(ses.n_fills, 3)
        self.assertEqual(ses.head.st["orders"], 2)
        self.assertAlmostEqual(ses.head.st["orders_btc"], 0.04)

    def test_an_order_is_handled_before_the_next_message_is_applied(self):
        """The order sees the books as they were at its last fill: a book message after it is applied later."""
        ses = _session()
        ses.feed(0, "coinbase", _cb(0, SNAP))
        ses.feed(10 * MS, "coinbase", _cb(10 * MS, _match("T1", "0.01")))
        seen = []
        orig = ses.head._new_order

        def spy(m, venue, side, px, size, n_fills=1):
            seen.append(ses.vens["coinbase"].asks.best()[0])
            return orig(m, venue, side, px, size, n_fills)
        ses.head._new_order = spy
        ses.feed(11 * MS, "coinbase", _cb(11 * MS, {"type": "l2update", "product_id": "BTC-USD",
                                                    "changes": [["sell", "100.01", "0"], ["sell", "100.05", "1"]]}))
        self.assertEqual(seen, [100.01])


# ------------------------------------------------------------------------------------------------- the warm-up
class WarmUp(unittest.TestCase):
    def test_no_order_before_one_basis_time_constant(self):
        ses = _session(warmup_s=1.0)
        ses.feed(0, "coinbase", _cb(0, SNAP))
        ses.feed(S // 2, "coinbase", _cb(S // 2, _match("T1", "0.01")))
        ses.feed(S // 2 + MS, "coinbase", _cb(S // 2 + MS, {"type": "heartbeat"}))
        ses.feed(3 * S // 2, "coinbase", _cb(3 * S // 2, _match("T2", "0.01")))
        ses.finish()
        self.assertEqual(ses.head.st["orders_warmup"], 1)
        self.assertEqual(ses.head.st["orders"], 1)

    def test_default_is_the_basis_time_constant(self):
        self.assertEqual(Params().warmup_s, Params().basis_tau_s)


# ------------------------------------------------------------------------------------------ the end of the data
class DataEnd(unittest.TestCase):
    def _sp(self):
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.02, 400)], bids=[(9.99, 300)])}
        e = engine(vens)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400)})
        e.on_order(t + MS, "A", "BUY", 10.01, 100)                # a 1 s display window
        return e, t

    def test_finish_decides_open_windows_at_the_last_message(self):
        e, t = self._sp()
        e.finish(t + 100 * MS)
        self.assertEqual(rules(e), ["SMARTPROVIDE", "SP_REEVAL"])
        self.assertTrue(all(ev["m"] <= t + 100 * MS for ev in e.events))
        self.assertEqual(e.st["sp_cut_by_data_end"], 1)

    def test_reevaluation_wording(self):
        """An unchanged price reads "same as"."""
        e, t = self._sp()
        e.advance(t + 2 * S)
        out = e.events[-1]["outcome"]
        self.assertTrue(out.endswith(", same as 10.01 at arrival"), out)
        self.assertNotIn("than", out)


# ---------------------------------------------------------------------------------------- toxicity baselines
class ToxicityBaselines(unittest.TestCase):
    def test_single_parameter_auc_takes_the_direction_from_the_first_half(self):
        rnd = random.Random(7)

        def data(n):
            X, y = [], []
            for _ in range(n):
                x = [rnd.gauss(0, 1) for _ in FEATURE_NAMES]
                y.append(1 if rnd.random() < 1 / (1 + pow(2.718281828, 3 * x[5])) else 0)   # toxic when x5 is LOW
                X.append(x)
            return X, y
        X1, y1 = data(1500)
        X2, y2 = data(1500)
        out = single_feature_auc(X1, y1, X2, y2)
        self.assertEqual(out[0][0], FEATURE_NAMES[5])
        self.assertGreater(out[0][1], 0.8)                         # flipped: a low value predicts a loss
        self.assertTrue(all(0.0 <= r[1] <= 1.0 for r in out))


# --------------------------------------------------- the attribution of the patent link, the feed, the threshold
class Attribution(unittest.TestCase):
    def test_the_patent_link_is_marked_ours(self):
        """The SEC order does not say how CES decided principal interest; the patent link is ours."""
        doc = " ".join(tox_mod.__doc__.split())
        self.assertIn("OURS: neither the SEC order nor the patent links them", doc)
        self.assertIn("How this rebuild uses the response (OURS", doc)
        item1 = " ".join(ces_mod.__doc__.split("2. FastFill")[0].split())
        self.assertIn("OURS", item1)
        self.assertIn("our assumption", item1)

    def test_smartprovide_reads_coinbase_as_spec(self):
        """SPEC fixes SMARTPROVIDE_FEEDS = coinbase; Kraken's $0.10 grid makes its trigger a standing condition."""
        self.assertEqual(Params().smartprovide_feeds, ("coinbase",))
        self.assertEqual(Params().sp_post_venue, "coinbase")

    def test_threshold_is_not_the_base_rate(self):
        """col. 9:1-5 expects a level of toxic trades from random arrival; 0.5 would screen half the flow."""
        self.assertEqual(Params().tox_threshold, 0.7)


class SharedMarket(unittest.TestCase):
    def test_a_sensitivity_engine_cannot_change_the_shared_market(self):
        """Every engine of a session reads the same books, basis and crossed-quote rule."""
        with self.assertRaises(ValueError):
            Session([Params(), Params(clip_crossed=False)], ["coinbase"])
        with self.assertRaises(ValueError):
            Session([Params(), Params(basis_tau_s=10.0)], ["coinbase"])
        Session([Params(), Params(preset="latency", tox_threshold=0.5)], ["coinbase"])   # engine settings may differ


class DisplayGrid(unittest.TestCase):
    def test_kraken_grid_display_is_rounded_away_from_the_sip(self):
        """One cent inside the SIP, then down (buy) onto the display venue's grid: on $0.10, 1 to 10 cents."""
        vens = {"A": venue("A", asks=[(10.07, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.10, 400)], bids=[(9.90, 300)])}
        e = engine(vens, venue_ticks=(("A", 0.01), ("B", 0.1)))
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.07, 500), "B": (9.90, 300, 10.10, 400)})
        e.on_order(t + MS, "A", "BUY", 10.07, 100)
        sp = e.events[0]
        self.assertEqual(sp["rule"], "SMARTPROVIDE")
        self.assertAlmostEqual(sp["post_px"], 10.00)
        self.assertTrue(0.01 - 1e-9 <= sp["sip_px"] - sp["post_px"] <= 0.10 + 1e-9)


class SecObservations(unittest.TestCase):
    """SEC 30 (price improvement often short of the trigger gap) and SEC 38 (SmartProvide delayed execution)."""

    def _ff(self, pi):
        vens = {"A": venue("A", bids=[(9.99, 500)], asks=[(10.01, 500)]),
                "B": venue("B", bids=[(9.99, 300)], asks=[(10.00, 50), (10.02, 400)])}
        e = engine(vens, price_improvement_usd=pi)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400)})
        e.on_order(t + MS, "A", "BUY", 10.01, 40)
        return e

    def test_price_improvement_short_of_the_gap(self):
        self.assertEqual(self._ff(0.0).st.get("ff_pi_short", 0), 1)      # $0 < the $0.01 gap
        self.assertEqual(self._ff(0.02).st.get("ff_pi_short", 0), 0)     # $0.02 covers it

    def test_smartprovide_wait(self):
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.02, 400)], bids=[(9.99, 300)])}
        e = engine(vens)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400)})
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        e.on_print(t + 500 * MS, "B", "SELL", 10.00, 100)
        self.assertEqual(e.sp_delay_ms, [499.0])
        self.assertEqual(engine_stats(e)["smartprovide"]["delay_ms_median"], 499.0)


class Halves(unittest.TestCase):
    def test_trade_out_share_and_no_principal_interest_by_half(self):
        class E:
            pi_log = [(1, True), (2, False), (11, True), (12, True)]
            ff_log = [(1, 1.0, False), (2, 0.1, True), (11, 2.0, False)]
        h = half_stats(E, 10)
        self.assertEqual(h["first_half"]["no_principal_interest_pct"], 50.0)
        self.assertAlmostEqual(h["first_half"]["trade_out_share_pct"], round(100 * 0.1 / 1.1, 2))
        self.assertEqual(h["second_half"]["trade_out_share_pct"], 0.0)
        self.assertEqual(h["second_half"]["orders"], 2)


# ------------------------------------------------------------- the report and the film data on the probe recording
@unittest.skipUnless(os.path.isdir(PROBE), "no data/probe recording")
class ReportOnProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.R = run_all(PROBE, base=Params(warmup_s=0.0), grid=(0.5, 5.0))
        cls.lines = cls.R["report"]
        cls.text = "\n".join(cls.lines)

    def test_report_lines_fit(self):
        self.assertTrue(all(len(x) <= 96 for x in self.lines))
        self.assertFalse(any("—" in x for x in self.lines))

    def test_shares_are_labelled_not_comparable(self):
        """The SEC's shares are of CES flow or of the orders FastFill / SmartProvide handled, after eligibility
        conditions that are not public: never comparable with ours."""
        self.assertIn("FastFill share of orders (simulated)", self.text)
        self.assertIn("0.4% of all CES flow", self.text)
        self.assertIn("not comparable", self.text)

    def test_both_presets_feeds_thresholds_and_baselines_are_printed(self):
        """Both presets, both SmartProvide feeds, the threshold grid with the SEC 31 halves, the trade-out reading,
        the clock note and the share of the orders without a FastFill trigger are printed."""
        for s in ("both presets at 5 ms", "SmartProvide feed (5 ms)", "toxicity threshold ->",
                  "first book message after", "of the orders without a FastFill trigger",
                  "(below the ~1 ms clock of a Windows recorder)", "trade outs by half (SEC 31"):
            self.assertIn(s, self.text)
        st = self.R["stats"]
        self.assertEqual(st["other_preset"]["preset"], "latency")
        self.assertEqual(sorted({(s["preset"], s["smartprovide_feed"]) for s in st["sensitivity_feed"]}),
                         [("latency", "coinbase"), ("latency", "kraken"), ("sip", "coinbase"), ("sip", "kraken")])
        self.assertEqual([s["tox_threshold"] for s in st["sensitivity_tox"]], [0.5, 0.6, 0.7, 0.8, 0.9])

    def test_the_films_report_panel_reads_the_sec_and_run_cells(self):
        """A reader that splits a report row at runs of two or more spaces (the film's report panel does) finds the
        SEC's number and this run's in the last two cells."""
        st = self.R["stats"]
        want = {"FastFill orders": f"{st['fastfill']['count']:,}",
                "FastFill triggered by an odd lot": f"{st['fastfill']['odd_lot_pct']:.1f}%",
                "trade out filled in full or part": f"{st['trade_out']['success_pct']:.1f}%",
                "SmartProvide orders (33)": f"{st['smartprovide']['count']:,}"}
        for pre, run in want.items():
            line = next(x for x in self.lines if x.strip().startswith(pre))
            parts = re.split(r"\s{2,}", line.strip())
            self.assertEqual(parts[-1], run, line)

    def test_fan_counts_what_it_leaves_out(self):
        """n_total counts only fills whose 30 s path fits; the rest are counted, not hidden."""
        film = export_film.build(self.R)
        f = film["fan"]
        self.assertEqual(f["n_total"] + f["n_excluded_end"], f["n_fastfill"])
        self.assertEqual(f["n_fastfill"], self.R["stats"]["fastfill"]["count"])

    def test_orders_t_counts_every_order(self):
        """The film counts orders from orders_t, not from the largest event id (orders without an event)."""
        import check_film
        film = export_film.build(self.R)
        self.assertEqual(len(film["orders_t"]), self.R["stats"]["orders"])
        err, _ = check_film.check(film)
        self.assertEqual(err, [])


if __name__ == "__main__":
    unittest.main()
