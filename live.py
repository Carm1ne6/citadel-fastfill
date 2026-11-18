"""Watch the FastFill and SmartProvide rules work on live BTC/USD order books.

    python -X utf8 live.py                   # until Ctrl+C
    python -X utf8 live.py --seconds 120     # stop after 120 s (orders start after the 60 s warm-up)
    python -X utf8 live.py --preset latency --latency-ms 50 --set smartprovide_feeds=coinbase
    python -X utf8 live.py --config fastfill.example.toml

Connects to the same four public WebSocket feeds as record.py (Coinbase, Kraken, Bitstamp, Gemini; same endpoints and
subscriptions) and feeds every message, as it arrives, through the same engine as run.py (citadel.sim.Session). Every
FastFill, trade out attempt, SmartProvide display, fill and re-evaluation is printed as the same terminal line run.py
writes to events.jsonl, with a running summary every 10 s.

Only public market data is read: nothing is sent to any venue besides the subscribe messages, and no orders are sent
anywhere. This is a simulation of a wholesaler on public data, not Citadel's code.
"""
import argparse
import asyncio
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import websockets  # noqa: E402

from citadel.ces import hms, money  # noqa: E402
from citadel.config import add_config_argument, parse_args  # noqa: E402
from citadel.params import Params, with_overrides  # noqa: E402
from citadel.report import SHORT, W, engine_stats, format_line  # noqa: E402
from citadel.sim import CLOCK_STEP_NS, Session  # noqa: E402
from record import VENUES as FEEDS, trim_bitstamp  # noqa: E402  (endpoints and subscriptions of the recorder)


class Clock:
    """Receive time of a message, as record.py stamps it and the replay reads it (citadel/sim.py): the wall clock,
    checked against the monotonic clock for steps, never going backwards."""

    def __init__(self):
        self.off0 = None
        self.last = 0
        self.fallback = 0

    def now(self):
        t, m = time.time_ns(), time.monotonic_ns()
        if self.off0 is None:
            self.off0 = t - m
        if abs((t - m) - self.off0) <= CLOCK_STEP_NS:
            c = t
        else:
            c = m + self.off0
            self.fallback += 1
        if c < self.last:
            c = self.last
        self.last = c
        return c


class Live:
    def __init__(self, params, names, every_s, rules, quiet):
        self.ses = Session([params], names)
        self.clock = Clock()
        self.names = names
        self.every_s = every_s
        self.rules = rules
        self.quiet = quiet
        self.counts = {n: 0 for n in names}
        self.errors = {n: 0 for n in names}
        self.t0 = time.monotonic()
        self.n_lines = 0

    def feed(self, name, rec):
        self.ses.feed(self.clock.now(), name, rec)
        self.flush()

    def flush(self):
        """Print the events the engine has written since the last call, then forget them (bounded memory)."""
        head = self.ses.head
        if head.events:
            for ev in head.events:
                if self.quiet or (self.rules and ev["rule"] not in self.rules):
                    continue
                print(format_line(ev), flush=False)
                self.n_lines += 1
            head.events.clear()
            sys.stdout.flush()
        if len(head.rows) > 10000:       # the offline toxicity test of run.py is not run live
            del head.rows[:]
        if len(head.ff_fills) > 10000:   # nor the film's markout fan
            del head.ff_fills[:]
        if len(self.ses.lines) > 3600:
            del self.ses.lines[:]
        if len(self.ses.tape.candles_1s) > 7200:
            self.ses.tape.trim_candles(3_600_000)

    def summary(self, final=False):
        ses = self.ses
        e = ses.head
        s = engine_stats(e)
        ff, to, sp = s["fastfill"], s["trade_out"], s["smartprovide"]
        el = int(time.monotonic() - self.t0)
        now = self.clock.last or time.time_ns()
        books = " ".join(SHORT.get(n, n) for n in self.names if ses.vens[n].live) or "none yet"
        bid, _, ask, _ = ses.market.top(e.p.fastfill_feeds)
        nbb, nbo = e.sip.nbb, e.sip.nbo
        tox = e.tox
        if e.t_orders is not None and now < e.t_orders:
            tox_s = f"warming up: no order before {e.p.warmup_s:g} s of data (one basis time constant)"
        elif tox.model is None:
            tox_s = f"warming up, {len(tox.y)} of {tox.min_train} outcomes known (no screening yet)"
        else:
            tox_s = (f"live, {tox.n_fits} fits; no principal interest for "
                     f"{s['no_principal_interest_pct']:.1f}% of orders")
        pa = e.profit
        L = [
            f"==== {'FINAL ' if final else ''}{hms(now)[:8]} UTC  live {el // 60}:{el % 60:02d}  messages "
            + "  ".join(f"{SHORT.get(n, n)} {self.counts[n]:,}" for n in self.names),
            f"     books {books}   SIP {_px(nbb)} x {_px(nbo)}   direct {_px(bid)} x {_px(ask)}",
            f"     orders {s['orders']:,}   FastFill {ff['count']:,} = {ff['share_pct']:.1f}%   odd lot "
            f"{ff['odd_lot_pct']:.1f}%   gap kept {_usd(ff['gap_usd_total'])} (median {ff['gap_bps_median']:.2f} bps)",
            f"     trade outs {to['attempts']:,}, {to['success_pct']:.1f}% filled in full or part",
            f"     SmartProvide {sp['count']:,}: filled in market {sp['fill_pct']:.1f}%, internalized after "
            f"{sp['internalized_after_pct']:.1f}%, routed after {sp.get('routed_after_pct', 0.0):.1f}%",
            f"     toxicity analyzer: {tox_s}",
            f"     profit tab {_usd(pa.tab_usd)} on {pa.n:,} fills, {pa.H / 1e9:g} s marks = spread at fill "
            f"{_usd(pa.spread_usd)} + later price {_usd(pa.move_usd)}",
        ]
        for line in L:
            print(line[:W])
        sys.stdout.flush()


def _px(x):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else money(x)


def _usd(x):
    """Dollars: cents from $1, four decimals below (a FastFill gap on 0.001 BTC is a fraction of a cent)."""
    return ("-$" if x < 0 else "$") + ("{:,.2f}".format(abs(x)) if abs(x) >= 1 else "{:.4f}".format(abs(x)))


async def run_venue(live, name, cfg, stop_at):
    backoff = 1.0
    while time.monotonic() < stop_at:
        try:
            async with websockets.connect(cfg["url"], max_size=None, ping_interval=20, open_timeout=15) as ws:
                for sub in cfg["subs"]:
                    await ws.send(json.dumps(sub))
                live.feed(name, {"event": "connected"})        # a (re)connect resets the venue's book
                backoff = 1.0
                while time.monotonic() < stop_at:
                    left = stop_at - time.monotonic()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=None if math.isinf(left) else max(0.05, left))
                    except asyncio.TimeoutError:
                        break
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    if name == "bitstamp":
                        raw = trim_bitstamp(raw)                # the recorder keeps the top 20 levels too
                    live.counts[name] += 1
                    live.feed(name, {"raw": raw})
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                 # reconnect on any network error
            live.errors[name] += 1
            live.feed(name, {"event": "error"})
            print(f"[{name}] error {exc!r}; reconnecting in {backoff:g} s"[:W], file=sys.stderr, flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def run_summary(live, stop_at):
    while True:
        left = stop_at - time.monotonic()
        if left <= 0:
            return
        await asyncio.sleep(min(live.every_s, left))
        if time.monotonic() < stop_at - 0.5:
            live.summary()


async def run_all(live, stop_at):
    tasks = [asyncio.create_task(run_venue(live, n, FEEDS[n], stop_at)) for n in live.names]
    tasks.append(asyncio.create_task(run_summary(live, stop_at)))
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=None, help="stop after this many seconds (default: Ctrl+C)")
    ap.add_argument("--every", type=float, default=10.0, help="seconds between summaries (default 10)")
    ap.add_argument("--preset", default=Params.preset, choices=["sip", "latency"])
    ap.add_argument("--latency-ms", type=float, default=Params.sip_latency_ms)
    ap.add_argument("--round-lot", type=float, default=Params.round_lot_btc)
    ap.add_argument("--venues", default=",".join(FEEDS))
    ap.add_argument("--rules", default="", help="only print these rules, e.g. FASTFILL,TRADE_OUT")
    ap.add_argument("--quiet", action="store_true", help="print the summaries only")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override any parameter of citadel/params.py")
    add_config_argument(ap)
    a = parse_args(ap)
    bad = [n.strip() for n in a.venues.split(",") if n.strip() and n.strip() not in FEEDS]
    if bad:
        raise SystemExit(f"--venues: unknown venue {bad[0]!r}; add it to record.VENUES first")
    names = [v.strip() for v in a.venues.split(",") if v.strip() in FEEDS]
    base = with_overrides(Params(preset=a.preset, sip_latency_ms=a.latency_ms, round_lot_btc=a.round_lot), a.set)
    base = with_overrides(base, [f"venues={','.join(names)}"] if tuple(names) != base.venues else [])
    rules = {r.strip().upper() for r in a.rules.split(",") if r.strip()}
    live = Live(base, names, a.every, rules, a.quiet)
    print("FASTFILL / SMARTPROVIDE LIVE  (rules of SEC order 33-10280, run on public BTC/USD feeds)")
    print("simulation of a wholesaler on public data; no orders are sent anywhere")
    rl = f">= {base.round_lot_btc:g} BTC" if base.preset == "sip" else "of any size"
    print(f"venues {', '.join(names)}"[:W])
    print(f"SIP: best quote {rl} per venue, published {base.sip_latency_ms:g} ms late; SmartProvide reads "
          f"{', '.join(base.smartprovide_feeds)}"[:W])
    print(("Ctrl+C to stop" if a.seconds is None else f"running for {a.seconds:g} s")
          + f"; no order is handled in the first {base.warmup_s:g} s (warm-up)")
    print("-" * 72, flush=True)
    stop_at = time.monotonic() + a.seconds if a.seconds else math.inf
    try:
        asyncio.run(run_all(live, stop_at))
    except KeyboardInterrupt:
        pass
    live.ses.finish()                    # SmartProvide windows and trade outs still open: resolve them
    live.flush()
    print("-" * 72)
    live.summary(final=True)


if __name__ == "__main__":
    main()
