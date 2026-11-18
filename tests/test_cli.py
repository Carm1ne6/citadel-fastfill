"""The command line and its documentation contract: the config file is --set (same parser, same Params), every
parameter is in fastfill.example.toml with its source, explain finds every RULES.md row it quotes, the commands run
end to end on the real messages of tests/fixtures, and the AI docs only name commands that exist."""
import argparse
import contextlib
import glob
import io
import json
import os
import re
import sys
import tempfile
import unittest
from dataclasses import fields

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from citadel import cli, explain  # noqa: E402
from citadel.config import load, parse_args, params_from  # noqa: E402
from citadel.params import Params, with_overrides  # noqa: E402
from test_sec_examples import MS, S, engine, sip_publish, venue  # noqa: E402

EXAMPLE = os.path.join(ROOT, "fastfill.example.toml")
FIX = os.path.join(ROOT, "tests", "fixtures")


def _toml(text):
    fd, path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main(argv)
    return rc, out.getvalue()


def _replay_parser():
    """The parameter options of run.py, built the same way."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default=Params.preset, choices=["sip", "latency"])
    ap.add_argument("--latency-ms", type=float, default=Params.sip_latency_ms)
    ap.add_argument("--round-lot", type=float, default=Params.round_lot_btc)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--config", default=None)
    return ap


def _base(a):
    return with_overrides(Params(preset=a.preset, sip_latency_ms=a.latency_ms, round_lot_btc=a.round_lot), a.set)


class ConfigFile(unittest.TestCase):
    def test_example_lists_every_parameter_with_its_meaning_and_source(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        for f in fields(Params):
            at = [i for i, ln in enumerate(lines) if ln.startswith(f"{f.name} = ")]
            self.assertEqual(len(at), 1, f"fastfill.example.toml: {f.name}")
            i = at[0]
            self.assertTrue(lines[i - 1].startswith("# source: "), f"{f.name}: no '# source:' line above it")
            self.assertTrue(lines[i - 2].startswith("# ") and len(lines[i - 2]) > 10, f"{f.name}: no meaning line")

    def test_example_is_exactly_the_defaults(self):
        self.assertEqual(params_from(EXAMPLE), Params())
        self.assertEqual(len(load(EXAMPLE)), len(fields(Params)))

    def test_a_file_gives_the_same_params_as_the_same_set_items(self):
        path = _toml('preset = "latency"\n[toxicity]\ntox_threshold = 0.8\ntox_min_train = 500\n'
                     '[market]\nvenues = ["coinbase", "kraken"]\nbasis_adjust = false\n'
                     'venue_ticks = { coinbase = 0.01, kraken = 0.1 }\n')
        by_set = with_overrides(Params(), ["preset=latency", "tox_threshold=0.8", "tox_min_train=500",
                                           "venues=coinbase,kraken", "basis_adjust=false",
                                           "venue_ticks=coinbase:0.01,kraken:0.1"])
        self.assertEqual(params_from(path), by_set)
        self.assertEqual(by_set.venue_ticks, (("coinbase", 0.01), ("kraken", 0.1)))
        self.assertIsInstance(by_set.tox_min_train, int)

    def test_the_command_line_wins_over_the_file(self):
        path = _toml('preset = "latency"\nsip_latency_ms = 50\ntox_threshold = 0.8\n')
        ap = _replay_parser()
        a = parse_args(ap, ["--config", path])
        p = _base(a)
        self.assertEqual((p.preset, p.sip_latency_ms, p.tox_threshold), ("latency", 50.0, 0.8))
        self.assertIsInstance(p.sip_latency_ms, float)
        a = parse_args(_replay_parser(), ["--config", path, "--preset", "sip", "--set", "tox_threshold=0.9"])
        p = _base(a)
        self.assertEqual((p.preset, p.sip_latency_ms, p.tox_threshold), ("sip", 50.0, 0.9))

    def test_without_a_file_nothing_changes(self):
        a = parse_args(_replay_parser(), [])
        self.assertEqual(_base(a), Params())

    def test_mistakes_stop_the_run(self):
        for text in ('tox_treshold = 0.8\n',                    # a typo in a name
                     'preset = "fast"\n',                       # not a preset
                     'smartprovide_feeds = ["binance"]\n',      # no parser for that venue
                     'tox_min_train = 1.5\n',                   # not an integer
                     'sip_latency_ms = 5\nsip_latency_ms = 6\n',  # not valid TOML (a key twice)
                     '[sip]\npreset = "sip"\n[x]\npreset = "sip"\n'):  # one parameter in two tables
            with self.assertRaises(SystemExit, msg=text):
                load(_toml(text))

    def test_set_takes_name_value_pairs(self):
        p = with_overrides(Params(), ["venue_ticks=coinbase:0.01, kraken:0.10"])
        self.assertEqual(p.venue_ticks, (("coinbase", 0.01), ("kraken", 0.1)))
        with self.assertRaises(SystemExit):
            with_overrides(Params(), ["venue_ticks=coinbase"])


class Explain(unittest.TestCase):
    def test_every_step_names_exactly_one_rules_row(self):
        rows = explain.load_rules(os.path.join(ROOT, "RULES.md"))
        for key in explain.ROWS:
            row = explain.find_row(rows, key)                  # raises unless exactly one row matches
            self.assertTrue(row["cells"][1], key)

    def _events(self, which):
        """Events of the SEC examples (test_sec_examples.py) as run.py writes them: FastFill with a trade out
        (SEC 31, fn 13), or SmartProvide filled in part, then re-evaluated (SEC 36-39)."""
        if which == "fastfill":
            vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                    "B": venue("B", asks=[(10.00, 100)], bids=[(9.98, 300)])}
            e = engine(vens)
            e.tox.quotient = lambda x: 0.99                     # likely toxic: no principal interest
            t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.98, 300, None, 0.0)})
            e.on_order(t + MS, "A", "BUY", 10.01, 90)
            e.on_book(t + 3 * MS, "B")
        else:
            vens = {"A": venue("A", asks=[(10.01, 500)], bids=[(9.99, 500)]),
                    "B": venue("B", asks=[(10.02, 400)], bids=[(9.99, 300)])}
            e = engine(vens)
            t = sip_publish(e, 0, {"A": (9.99, 500, 10.01, 500), "B": (9.99, 300, 10.02, 400)})
            e.on_order(t + MS, "A", "BUY", 10.01, 100)
            e.on_print(t + 500 * MS, "B", "SELL", 10.00, 40)
            e.advance(t + 2 * S)
        d = tempfile.mkdtemp()
        path = os.path.join(d, "events.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for ev in e.events:
                fh.write(json.dumps({k: v for k, v in ev.items() if k != "m"}) + "\n")
        return path, [ev["rule"] for ev in e.events]

    def test_fastfill_and_trade_out_quote_their_paragraphs(self):
        path, rules = self._events("fastfill")
        self.assertEqual(rules, ["FASTFILL", "TRADE_OUT"])
        rc, out = _run(["explain", "--events", path, "--id", "1"])
        self.assertEqual(rc, 0)
        for s in ("FastFill immediately internalized the order",          # SEC 30
                  "no greater than 90% of the quantity",                  # fn 13
                  "approximately 55% of the time",                        # fn 13
                  "in a contemporaneous offsetting transaction",          # fn 10
                  "the order is executed at step 508",                    # patent col. 11:59-67
                  "a running tab of profit (or loss) can be kept",        # patent col. 14:3-5
                  "ANOTHER_MARKET_CENTER"):
            self.assertIn(s, out)

    def test_smartprovide_chain(self):
        path, rules = self._events("smartprovide")
        self.assertEqual(rules, ["SMARTPROVIDE", "SP_FILLED", "SP_REEVAL"])
        rc, out = _run(["explain", "--events", path, "--id", "1", "--short"])
        self.assertEqual(rc, 0)
        for s in ("SEC 12", "SEC 36", "SEC 38", "SEC 37", "SEC 39", "displayed a bid at 10.00", "filled 499 ms"):
            self.assertIn(s, out)
        rc, out = _run(["explain", "--events", path, "--rule", "sp_reeval", "--json"])
        x = json.loads(out)
        self.assertEqual(x["other_events"], ["SMARTPROVIDE", "SP_FILLED"])
        self.assertTrue(all(r["source"] for item in x["events"] for s in item["steps"] for r in s["rules"]))

    def test_a_run_whose_analyzer_never_fitted_says_so(self):
        """With summary.json next to the events: the run's parameters, and a silent analyzer named as such."""
        path, _ = self._events("smartprovide")
        with open(os.path.join(os.path.dirname(path), "summary.json"), "w", encoding="utf-8") as fh:
            json.dump({"meta": {"params": Params(tox_threshold=0.75).as_dict()}, "toxicity": {"walk_forward_fits": 0}},
                      fh)
        rc, out = _run(["explain", "--events", path, "--id", "1", "--short"])
        self.assertIn("never fitted in this run", out)
        self.assertIn("parameters (from summary.json)", out)
        self.assertIn("threshold 0.75", out)

    def test_list_and_bad_selections(self):
        path, _ = self._events("fastfill")
        rc, out = _run(["explain", "--events", path, "--list"])
        self.assertIn("FASTFILL 1, TRADE_OUT 1", out)
        for argv in (["--id", "7"], ["--rule", "SMARTPROVIDE"], ["--rule", "NOPE"]):
            with self.assertRaises(SystemExit):
                _run(["explain", "--events", path] + argv)


class Commands(unittest.TestCase):
    """replay, sensitivity, sec-check and params end to end on the real messages of tests/fixtures (0.3 min)."""

    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp()
        cls.cfg = _toml('warmup_s = 0.0\n[toxicity]\ntox_threshold = 0.8\n')
        with contextlib.redirect_stdout(io.StringIO()):
            cls.rc = cli.main(["replay", "--data", FIX, "--out", cls.out, "--config", cls.cfg])
        with open(os.path.join(cls.out, "summary.json"), encoding="utf-8") as fh:
            cls.summary = json.load(fh)

    def test_replay_writes_the_run_with_the_config(self):
        self.assertEqual(self.rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.out, "events.jsonl")))
        p = self.summary["meta"]["params"]
        self.assertEqual((p["tox_threshold"], p["warmup_s"]), (0.8, 0.0))
        self.assertGreater(self.summary["stats"]["orders"], 0)

    def test_sensitivity_reads_the_summary(self):
        rc, out = _run(["sensitivity", "--run", self.out, "--json"])
        x = json.loads(out)
        self.assertEqual(sorted(x["grids"]), ["feed", "latency", "round_lot", "threshold", "trade_out"])
        self.assertEqual([r["tox_threshold"] for r in x["grids"]["threshold"]], [0.5, 0.6, 0.7, 0.8, 0.9])
        rc, out = _run(["sensitivity", "--run", self.out, "--only", "latency,threshold"])
        self.assertIn("SIP latency", out)
        self.assertNotIn("round lot (SEC 5", out)

    def test_sensitivity_custom_grid_replays(self):
        out = tempfile.mkdtemp()
        with contextlib.redirect_stderr(io.StringIO()):
            rc, text = _run(["sensitivity", "--data", FIX, "--run", out, "--threshold-grid", "0.55,0.95",
                             "--latency-grid", "5,20", "--set", "warmup_s=0", "--json"])
        x = json.loads(text)
        self.assertEqual([r["tox_threshold"] for r in x["grids"]["threshold"]], [0.55, 0.7, 0.95])
        self.assertEqual(sorted({r["sip_latency_ms"] for r in x["grids"]["latency"]}), [5.0, 20.0])
        from citadel import pipeline
        self.assertEqual(pipeline.TOX_GRID, (0.5, 0.6, 0.7, 0.8, 0.9))      # the module grid is restored

    def test_sec_check(self):
        rc, out = _run(["sec-check", "--run", self.out, "--json"])
        x = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(x["sec_numbers_match"])
        self.assertEqual(x["rows"][-1]["sec"], "$22,665,268")
        rc, out = _run(["sec-check", "--run", self.out])
        self.assertIn("para 29", out)

    def test_params(self):
        rc, out = _run(["params", "--config", self.cfg, "--changed", "--json"])
        rows = json.loads(out)
        self.assertEqual({r["name"] for r in rows}, {"tox_threshold", "warmup_s"})
        rc, out = _run(["params"])
        for f in fields(Params):
            self.assertIn(f.name, out)

    def test_help_and_unknown_command(self):
        rc, out = _run(["--help"])
        for name in list(cli.SCRIPTS) + list(cli.LOCAL):
            self.assertIn(name, out)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["frobnicate"]), 2)
        with self.assertRaises(SystemExit):                       # argparse --help exits 0
            _run(["replay", "--help"])


class AiDocs(unittest.TestCase):
    def test_agents_md_is_claude_md(self):
        with open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8") as a, \
                open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8") as b:
            self.assertEqual(a.read(), b.read())

    def test_docs_name_only_commands_that_exist(self):
        docs = ["CLAUDE.md", ".claude/skills/citadel-fastfill/SKILL.md"] + \
            sorted(glob.glob(os.path.join(ROOT, ".claude", "commands", "*.md"))) + \
            sorted(glob.glob(os.path.join(ROOT, "prompts", "*.md")))
        known = set(cli.SCRIPTS) | set(cli.LOCAL)
        seen = 0
        for d in docs:
            with open(os.path.join(ROOT, d), encoding="utf-8") as fh:
                text = fh.read()
            for cmd in re.findall(r"citadel-fastfill ([a-z][a-z-]+)", text):
                seen += 1
                self.assertIn(cmd, known, f"{d}: citadel-fastfill {cmd}")
        self.assertGreater(seen, 20)

    def test_every_command_file_has_a_description(self):
        names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, ".claude", "commands", "*.md")))
        self.assertEqual(names, ["add-venue.md", "explain.md", "live.md", "replay.md", "sec-check.md",
                                 "sensitivity.md"])
        for n in names + ["../skills/citadel-fastfill/SKILL.md"]:
            with open(os.path.join(ROOT, ".claude", "commands", n), encoding="utf-8") as fh:
                head = fh.read().split("---")
            self.assertIn("description:", head[1], n)


if __name__ == "__main__":
    unittest.main()
