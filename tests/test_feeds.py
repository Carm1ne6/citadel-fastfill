"""The four depth-of-book parsers on real venue messages (tests/fixtures, cut from data/probe by make_fixtures.py).

Venue conventions checked here are documented with their sources in NOTES.md.
"""
import gzip
import json
import os
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from citadel.venues import (BUY, SELL, Bitstamp, Coinbase, Gemini, Kraken, iter_records,  # noqa: E402
                            kraken_checksum, make_venue)

FIX = os.path.join(ROOT, "tests", "fixtures")
PROBE = os.path.join(ROOT, "data", "probe")


def records(venue, folder=FIX):
    return list(iter_records(os.path.join(folder, f"{venue}.jsonl.gz")))


def replay(venue, folder=FIX, check=None):
    """Feed one venue's records; call check(venue, rec, changed, trades) after each. Returns the venue."""
    v = make_venue(venue)
    for rec in records(venue, folder):
        changed, trades = v.on_record(rec)
        if check:
            check(v, rec, changed, trades)
    return v


def not_crossed(test):
    def check(v, rec, changed, trades):
        if v.live:
            b, _ = v.bids.best()
            a, _ = v.asks.best()
            test.assertTrue(b is None or a is None or b < a, f"{v.name} book crossed: {b} >= {a}")
    return check


class Coinbase_(unittest.TestCase):
    def test_snapshot_and_l2update_keep_a_valid_book(self):
        v = replay("coinbase", check=not_crossed(self))
        self.assertTrue(v.live)
        self.assertGreater(v.n_msgs, 100)

    def test_match_side_is_the_maker_side(self):
        """Coinbase docs: "The side field indicates the maker order side". side "sell" = the maker sold, so the
        marketable order (the taker) bought, and the reverse."""
        raw = [r["raw"] for r in records("coinbase") if r.get("raw") and '"type":"match"' in r["raw"]]
        self.assertGreater(len(raw), 10)
        msg = json.loads(raw[0])
        _, trades = Coinbase().on_msg(msg)
        expect = BUY if msg["side"] == "sell" else SELL
        self.assertEqual(trades[0], (expect, float(msg["price"]), float(msg["size"]), msg["taker_order_id"]))

    def test_taker_buys_print_at_or_above_the_mid(self):
        """On the real messages: a taker buy (maker side "sell") prints at or above the mid of the book before it."""
        v = Coinbase()
        hits = total = 0
        for rec in records("coinbase"):
            raw = rec.get("raw")
            if raw and '"type":"match"' in raw and v.live:
                b, _ = v.bids.best()
                a, _ = v.asks.best()
                _, trades = v.on_msg(json.loads(raw))
                side, px = trades[0][:2]
                mid = 0.5 * (a + b)
                total += 1
                hits += (px >= mid - 1e-9) if side == BUY else (px <= mid + 1e-9)
                continue
            v.on_record(rec)
        self.assertGreater(total, 10)
        self.assertGreaterEqual(hits / total, 0.9)

    def test_level2_batch_is_confirmed_as_level2_50(self):
        """The recorder subscribes to level2_batch; Coinbase confirms it as the 50 ms batched channel."""
        subs = [json.loads(r["raw"]) for r in records("coinbase") if r.get("raw") and '"subscriptions"' in r["raw"]]
        names = {c["name"] for c in subs[0]["channels"]}
        self.assertIn("level2_50", names)


class Kraken_(unittest.TestCase):
    def test_every_real_checksum_matches(self):
        """Kraken v2 sends a CRC32 of the top 10 levels with every book message; ours matches all of them."""
        v = replay("kraken", check=not_crossed(self))
        self.assertGreater(v.n_crc, 300)
        self.assertEqual(v.n_crc_bad, 0)
        self.assertTrue(v.live)

    def test_checksum_string_format(self):
        """Price and qty at the pair's precision (BTC/USD: 1 and 8 decimals), '.' and leading zeros removed, asks
        low to high then bids high to low, CRC32."""
        asks = [(64184.7, 0.5)]
        bids = [(64184.6, 0.00006120)]
        s = "641847" + "50000000" + "641846" + "6120"
        self.assertEqual(kraken_checksum(asks, bids), zlib.crc32(s.encode()) & 0xFFFFFFFF)

    def test_bad_checksum_takes_the_book_out(self):
        """A book that fails its checksum is not trusted: the venue leaves both views until a checksum matches."""
        v = Kraken()
        recs = records("kraken")
        for rec in recs:
            v.on_record(rec)
        p, q = v.asks.best()
        v.asks.set(p, q + 1.0)                               # corrupt the best offer
        upd = next(json.loads(r["raw"]) for r in recs if r.get("raw") and '"type":"update"' in r["raw"]
                   and '"channel":"book"' in r["raw"])
        v.on_msg({"channel": "book", "type": "update", "data": [dict(upd["data"][0], bids=[], asks=[])]})
        self.assertFalse(v.live)

    def test_trade_side_is_the_taker_side(self):
        raw = next(r["raw"] for r in records("kraken") if r.get("raw") and '"channel":"trade"' in r["raw"]
                   and '"type":"update"' in r["raw"])
        msg = json.loads(raw)
        _, trades = Kraken().on_msg(msg)
        d = msg["data"][0]
        side = BUY if d["side"] == "buy" else SELL
        self.assertEqual(trades[0], (side, float(d["price"]), float(d["qty"]), (side, d["timestamp"])))

    def test_book_is_kept_at_subscribed_depth(self):
        v = replay("kraken")
        self.assertLessEqual(len(v.bids.keys), Kraken.depth)
        self.assertLessEqual(len(v.asks.keys), Kraken.depth)


class Bitstamp_(unittest.TestCase):
    def test_each_book_message_replaces_the_book(self):
        """order_book_btcusd sends the whole top of the book each time (the recorder keeps 20 levels)."""
        v = Bitstamp()
        for rec in records("bitstamp"):
            raw = rec.get("raw")
            changed, _ = v.on_record(rec)
            if raw and '"event":"data"' in raw:
                d = json.loads(raw)["data"]
                self.assertTrue(changed)
                self.assertEqual(v.bids.best()[0], float(d["bids"][0][0]))
                self.assertEqual(v.asks.best()[0], float(d["asks"][0][0]))
                self.assertEqual(len(v.bids.keys), len(d["bids"]))

    def test_trade_type_0_is_a_buy(self):
        """live_trades: type 0 = buy, 1 = sell (the taker's side)."""
        msgs = [json.loads(r["raw"]) for r in records("bitstamp") if r.get("raw") and '"event":"trade"' in r["raw"]]
        self.assertGreater(len(msgs), 3)
        for m in msgs:
            _, trades = Bitstamp().on_msg(m)
            self.assertEqual(trades[0][0], BUY if m["data"]["type"] == 0 else SELL)
            self.assertEqual(trades[0][1], float(m["data"]["price_str"]))

    def test_trade_type_is_the_side_of_the_newer_order(self):
        """The taker is the order that arrived last, the one with the larger order id: type 0 (buy) exactly when
        buy_order_id > sell_order_id, on every real trade of the fixture."""
        import check_venues
        raws = [r["raw"] for r in records("bitstamp") if r.get("raw") and '"event":"trade"' in r["raw"]]
        self.assertGreater(len(raws), 3)
        for raw in raws:
            _, trades = Bitstamp().on_msg(json.loads(raw))
            self.assertEqual(trades[0][0], check_venues.bitstamp_newer_side(raw))


class Gemini_(unittest.TestCase):
    def test_first_l2_updates_is_the_full_book(self):
        v = replay("gemini", check=not_crossed(self))
        self.assertTrue(v.live)
        self.assertGreater(len(v.bids.keys), 100)

    def test_trade_side_is_the_taker_side(self):
        msgs = [m for m in (json.loads(r["raw"]) for r in records("gemini") if r.get("raw"))
                if m.get("type") == "trade"]
        self.assertGreater(len(msgs), 3)
        for m in msgs:
            _, trades = Gemini().on_msg(m)
            self.assertEqual(trades[0][:3], (BUY if m["side"] == "buy" else SELL, float(m["price"]),
                                         float(m["quantity"])))


class Recorder(unittest.TestCase):
    def test_reconnect_resets_the_book(self):
        """A reconnect ('connected' or 'error' event) clears the book until a new snapshot arrives."""
        for name in ("coinbase", "kraken", "bitstamp", "gemini"):
            v = replay(name)
            self.assertTrue(v.live, name)
            v.on_record({"event": "connected"})
            self.assertFalse(v.live, name)
            self.assertEqual(v.bids.best(), (None, 0.0))

    def test_truncated_gzip_stops_cleanly(self):
        """Files of a killed recorder have no gzip trailer and may end in half a line."""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.jsonl.gz")
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            for i in range(5000):
                fh.write('{"t": %d, "m": %d, "raw": "{}"}\n' % (i, i))
        data = open(p, "rb").read()
        open(p, "wb").write(data[: len(data) - 40])
        recs = list(iter_records(p))
        self.assertGreater(len(recs), 0)
        self.assertTrue(all("raw" in r for r in recs))

    @unittest.skipUnless(os.path.isdir(PROBE), "no data/probe recording")
    def test_whole_probe_recording(self):
        """Every venue of the 24 s probe: books valid, never crossed, every Kraken checksum matches."""
        for name in ("coinbase", "kraken", "bitstamp", "gemini"):
            v = replay(name, PROBE, not_crossed(self))
            self.assertTrue(v.live, name)
            if name == "kraken":
                self.assertEqual(v.n_crc_bad, 0)
            # trades are not counted here: a quiet venue (Gemini on 2026-11-18) can print almost none in a probe

    @unittest.skipUnless(os.path.isdir(PROBE), "no data/probe recording")
    def test_taker_sides_on_the_probe(self):
        """check_venues.py on the probe: a taker buy prints at or above the mid of the book before it (Coinbase,
        Kraken, Gemini); Bitstamp's side is the side of its newer order (its book can arrive before the trade)."""
        import check_venues
        for name in ("coinbase", "kraken", "gemini"):
            s = check_venues.check_venue(name, os.path.join(PROBE, f"{name}.jsonl.gz"))
            if s["trades_checked"] <= 5:            # a quiet venue: nothing to check
                continue
            self.assertGreaterEqual(s["taker_side_ok"] / s["trades_checked"], 0.95, name)
            self.assertEqual(s["crossed"], 0, name)
        s = check_venues.check_venue("bitstamp", os.path.join(PROBE, "bitstamp.jsonl.gz"))
        self.assertGreater(s["id_checked"], 5)
        self.assertEqual(s["id_ok"], s["id_checked"])


if __name__ == "__main__":
    unittest.main()
