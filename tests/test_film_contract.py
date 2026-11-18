"""The film data: 1 s trade candles (synthetic prints), and end-to-end on the 24 s probe recording against the contract."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import check_film  # noqa: E402
import export_film  # noqa: E402
from citadel.params import Params  # noqa: E402
from citadel.pipeline import run_all  # noqa: E402
from citadel.sim import Tape  # noqa: E402

PROBE = os.path.join(ROOT, "data", "probe")
MS = 1_000_000


class Candles1s(unittest.TestCase):
    def _tape(self, prints):
        tape = Tape()
        for t_ms, px, size in prints:
            tape.on_print(t_ms * MS, "coinbase", px, size, candle=True)
        return tape

    def test_empty_seconds_repeat_the_last_close_with_no_volume(self):
        tape = self._tape([(1_000, 100.0, 0.1), (1_400, 101.0, 0.2), (1_900, 99.5, 0.3), (4_200, 102.0, 0.5)])
        c1 = export_film.candles_1s(tape)
        self.assertEqual([r[0] for r in c1], [1000, 2000, 3000, 4000])
        self.assertEqual(c1[0], [1000, 100.0, 101.0, 99.5, 99.5, 0.6])
        self.assertEqual(c1[1], [2000, 99.5, 99.5, 99.5, 99.5, 0.0])      # no print: the last close, v = 0
        self.assertEqual(c1[2], [3000, 99.5, 99.5, 99.5, 99.5, 0.0])
        self.assertEqual(c1[3], [4000, 102.0, 102.0, 102.0, 102.0, 0.5])

    def test_one_second_candles_rebuild_the_five_second_candles(self):
        prints = [(5_000 + 37 * i, 100.0 + ((i * 7) % 11) * 0.01, 0.001 * (1 + i % 3)) for i in range(400)]
        prints += [(30_000 + 250 * i, 101.0 - 0.02 * i, 0.01) for i in range(20)]   # after an empty stretch
        tape = self._tape(prints)
        film = {"candles_1s": export_film.candles_1s(tape), "candles_5s": export_film.candles(tape, 5000)}
        self.assertEqual(check_film.check_candles_1s(film), [])
        film["candles_1s"][3][2] += 0.05                                       # break one high
        self.assertTrue(check_film.check_candles_1s(film))


@unittest.skipUnless(os.path.isdir(PROBE), "no data/probe recording")
class FilmContract(unittest.TestCase):
    def test_probe_export_matches_contract(self):
        R = run_all(PROBE, base=Params(warmup_s=0.0), grid=(5.0,))
        film = export_film.build(R)
        err, crossed = check_film.check(film)
        self.assertEqual(err, [])
        self.assertEqual(crossed, 0)
        self.assertGreater(film["meta"]["orders_total"], 0)
        self.assertGreater(len(film["candles_1s"]), 15)
        self.assertTrue(all(len(x) <= 96 for x in film["report_lines"]))


if __name__ == "__main__":
    unittest.main()
