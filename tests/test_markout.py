"""tools/markout_my_fills.py: markouts of the reader's own fills against a recording (the patent's profit analyzer,
col. 13:60-14:5, turned to the reader's side), on a synthetic Coinbase recording with known mids."""
import contextlib
import csv
import datetime as dt
import gzip
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import markout_my_fills as M  # noqa: E402

S = 1_000_000_000
T0 = 1_794_972_000 * S                     # 2026-11-18T03:20:00Z


def _iso(ns):
    d = dt.datetime.fromtimestamp(ns // S, tz=dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S") + ".%03dZ" % (ns % S // 1_000_000)


def write_recording(folder):
    """Coinbase: mid 100.01 from T0 + 0.1 s, 100.11 from T0 + 2 s, 99.91 from T0 + 10 s; last message T0 + 40 s."""
    def rec(t, msg=None, event=None):
        d = {"t": t, "m": t}
        if event:
            d["event"] = event
        else:
            d["raw"] = json.dumps(msg)
        return json.dumps(d) + "\n"

    def upd(changes):
        return {"type": "l2update", "product_id": "BTC-USD", "changes": changes}
    lines = [
        rec(T0, event="connected"),
        rec(T0 + S // 10, {"type": "snapshot", "product_id": "BTC-USD", "bids": [["100.00", "1"]],
                           "asks": [["100.02", "1"]]}),
        rec(T0 + 2 * S, upd([["buy", "100.10", "1"], ["sell", "100.12", "1"], ["buy", "100.00", "0"],
                             ["sell", "100.02", "0"]])),
        rec(T0 + 10 * S, upd([["buy", "99.90", "1"], ["sell", "99.92", "1"], ["buy", "100.10", "0"],
                              ["sell", "100.12", "0"]])),
        rec(T0 + 40 * S, {"type": "heartbeat"}),
    ]
    with gzip.open(os.path.join(folder, "coinbase.jsonl.gz"), "wt", encoding="utf-8") as fh:
        fh.writelines(lines)


def write_fills(path):
    rows = [
        ["Time", "Side", "Price", "Size"],
        [_iso(T0 + S), "buy", "100.00", "0.5"],                    # mid 100.01 at the fill
        [str((T0 + 3 * S) // 1_000_000), "SELL", "100.12", "0.2"],  # epoch ms; mid 100.11 at the fill
        [_iso(T0 + 20 * S), "b", "99.92", "0.1"],                   # its 30 s mark falls after the recording
        [_iso(T0 - 5 * S), "s", "100.00", "1.0"],                   # before the first book: left out
    ]
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)


class Markouts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        write_recording(cls.dir)
        cls.fills = os.path.join(cls.dir, "fills.csv")
        write_fills(cls.fills)
        fills = M.read_fills(cls.fills, "coinbase")
        cls.rows = M.markouts(fills, {"coinbase": M.mid_series(cls.dir, "coinbase")})

    def test_markout_is_the_later_mid_against_the_fill_price(self):
        buy, sell = self.rows[0], self.rows[1]
        self.assertAlmostEqual(buy["mid_at_fill"], 100.01)
        self.assertAlmostEqual(buy["h"][1.0]["total"], 0.11)        # mid 100.11 one second later
        self.assertAlmostEqual(buy["h"][1.0]["edge"], 0.01)         # bought a cent under the mid
        self.assertAlmostEqual(buy["h"][1.0]["move"], 0.10)
        self.assertAlmostEqual(buy["h"][1.0]["bps"], 11.0)
        self.assertAlmostEqual(buy["h"][30.0]["total"], -0.09)      # mid 99.91 thirty seconds later
        self.assertTrue(buy["h"][30.0]["toxic"])                    # a loss: toxic (patent col. 4:1-3)
        self.assertAlmostEqual(sell["h"][1.0]["total"], 0.01)       # sold a cent over the mid, no move
        self.assertAlmostEqual(sell["h"][30.0]["total"], 0.21)      # the mid fell 0.20 after the sale

    def test_fills_outside_the_recording_are_left_out(self):
        late, early = self.rows[2], self.rows[3]
        self.assertIsNotNone(late["h"][5.0])
        self.assertIsNone(late["h"][30.0])                          # T0 + 50 s is after the last message
        self.assertTrue(all(early["h"][h] is None for h in M.HORIZONS))

    def test_summary(self):
        res = M.summarize(self.rows)["all"]
        self.assertEqual((res[1.0]["n"], res[1.0]["left_out"]), (3, 1))
        self.assertAlmostEqual(res[1.0]["losing_pct"], 100 / 3)      # the third fill: -0.01
        self.assertAlmostEqual(res[1.0]["usd"], 0.5 * 0.11 + 0.2 * 0.01 - 0.1 * 0.01)
        self.assertAlmostEqual(res[1.0]["usd"], res[1.0]["edge_usd"] + res[1.0]["move_usd"])
        self.assertEqual(res[30.0]["n"], 2)
        self.assertAlmostEqual(res[30.0]["usd"], 0.5 * -0.09 + 0.2 * 0.21)

    def test_command_line(self):
        out = io.StringIO()
        per = os.path.join(self.dir, "per_fill.csv")
        with contextlib.redirect_stdout(out):
            rc = M.main(["--fills", self.fills, "--data", self.dir, "--json", "--per-fill", per])
        self.assertEqual(rc, 0)
        d = json.loads(out.getvalue())
        self.assertEqual(d["all"]["1s"]["n"], 3)
        with open(per, newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(float(rows[0]["markout_1s_bps"]), 11.0)

    def test_shift_moves_fills_onto_the_recording_clock(self):
        per = os.path.join(self.dir, "shifted.csv")
        with contextlib.redirect_stdout(io.StringIO()):
            M.main(["--fills", self.fills, "--data", self.dir, "--shift-ms", "-500", "--per-fill", per])
        with open(per, newline="") as fh:
            first = next(csv.DictReader(fh))
        self.assertAlmostEqual(float(first["markout_1s_bps"]), 1.0)   # 1.5 s after T0 the mid is still 100.01


class Inputs(unittest.TestCase):
    def test_time_formats(self):
        ns = T0 + 250 * 1_000_000
        self.assertEqual(M.parse_time("2026-11-18T03:20:00.250Z"), ns)
        self.assertEqual(M.parse_time("2026-11-18 03:20:00.25"), ns)               # no zone = UTC
        self.assertEqual(M.parse_time("2026-11-18T05:20:00.250+02:00"), ns)
        self.assertEqual(M.parse_time("2026-11-18T03:20:00.250000001Z"), ns + 1)   # nanoseconds kept
        self.assertEqual(M.parse_time("1794972000.25"), ns)                        # epoch seconds
        self.assertEqual(M.parse_time("1794972000250"), ns)                        # epoch ms
        self.assertEqual(M.parse_time("1794972000250000"), ns)                     # epoch us
        self.assertEqual(M.parse_time(str(ns)), ns)                                # epoch ns
        with self.assertRaises(ValueError):
            M.parse_time("yesterday")

    def test_columns_and_sides(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "f.csv")
        with open(p, "w", newline="") as fh:
            csv.writer(fh).writerows([["timestamp", "SIDE", "px", "qty", "exchange", "fee"],
                                      ["1794972000", "Sell", "1", "2", "Kraken", "0.1"]])
        f = M.read_fills(p, "coinbase")[0]
        self.assertEqual((f["side"], f["price"], f["size"], f["venue"]), ("SELL", 1.0, 2.0, "kraken"))
        with open(p, "w", newline="") as fh:
            csv.writer(fh).writerows([["time", "side", "price", "size"], ["1794972000", "hold", "1", "2"]])
        with self.assertRaises(SystemExit):
            M.read_fills(p)
        with open(p, "w", newline="") as fh:
            csv.writer(fh).writerows([["time", "side", "price"], ["1794972000", "buy", "1"]])
        with self.assertRaises(SystemExit):
            M.read_fills(p)


if __name__ == "__main__":
    unittest.main()
