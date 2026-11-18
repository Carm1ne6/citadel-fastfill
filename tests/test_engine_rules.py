"""SEC paragraphs the SEC examples do not cover (26, 39, fn 12), and the documentation contract: every parameter of
citadel/params.py has a source and a row in RULES.md."""
import os
import sys
import unittest
from dataclasses import fields

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from citadel.params import SOURCE, Params  # noqa: E402
from citadel.report import format_line  # noqa: E402
from test_sec_examples import MS, S, engine, rules, sip_publish, venue  # noqa: E402


class Reevaluation(unittest.TestCase):
    def _setup(self, principal_interest):
        # SIP NBO $10.01 from A; B (the only feed SmartProvide reads) shows $10.02: SmartProvide (SEC 34-35)
        vens = {"A": venue("A", asks=[(10.01, 500), (10.03, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.02, 400)], bids=[(9.99, 300)])}
        e = engine(vens)
        if not principal_interest:
            e.tox.quotient = lambda x: 0.99                     # likely toxic (patent step 510)
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400)})
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        self.assertEqual(rules(e), ["SMARTPROVIDE"])
        e.advance(t + 2 * S)                                    # the 1 s display ends unfilled (SEC 38)
        return e

    def test_para_39_with_principal_interest_internalizes(self):
        e = self._setup(principal_interest=True)
        rv = e.events[-1]
        self.assertEqual(rv["rule"], "SP_REEVAL")
        self.assertEqual(rv["action"], "internalize")
        self.assertAlmostEqual(rv["fill_px"], 10.01)
        self.assertEqual(e.st["sp_internalized_after"], 1)
        self.assertAlmostEqual(e.inventory, -100)               # CES sold 100 shares from its own account

    def test_para_39_fn_12_without_principal_interest_routes(self):
        """SEC 39: re-evaluation may route "an order to the market in an attempt to take liquidity"; fn 12: without
        a principal interest CES worked the order "in the market on a riskless principal basis"."""
        e = self._setup(principal_interest=False)
        rv = e.events[-1]
        self.assertEqual(rv["action"], "route")
        self.assertAlmostEqual(rv["fill_px"], 10.01)            # took 100 of A's 500 at $10.01
        self.assertEqual(e.st.get("sp_internalized_after", 0), 0)
        self.assertEqual(e.st["sp_routed_after"], 1)
        self.assertAlmostEqual(e.inventory, 0)                  # riskless principal: nothing on CES's books
        self.assertIn("ROUTED", format_line(rv))
        self.assertLessEqual(len(format_line(rv)), 96)

    def test_route_walks_the_displayed_book(self):
        e = self._setup(principal_interest=False)
        self.assertAlmostEqual(e._take("BUY", 700), (500 * 10.01 + 200 * 10.02) / 700)

    def test_principal_interest_comes_from_the_analyzer(self):
        """SEC 26 + patent FIG. 5 (our link): the decision to internalize is the toxicity analyzer's response."""
        e = self._setup(principal_interest=False)
        self.assertFalse(e.events[0]["principal_interest"])
        self.assertAlmostEqual(e.events[0]["toxicity"], 0.99)


class SmartProvideFeeds(unittest.TestCase):
    def test_para_34_triggers_on_one_referenced_feed_and_not_the_others(self):
        """SEC 12: triggered when the SIP was better than the best price "from at least one of the depth of book
        feeds"; SEC 34: it "sometimes could be triggered when the difference existed between the SIP and only one of
        the depth of book feeds SmartProvide referenced, and not the others". SmartProvide reads C and B; C shows the
        SIP offer $10.01, B shows $10.02: triggered by B alone."""
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "B": venue("B", asks=[(10.02, 400)], bids=[(9.99, 300)]),
                "C": venue("C", asks=[(10.01, 300)], bids=[(9.98, 300)])}
        e = engine(vens, smartprovide_feeds=("C", "B"))
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400),
                               "C": (9.98, 300, 10.01, 300)})
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        self.assertEqual(rules(e), ["SMARTPROVIDE"])
        ev = e.events[0]
        self.assertEqual(ev["ref_feed"], "B")
        self.assertAlmostEqual(ev["depth_px"], 10.02)
        self.assertAlmostEqual(ev["post_px"], 10.00)

    def test_no_trigger_when_every_referenced_feed_shows_the_sip_price(self):
        vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                "C": venue("C", asks=[(10.01, 300)], bids=[(9.98, 300)])}
        e = engine(vens, smartprovide_feeds=("C",), sp_post_venue="C")
        t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "C": (9.98, 300, 10.01, 300)})
        e.on_order(t + MS, "A", "BUY", 10.01, 100)
        self.assertEqual(rules(e), [])
        self.assertEqual(e.st["other_orders"], 1)


class Documentation(unittest.TestCase):
    def test_every_parameter_has_a_source_and_a_rules_row(self):
        text = open(os.path.join(ROOT, "RULES.md"), encoding="utf-8").read()
        for f in fields(Params):
            self.assertIn(f.name, SOURCE, f"params.SOURCE lacks {f.name}")
            self.assertTrue(f"`{f.name}`" in text, f"RULES.md lacks a row for `{f.name}`")

    def test_sources_say_ours_or_cite_a_document(self):
        for k, v in SOURCE.items():
            self.assertTrue(any(w in v for w in ("OURS", "SEC", "PAT", "SPEC", "venue", "Coinbase", "tolerance")),
                            f"{k}: {v}")


if __name__ == "__main__":
    unittest.main()
