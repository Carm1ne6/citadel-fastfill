"""The toxicity analyzer and profit analyzer of US 7,587,347 B2, step by step (citations: column:line, claims)."""
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from citadel.params import Params  # noqa: E402
from citadel.toxicity import (ANOTHER_MARKET_CENTER, BUY, EXECUTE, EXECUTION, FEATURE_NAMES, MARKET,  # noqa: E402
                              NOT_AVAILABLE, ORDER, PARAMETERS, SELL, ProfitAnalyzer, ToxicityAnalyzer, auc,
                              correlations, dealer_pnl, evaluate, fit_logistic, is_toxic, measure, predict)

S = 1_000_000_000

# The patent's three lists (col. 9:38-51) and claim 6.
PATENT_ORDER = ["the identity of the equity", "the action type", "the order type", "the limit price",
                "the order size"]
PATENT_MARKET = ["the theoretical value of the equity", "the frequency of order submission",
                 "the NBBO market source", "the NBBO size", "the liquidity of the equity in the market",
                 "the implied spread (NBBO)", "equity-market performance correlation"]
PATENT_EXECUTION = ["the fill spread", "the fill price"]


class Definitions(unittest.TestCase):
    def test_toxic_means_no_profit_or_a_loss(self):
        """col. 4:1-3: toxic orders "would result in no profit, reduced profit and/or a loss"."""
        self.assertTrue(is_toxic(0.0))
        self.assertTrue(is_toxic(-0.01))
        self.assertFalse(is_toxic(0.01))

    def test_dealer_profit_comes_from_the_spread(self):
        """col. 3:9-13: internalizing, the profit "is based on the spread between bid and offer prices"."""
        self.assertAlmostEqual(dealer_pnl(BUY, 10.01, 10.00), 0.01)      # sold to a buyer at the offer
        self.assertAlmostEqual(dealer_pnl(SELL, 9.99, 10.00), 0.01)      # bought from a seller at the bid

    def test_every_listed_parameter_is_measured_or_explained(self):
        """Each parameter the patent lists is either measured or named in NOT_AVAILABLE with the reason."""
        words = {ORDER: [], MARKET: [], EXECUTION: []}
        for _, kind, w, _how in PARAMETERS:
            if kind in words:
                words[kind].append(w)
        missing = [w for w, _k, _r in NOT_AVAILABLE]
        for kind, listed in ((ORDER, PATENT_ORDER), (MARKET, PATENT_MARKET), (EXECUTION, PATENT_EXECUTION)):
            for item in listed:
                hit = any(w.startswith(item) for w in words[kind]) or any(m.startswith(item) for m in missing)
                self.assertTrue(hit, f"{kind}: {item}")
        self.assertIn("log_size_vs_nbbo", FEATURE_NAMES)                    # claim 6
        self.assertEqual(len(FEATURE_NAMES), len(set(FEATURE_NAMES)))


class Measure(unittest.TestCase):
    def _x(self, side, print_px, sip, fill):
        return measure(side, 0.1, print_px, sip, fill, 99.99, 100.01, 1.0, ("coinbase",), 99.995, 2.0, 100.005, 1.0,
                       5.0, 3.0, 100.0)

    def test_buy_and_sell_mirror_each_other(self):
        b = self._x(BUY, 100.02, 100.01, 100.01)
        s = self._x(SELL, 99.98, 99.99, 99.99)
        n = FEATURE_NAMES.index
        self.assertEqual(b[n("side_buy")], 1.0)
        self.assertEqual(s[n("side_buy")], -1.0)
        self.assertAlmostEqual(b[n("limit_through_bps")], s[n("limit_through_bps")], places=6)
        self.assertAlmostEqual(b[n("fill_edge_bps")], s[n("fill_edge_bps")], places=6)
        self.assertGreater(b[n("fill_edge_bps")], 0)                        # dealer sells above the mid
        self.assertEqual(b[n("src_coinbase")], 1.0)
        self.assertEqual(len(b), len(FEATURE_NAMES))

    def test_execution_parameters_use_the_fill(self):
        """col. 9:49-51: "the fill spread (e.g., the bid/offer spread at the time of filling the order) and the fill
        price"; price improvement (SEC 27) moves the fill price, not the SIP price."""
        a = self._x(BUY, 100.02, 100.01, 100.01)
        b = self._x(BUY, 100.02, 100.01, 100.009)
        n = FEATURE_NAMES.index
        self.assertLess(b[n("fill_edge_bps")], a[n("fill_edge_bps")])
        self.assertEqual(b[n("limit_through_bps")], a[n("limit_through_bps")])


class Quotient(unittest.TestCase):
    def _data(self, n, seed):
        rnd = random.Random(seed)
        X, y = [], []
        for _ in range(n):
            x = [rnd.gauss(0, 1) for _ in FEATURE_NAMES]
            p = 1 / (1 + pow(2.718281828, -3 * x[3]))          # toxic when the theoretical value is above the SIP
            X.append(x)
            y.append(1 if rnd.random() < p else 0)
        return X, y

    def test_empirical_combination_separates_losing_from_profitable(self):
        """col. 9:57-67: combine the parameters empirically by correlating them with trade outcomes."""
        X, y = self._data(2000, 1)
        model = fit_logistic(X, y)
        X2, y2 = self._data(1000, 2)
        self.assertGreater(auc(predict(model, X2), y2), 0.8)
        self.assertGreater(model[0][1 + 3], 1.0)
        r = {c[0]: c[1] for c in correlations(X, y)}
        self.assertGreater(r[FEATURE_NAMES[3]], 0.3)
        self.assertLess(abs(r[FEATURE_NAMES[0]]), 0.1)

    def test_response(self):
        """FIG. 5 step 506: not toxic -> executed (step 508); likely toxic -> another market center (step 510)."""
        ta = ToxicityAnalyzer(Params(tox_threshold=0.5))
        self.assertEqual(ta.respond(None), EXECUTE)                 # a silent analyzer screens nothing
        self.assertEqual(ta.respond(0.2), EXECUTE)
        self.assertEqual(ta.respond(0.5), ANOTHER_MARKET_CENTER)

    def test_walk_forward_refit(self):
        """col. 10:15-16: quotients "may be dynamically modified based on events occurring after their initial
        generation": the model appears once enough outcomes are known and is refitted as more arrive."""
        ta = ToxicityAnalyzer(Params(tox_horizon_s=1.0, tox_min_train=50, tox_refit_s=5.0))
        X, y = self._data(400, 3)
        for i, (x, lab) in enumerate(zip(X, y)):
            m = i * S // 10
            ta.observe(m, x, BUY, 100.0, 1.0)
            ta.on_time(m, None)
        self.assertIsNone(ta.model)
        for i, lab in enumerate(y):                                   # outcomes become known one by one
            ta.on_time(i * S // 10 + S, 101.0 if lab else 99.0)
        self.assertIsNotNone(ta.model)
        self.assertGreater(ta.n_fits, 1)

    def test_offline_test_fits_on_the_first_half_only(self):
        rows = []
        X, y = self._data(400, 4)
        for i, (x, lab) in enumerate(zip(X, y)):
            rows.append((i * S, x, BUY, 100.0))
        mids = {i * S + S: (101.0 if lab else 99.0) for i, lab in enumerate(y)}
        res = evaluate(rows, lambda m: mids.get(m), t_split=200 * S, horizons=(1.0,))["horizons"]["1.0"]
        self.assertEqual(res["n_train"], 200)
        self.assertEqual(res["n_test"], 200)
        self.assertGreater(res["auc_oos"], 0.75)


class Profit(unittest.TestCase):
    def test_running_tab_splits_spread_and_later_price(self):
        """col. 14:3-5: "By logging the bid/offer spread at the time of order filling for each equity trade executed,
        a running tab of profit (or loss) can be kept." Sell 100 at the $10.01 offer (mid $10.00), mid later $10.03."""
        pa = ProfitAnalyzer(mark_s=5.0)
        pa.on_fill(0, "A", BUY, 10.01, 100, 9.99, 10.01)
        pa.on_time(5 * S, 10.03)
        self.assertAlmostEqual(pa.spread_usd, 1.00)
        self.assertAlmostEqual(pa.tab_usd, -2.00)
        self.assertAlmostEqual(pa.move_usd, -3.00)
        self.assertEqual(pa.n_losing, 1)

    def test_corrective_action_signal_below_expected_level(self):
        """col. 14:5-11 and claims 34-35: an expected level of profit per order flow; a period well below it signals
        the need for corrective action."""
        pa = ProfitAnalyzer(mark_s=1.0, period_s=10.0, z=2.0, min_trades=20)
        m = 0
        for period in range(4):
            for i in range(24):
                # three ordinary periods earn $0.009 / $0.011 a trade; the fourth loses $0.04 a trade
                mid = (10.001 if i % 2 else 9.999) if period < 3 else 10.05
                pa.on_fill(m, "A", BUY, 10.01, 1.0, 9.99, 10.01)
                pa.on_fill(m, "B", BUY, 10.01, 1.0, 9.99, 10.01)
                pa.on_time(m + S, mid)
                m += S // 4
            m = (period + 1) * 10 * S
        out = pa.summary()
        self.assertEqual(out["by_order_flow"]["A"]["corrective_action_signals"], 1)
        self.assertEqual(out["by_order_flow"]["A"]["periods"], 4)
        self.assertEqual(len(out["signals"]), 2)

    def test_dealer_own_trades_count_in_the_tab_but_not_in_any_flow(self):
        pa = ProfitAnalyzer(mark_s=1.0)
        pa.on_fill(0, "A", SELL, 10.00, 1.0, 9.99, 10.01, flow=False)
        pa.on_time(S, 10.02)
        out = pa.summary()
        self.assertAlmostEqual(out["tab_usd"], 0.02)
        self.assertEqual(out["by_order_flow"], {})


if __name__ == "__main__":
    unittest.main()
