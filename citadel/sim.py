"""Replay: merge the four venue recordings by receive time, keep the books, feed every engine.

Orders. Each public taker order on the four venues is one marketable order of its aggressor side: its fills on one
venue are grouped (venues.py names the taker order of each fill), its size is the sum of the fills, and it arrives
when its last fill is received. A group is complete when the next message received, from any venue, is not one of
its fills; the order is then handled with the books as they were at its last fill (no later message has been applied
yet). Fills that another message interrupts count as two or more orders (41 of the 2,750 multi-fill taker orders of
run_final, 47 extra orders; NOTES.md). Every fill still prints on its own: candles, and the prints that can fill a
displayed SmartProvide order.

Clock. The recorder stores a wall-clock time `t` and a monotonic time `m`. On the Windows machine that made the
recordings `m` ticks every 15.6 ms, too coarse for a 5 ms SIP delay, so the replay clock is `t`, checked against `m`:
if t - m ever moves more than CLOCK_STEP_NS away from its first value (a wall-clock step), that record uses
m + offset. `t` is stored in 100 ns units but advances in steps of about 1 ms (its effective resolution, NOTES.md);
messages with the same `t` keep the order of the venue list.
"""
import heapq
import math
import os
from array import array
from collections import deque

from .depth import Market
from .venues import iter_records, make_venue

CLOCK_STEP_NS = 50_000_000


def _raw_mid(v, min_level=0.0):
    b, _ = v.bids.best_min(min_level)
    a, _ = v.asks.best_min(min_level)
    if b is None or a is None:
        return None
    return 0.5 * (a + b)


class Basis:
    """Each venue's slow price difference to the reference venue (time-weighted EMA, past data only), from mids of
    levels of at least one share-equivalent."""

    def __init__(self, vens, ref, tau_s, on=True, min_level=0.0):
        self.vens = vens
        self.ref = vens.get(ref)
        self.tau = tau_s * 1e9
        self.on = on and self.ref is not None
        self.min_level = min_level
        self.t = {}
        self.ema = {}

    def update(self, c, changed):
        if not self.on or not self.ref.live:
            return
        rm = _raw_mid(self.ref, self.min_level)
        if rm is None:
            return
        targets = [v for v in self.vens.values() if v is not self.ref] if changed is self.ref else [changed]
        for v in targets:
            if v is self.ref or not v.live:
                continue
            vm = _raw_mid(v, self.min_level)
            if vm is None:
                continue
            diff = vm - rm
            t0 = self.t.get(v.name)
            if t0 is None:
                ema = diff
            else:
                a = 1.0 - math.exp(-(c - t0) / self.tau)
                ema = self.ema[v.name] + a * (diff - self.ema[v.name])
            self.ema[v.name] = ema
            self.t[v.name] = c
            v.basis = round(ema, 2)          # whole cents keep shifted prices on the $0.01 grid


class Tape:
    """Shared history: the direct-feed mid on a 50 ms grid, taker orders per venue, Coinbase candles (5 s and 1 s
    buckets of the prints, by receive time)."""

    def __init__(self, step_ms=50):
        self.step = int(step_ms * 1e6)
        self.m0 = None
        self.mids = array("d")
        self.cur_mid = None
        self.prints = {}           # venue -> receive times of its taker orders ("the frequency of order submission")
        self.candles = {}          # bucket start (ms) -> [o, h, l, c, v], 5 s buckets
        self.candles_1s = {}       # bucket start (ms) -> [o, h, l, c, v], 1 s buckets

    def advance(self, m):
        if self.m0 is None:
            self.m0 = m
        g = (m - self.m0) // self.step
        val = self.cur_mid if self.cur_mid is not None else float("nan")
        mids = self.mids
        while len(mids) <= g:
            mids.append(val)

    def mid_at(self, m):
        if self.m0 is None:
            return None
        g = (m - self.m0) // self.step
        if g < 0:
            return None
        if g >= len(self.mids):
            return None
        v = self.mids[g]
        return None if v != v else v

    def on_order(self, m, venue):
        """One taker order of `venue` received at m (counted in print_rate)."""
        dq = self.prints.get(venue)
        if dq is None:
            dq = self.prints[venue] = deque()
        dq.append(m)

    def on_print(self, m, venue, px, size, candle=False):
        if candle:
            ms = m // 1_000_000
            for book, b in ((self.candles, ms // 5000 * 5000), (self.candles_1s, ms // 1000 * 1000)):
                c = book.get(b)
                if c is None:
                    book[b] = [px, px, px, px, size]
                else:
                    if px > c[1]:
                        c[1] = px
                    if px < c[2]:
                        c[2] = px
                    c[3] = px
                    c[4] += size

    def trim_candles(self, keep_ms):
        """Forget candles that start more than keep_ms before the newest one (live sessions run for hours)."""
        for book in (self.candles, self.candles_1s):
            if book:
                lim = max(book) - keep_ms
                for k in [k for k in book if k < lim]:
                    del book[k]

    def print_rate(self, venue, m, window_s=10.0):
        """Taker orders a second on `venue` over the last window_s, the current one included."""
        dq = self.prints.get(venue)
        if not dq:
            return 0.0
        lim = m - int(window_s * 1e9)
        while dq and dq[0] < lim:
            dq.popleft()
        return len(dq) / window_s


def _stream(name, path, clock_stats):
    off0 = None
    last = None
    for rec in iter_records(path):
        t = rec.get("t")
        m = rec.get("m")
        if t is None or m is None:
            continue
        if off0 is None:
            off0 = t - m
        if abs((t - m) - off0) <= CLOCK_STEP_NS:
            c = t
        else:
            c = m + off0
            clock_stats["fallback"] = clock_stats.get("fallback", 0) + 1
        if last is not None and c < last:
            c = last
        last = c
        yield c, name, rec


class _Taker:
    """The fills of one taker order received so far (sim.Session groups them into one marketable order)."""
    __slots__ = ("venue", "key", "side", "size", "worst", "c", "n")

    def __init__(self, venue, key, side):
        self.venue, self.key, self.side = venue, key, side
        self.size = 0.0
        self.worst = None
        self.c = None
        self.n = 0

    def add(self, c, px, size):
        self.size += size
        if self.worst is None or (px > self.worst if self.side == "BUY" else px < self.worst):
            self.worst = px                  # the price furthest through the book: it bounds the limit price
        self.c = c
        self.n += 1


# Every engine of a session reads the same books, basis and crossed-quote rule (one Market, one Basis): a sensitivity
# engine cannot change them, so they must equal the headline's.
SHARED_PARAMS = ("venues", "basis_adjust", "basis_ref", "basis_tau_s", "clip_crossed", "min_level_btc")


class Session:
    """The books, the shared market view, the tape and every engine of one run, fed one received message at a time.

    simulate() feeds it recorded messages; live.py feeds it the same public messages as they arrive. Both go through
    feed(), so a live session prints exactly what a replay of the same messages would print."""

    def __init__(self, engines_params, names, headline=0, sample_s=1.0):
        from .ces import CES

        self.names = list(names)
        self.vens = {n: make_venue(n) for n in self.names}
        hp = engines_params[headline]
        for p in engines_params:
            for k in SHARED_PARAMS:
                if getattr(p, k) != getattr(hp, k):
                    raise ValueError(f"every engine of a session shares the market: {k} = {getattr(p, k)!r} differs "
                                     f"from the headline's {getattr(hp, k)!r}; run it as its own session")
        self.market = Market(self.vens, ref=hp.basis_ref, clip=hp.clip_crossed, min_level=hp.min_level_btc)
        self.tape = Tape()
        self.engines = [CES(p, self.market, self.tape, record=(i == headline)) for i, p in enumerate(engines_params)]
        self.head = self.engines[headline]
        self.basis = Basis(self.vens, hp.basis_ref, hp.basis_tau_s, on=hp.basis_adjust, min_level=hp.min_level_btc)
        self.min_sizes = sorted(set(e.min_size for e in self.engines))
        self.feeds_all = tuple(self.names)
        self.lines = []
        self.step_s = int(sample_s * 1e9)
        self.next_s = None
        self.c_first = self.c_last = None
        self.n_rec = 0
        self.pending = None              # the taker order whose fills are arriving
        self.n_takers = 0
        self.n_fills = 0

    def _flush(self):
        """The pending taker order is complete: hand it to every engine as one marketable order, at its last fill."""
        g = self.pending
        if g is None:
            return
        self.pending = None
        self.n_takers += 1
        self.tape.on_order(g.c, g.venue)
        for e in self.engines:
            e.on_order(g.c, g.venue, g.side, g.worst, g.size, g.n)

    def feed(self, c, name, rec):
        """Apply one recorded or live message of venue `name`, received at time c (ns, non-decreasing)."""
        ven = self.vens[name]
        msg = ven.decode(rec)
        pre = None
        g = self.pending
        if g is not None:
            if g.venue == name and msg is not None and ven.is_trade(msg):
                pre = ven.apply(rec, msg)              # a trade message never changes the book: read it first
                tr = pre[1]
                if not tr or tr[0][3] != g.key or tr[0][0] != g.side:
                    self._flush()
            else:
                self._flush()                          # not one of its fills: the taker order is complete
        if self.c_first is None:
            self.c_first = c
            self.next_s = (c // self.step_s + 1) * self.step_s
            for e in self.engines:
                e.t_orders = c + int(e.p.warmup_s * 1e9)
        self.c_last = c
        self.n_rec += 1
        market, tape, head, engines = self.market, self.tape, self.head, self.engines
        tape.advance(c)
        while self.next_s <= c:
            head.advance(self.next_s)
            bid, _, ask, _ = market.top(head.p.fastfill_feeds)
            self.lines.append([self.next_s // 1_000_000, head.sip.nbb, head.sip.nbo, bid, ask])
            self.next_s += self.step_s
        for e in engines:
            e.advance(c)
        vens = self.vens
        changed, trades = pre if pre is not None else ven.apply(rec, msg)
        if changed:
            self.basis.update(c, ven)
            # each venue's quote as the SIP receives it: best level of at least one round lot (0 = any size),
            # basis-adjusted, not locking or crossing the reference venue. A reference change re-prices them all.
            upd = list(vens.values()) if (market.clip and name == market.ref) else [ven]
            for uv in upd:
                quotes = {}
                for ms in self.min_sizes:
                    if uv.live:
                        b, bs = market.vbest(uv, "bid", ms)
                        a, as_ = market.vbest(uv, "ask", ms)
                        quotes[ms] = (b, bs, a, as_)
                    else:
                        quotes[ms] = (None, 0.0, None, 0.0)
                for e in engines:
                    e.on_venue_update(c, uv.name, quotes[e.min_size])
            for e in engines:
                e.on_book(c, name)                     # trade outs that reached this venue are read on its book
            tape.cur_mid = market.mid(self.feeds_all)
        for side, px, size, key in trades:
            self.n_fills += 1
            tape.on_print(c, name, px, size, candle=(name == "coinbase"))
            for e in engines:
                e.on_print(c, name, side, px, size)    # a fill can fill a displayed SmartProvide order (SEC 37)
            g = self.pending
            if g is not None and (g.venue != name or g.key != key or g.side != side):
                self._flush()                          # one message can carry the fills of several taker orders
                g = None
            if g is None:
                g = self.pending = _Taker(name, key, side)
            g.add(c, px, size)

    def finish(self):
        """End of data: the last taker order, then what is left open, with the last known books."""
        self._flush()
        for e in self.engines:
            e.finish(self.c_last)

    def result(self, clock_stats=None):
        return {
            "engines": self.engines, "head": self.head, "tape": self.tape, "lines_1s": self.lines,
            "venues": self.vens, "names": self.names, "c_first": self.c_first, "c_last": self.c_last,
            "n_records": self.n_rec, "clock": clock_stats or {},
            "basis": {n: v.basis for n, v in self.vens.items()},
            "fills": self.n_fills, "taker_orders": self.n_takers,
        }


def simulate(data_dir, engines_params, venues=("coinbase", "kraken", "bitstamp", "gemini"), headline=0,
             max_minutes=None, sample_s=1.0, progress=False):
    """Run every engine in one pass over the data. Returns a dict with the engines, tape, 1 s lines and run info."""
    names = [n for n in venues if os.path.exists(os.path.join(data_dir, f"{n}.jsonl.gz"))]
    ses = Session(engines_params, names, headline=headline, sample_s=sample_s)
    clock_stats = {}
    streams = [_stream(n, os.path.join(data_dir, f"{n}.jsonl.gz"), clock_stats) for n in names]
    merged = heapq.merge(*streams, key=lambda r: r[0])
    stop_at = None
    first = True
    for c, name, rec in merged:
        if first:
            first = False
            if max_minutes:
                stop_at = c + int(max_minutes * 60e9)
        if stop_at is not None and c > stop_at:
            break
        ses.feed(c, name, rec)
        if progress and ses.n_rec % 200000 == 0:
            print(f"  {ses.n_rec} records, {(c - ses.c_first) / 60e9:.1f} min", flush=True)
    ses.finish()
    return ses.result(clock_stats)
