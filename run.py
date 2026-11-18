"""Run the rebuild on one recording and print the report with the SEC's numbers next to ours.

    python -X utf8 run.py --data data/run1
    python -X utf8 run.py --data data/run1 --config fastfill.example.toml --set tox_threshold=0.8

Writes out/<run>/events.jsonl (every FastFill, SmartProvide and trade out event with its outcome) and
out/<run>/summary.json (statistics, sensitivity grid, toxicity test, report lines).
A simulation of a wholesaler on public data: no orders are sent anywhere.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from citadel.config import add_config_argument, parse_args  # noqa: E402
from citadel.params import Params, with_overrides  # noqa: E402
from citadel.pipeline import run_all, write_outputs  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, help="recording folder (data/<run>), made by citadel-fastfill record")
    ap.add_argument("--out", default=None, help="output folder (default out/<run name>)")
    ap.add_argument("--preset", default=Params.preset, choices=["sip", "latency"])
    ap.add_argument("--latency-ms", type=float, default=Params.sip_latency_ms)
    ap.add_argument("--round-lot", type=float, default=Params.round_lot_btc)
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override any parameter of citadel/params.py, e.g. --set smartprovide_feeds=coinbase")
    add_config_argument(ap)
    a = parse_args(ap)
    t0 = time.time()
    base = with_overrides(Params(preset=a.preset, sip_latency_ms=a.latency_ms, round_lot_btc=a.round_lot), a.set)
    R = run_all(a.data, base=base, max_minutes=a.max_minutes)
    out = a.out or os.path.join("out", os.path.basename(a.data.rstrip("/\\")))
    write_outputs(out, R)
    for line in R["report"]:
        print(line)
    print(f"\nwrote {out}/events.jsonl and {out}/summary.json in {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
