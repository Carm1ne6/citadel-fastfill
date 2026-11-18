"""No look-ahead: every decision at time t uses only messages received by t (and the SIP only what it had published).

The strongest check is prefix invariance: replaying a recording cut at time T gives exactly the same events, the same
toxicity parameters and the same quotients before T as replaying the whole recording. A decision that peeked at a
later message would differ.
"""
import contextlib
import heapq
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from citadel.params import Params  # noqa: E402
from citadel.report import format_line  # noqa: E402
from citadel.sim import Tape, _stream, simulate  # noqa: E402
from citadel.sip import SipEmulator  # noqa: E402
from citadel.toxicity import BUY, ProfitAnalyzer, ToxicityAnalyzer  # noqa: E402

MS = 1_000_000
S = 1_000_000_000
PROBE = os.path.join(ROOT, "data", "probe")
DEV = os.path.join(ROOT, "data", "run_dev")


class Units(unittest.TestCase):
    def test_sip_publishes_only_after_its_latency(self):
        """SEC 22, fn 9: a quote received at t reaches the SIP NBBO at t + SIP_LATENCY_MS, not before."""
        sip = SipEmulator(["A", "B"], 5.0, 0.0)
        sip.source(1000, "A", (9.99, 1.0, 10.01, 1.0))
        self.assertFalse(sip.advance(1000 + 5 * MS - 1))
        self.assertIsNone(sip.nbo)
        self.assertTrue(sip.advance(1000 + 5 * MS))
        self.assertEqual(sip.nbo, 10.01)

    def test_toxicity_learns_an_outcome_only_once_it_is_known(self):
        """The running analyzer sees an order's outcome only TOX_HORIZON_S after the order."""
        ta = ToxicityAnalyzer(Params(tox_horizon_s=5.0, tox_min_train=10 ** 9))
        ta.observe(0, [0.0] * 16, BUY, 100.0, 1.0)
        ta.on_time(5 * S - 1, 99.0)
        self.assertEqual(ta.y, [])
        ta.on_time(5 * S, 101.0)                 # dealer sold at 100, the mid is now 101: a loss, toxic
        self.assertEqual(ta.y, [1])

    def test_profit_analyzer_marks_a_fill_only_later(self):
        """Patent col. 13:60-62: profit (or loss) "can only be determined after a trade has been executed"."""
        pa = ProfitAnalyzer(mark_s=5.0)
        pa.on_fill(0, "A", BUY, 10.01, 100, 9.99, 10.01)
        pa.on_time(5 * S - 1, 10.03)
        self.assertEqual(pa.n, 0)
        pa.on_time(5 * S, 10.03)
        self.assertEqual(pa.n, 1)

    def test_tape_has_no_future_mid(self):
        t = Tape()
        t.cur_mid = 100.0
        t.advance(0)
        t.advance(200 * MS)
        self.assertEqual(t.mid_at(100 * MS), 100.0)
        self.assertIsNone(t.mid_at(10 * S))


def _decided(e):
    """When an event's content was fixed: a trade out when it was read on its venue's book, the rest at its time."""
    return e["m"] + int(e.get("resolved_ms", 0.0) * MS) if e["rule"] == "TRADE_OUT" else e["m"]


def _prefix_check(test, data_dir, cut_s, params):
    full = simulate(data_dir, [params])
    c0 = full["c_first"]
    cut = simulate(data_dir, [params], max_minutes=cut_s / 60.0)
    stop = c0 + int(cut_s * S)
    margin = 100 * MS        # an order is handled at its last fill; the fills of one taker order span at most ~60 ms
    a = [e for e in full["head"].events if _decided(e) < stop - margin]
    b = [e for e in cut["head"].events if _decided(e) < stop - margin]
    test.assertGreater(len(a), 0)
    test.assertEqual(a, b)
    ra = [r for r in full["head"].rows if r[0] < stop - margin]
    rb = [r for r in cut["head"].rows if r[0] < stop - margin]
    test.assertGreater(len(ra), 0)
    test.assertEqual(ra, rb)
    return a


class PrefixInvariance(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir(PROBE), "no data/probe recording")
    def test_probe_cut_at_15_s(self):
        _prefix_check(self, PROBE, 15.0, Params(warmup_s=0.0))

    @unittest.skipUnless(os.path.isdir(DEV), "no data/run_dev recording")
    def test_dev_cut_at_100_s_with_the_quotient_running(self):
        """2.5-minute recording, analyzer active after 100 outcomes: quotients and principal-interest decisions
        before the cut do not depend on anything after it."""
        evs = _prefix_check(self, DEV, 100.0, Params(tox_min_train=100, tox_refit_s=10.0, warmup_s=0.0))
        if not any(not e["principal_interest"] for e in evs):
            self.skipTest("the analyzer flagged no order before 100 s in this recording")


class LiveIsReplay(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir(PROBE), "no data/probe recording")
    def test_live_prints_the_replay_lines(self):
        """live.py fed the recorded messages at their recorded receive times prints exactly the lines of the events
        run.py writes for the same recording."""
        import live as live_mod

        names = [n for n in ("coinbase", "kraken", "bitstamp", "gemini")
                 if os.path.exists(os.path.join(PROBE, f"{n}.jsonl.gz"))]
        lv = live_mod.Live(Params(warmup_s=0.0), names, every_s=10.0, rules=set(), quiet=False)
        streams = [_stream(n, os.path.join(PROBE, f"{n}.jsonl.gz"), {}) for n in names]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for c, name, rec in heapq.merge(*streams, key=lambda r: r[0]):
                lv.clock.now = (lambda c=c: c)
                lv.feed(name, rec)
            lv.ses.finish()
            lv.flush()
        printed = buf.getvalue().splitlines()
        expected = [format_line(e) for e in simulate(PROBE, [Params(warmup_s=0.0)])["head"].events]
        self.assertGreater(len(expected), 0)
        self.assertEqual(printed, expected)

    def test_live_clock_never_goes_back(self):
        import live as live_mod

        clk = live_mod.Clock()
        ts = [clk.now() for _ in range(1000)]
        self.assertTrue(all(b >= a for a, b in zip(ts, ts[1:])))


if __name__ == "__main__":
    unittest.main()
