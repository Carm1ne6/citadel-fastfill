"""Markouts of YOUR OWN fills against recorded public order books: did the price move for you or against you 1, 5
and 30 s after each fill? A fill that keeps losing after it is done is adverse selection: whoever traded with you
knew where the price was going.

    citadel-fastfill markout --fills my_fills.csv --data data/run1
    python -X utf8 tools/markout_my_fills.py --fills my_fills.csv --data data/run1 --per-fill out/markouts.csv

The idea is the profit analyzer of US 7,587,347 B2 (RULES.md section 6), turned to your side of the trade: "By logging
the bid/offer spread at the time of order filling for each equity trade executed, a running tab of profit (or loss)
can be kept" (col. 14:3-5), and "profit (or loss) on a trade can only be determined after a trade has been executed"
(col. 13:60-62). The markout is our version of that running tab: the horizons and the split are ours, the edge at
the fill (your price against the mid at the fill) plus the later move (the mid at t + h against the mid at the fill).
If you provide liquidity, a fill whose markout is zero or negative came from what the patent calls a toxic order: one
that would give the broker-dealer who accepts it "no profit, reduced profit and/or a loss" (col. 4:1-3).

The fills CSV has a header row; column names are not case sensitive and other columns are ignored:
    time    ISO 8601 UTC (2026-11-18T03:20:01.250Z; no zone = UTC) or epoch seconds, ms, us or ns
    side    buy or sell: YOUR side of the fill (b / s also work)
    price   your fill price, USD
    size    your fill size, BTC
    venue   optional: coinbase, kraken, bitstamp or gemini; without it --venue is used

Markout per BTC at horizon h: mid(t + h) - price for a buy, price - mid(t + h) for a sell; positive = the price moved
your way. mid is the venue's own mid as last received at or before that time, on the recording's clock (record.py
stamps every message with this computer's wall clock), so give your fill times in UTC and keep the computer's clock
synchronised (--shift-ms moves your fill times onto the recording's clock if you know the offset). Fills outside
the recording, or too close to its end for a horizon, are counted and left out.

Reads files only: nothing is sent anywhere.
"""
import argparse
import bisect
import csv
import datetime as _dt
import json
import os
import sys
from array import array
from decimal import Decimal, InvalidOperation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from citadel.sim import _stream  # noqa: E402  (the replay's clock rule: wall clock, checked against monotonic)
from citadel.venues import VENUE_CLASSES, make_venue  # noqa: E402

HORIZONS = (1.0, 5.0, 30.0)
EPS = 1e-9
COLUMNS = {
    "time": ("time", "timestamp", "ts", "datetime", "date", "time_utc", "created_at", "trade_time"),
    "side": ("side", "direction"),
    "price": ("price", "px", "fill_price"),
    "size": ("size", "qty", "quantity", "amount", "base_size"),
    "venue": ("venue", "exchange"),
}
SIDES = {"buy": "BUY", "b": "BUY", "bought": "BUY", "sell": "SELL", "s": "SELL", "sold": "SELL"}
EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


def parse_time(text):
    """A fill time as integer ns since the epoch (UTC): ISO 8601 or epoch s / ms / us / ns by magnitude."""
    s = str(text).strip()
    try:
        x = Decimal(s)                     # exact: a float cannot hold epoch nanoseconds
    except InvalidOperation:
        x = None
    if x is not None and x.is_finite():
        a = abs(x)
        scale = 1 if a >= 10 ** 17 else 1_000 if a >= 10 ** 14 else 1_000_000 if a >= 10 ** 11 else 1_000_000_000
        return int((x * scale).to_integral_value())
    t = s.replace(" ", "T", 1)
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    frac_ns = 0
    if "." in t:                           # keep every digit of the fraction (fromisoformat stops at 6 before 3.11)
        head, rest = t.split(".", 1)
        digits = ""
        while rest and rest[0].isdigit():
            digits, rest = digits + rest[0], rest[1:]
        frac_ns = int((digits + "000000000")[:9]) if digits else 0
        t = head + rest
    try:
        dt = _dt.datetime.fromisoformat(t)
    except ValueError:
        raise ValueError(f"time {text!r}: use ISO 8601 (2026-11-18T03:20:01.250Z) or epoch seconds / ms / ns")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return (dt - EPOCH) // _dt.timedelta(seconds=1) * 1_000_000_000 + frac_ns


def read_fills(path, default_venue=None):
    """The fills of a CSV as dicts {row, t, side, price, size, venue}."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise SystemExit(f"{path}: empty file, or no header row")
        lower = {name.strip().lower(): name for name in reader.fieldnames}
        col = {}
        for key, names in COLUMNS.items():
            col[key] = next((lower[n] for n in names if n in lower), None)
        missing = [k for k in ("time", "side", "price", "size") if col[k] is None]
        if missing:
            raise SystemExit(f"{path}: no column for {', '.join(missing)}; the header has "
                             f"{', '.join(reader.fieldnames)} (see the docstring of {os.path.basename(__file__)})")
        fills = []
        for i, row in enumerate(reader, start=2):
            if not any((v or "").strip() for v in row.values()):
                continue
            try:
                side = SIDES[row[col["side"]].strip().lower()]
                venue = (row[col["venue"]].strip().lower() if col["venue"] and row[col["venue"]] else None) \
                    or default_venue
                fills.append({"row": i, "t": parse_time(row[col["time"]]), "side": side,
                              "price": float(row[col["price"]]), "size": float(row[col["size"]]), "venue": venue})
            except KeyError:
                raise SystemExit(f"{path} line {i}: side {row[col['side']]!r} is not buy or sell")
            except ValueError as exc:
                raise SystemExit(f"{path} line {i}: {exc}")
    return fills


class MidSeries:
    """A venue's mid at every book change, as received; at(t) = the last one at or before t (None: no valid book)."""

    def __init__(self):
        self.t = array("q")
        self.mid = array("d")
        self.end = None          # receive time of the venue's last message: no mid is known after it

    def add(self, c, mid):
        if self.t and c == self.t[-1]:
            self.mid[-1] = mid
        else:
            self.t.append(c)
            self.mid.append(mid)

    def at(self, c):
        i = bisect.bisect_right(self.t, c) - 1
        if i < 0:
            return None
        m = self.mid[i]
        return None if m != m else m

    @property
    def first(self):
        return self.t[0] if self.t else None


def mid_series(data_dir, venue):
    """Replay one venue's recording and keep its mid after every book change (NaN while its book is not valid)."""
    path = os.path.join(data_dir, f"{venue}.jsonl.gz")
    v = make_venue(venue)
    s = MidSeries()
    for c, _, rec in _stream(venue, path, {}):
        s.end = c
        changed, _ = v.on_record(rec)
        if not changed:
            continue
        b, _ = v.bids.best()
        a, _ = v.asks.best()
        s.add(c, 0.5 * (a + b) if (v.live and a is not None and b is not None) else float("nan"))
    return s


def markouts(fills, series, horizons=HORIZONS):
    """Per fill and horizon: the markout per BTC, its split, and in bps of the fill price."""
    out = []
    for f in fills:
        s = series.get(f["venue"])
        m0 = s.at(f["t"]) if s is not None else None
        sgn = 1.0 if f["side"] == "BUY" else -1.0
        r = dict(f, mid_at_fill=m0, h={})
        for h in horizons:
            th = f["t"] + int(round(h * 1e9))
            if s is None or m0 is None or s.end is None or th > s.end:
                r["h"][h] = None
                continue
            mh = s.at(th)
            if mh is None:
                r["h"][h] = None
                continue
            edge = sgn * (m0 - f["price"])
            move = sgn * (mh - m0)
            total = edge + move
            r["h"][h] = {"mid": mh, "edge": edge, "move": move, "total": total, "bps": total / f["price"] * 1e4,
                         "usd": total * f["size"], "toxic": total <= EPS}
        out.append(r)
    return out


def summarize(rows, horizons=HORIZONS, key=None):
    """Per horizon (and per group when key is given): fills, mean and median bps, losing share, USD split."""
    groups = {}
    for r in rows:
        groups.setdefault(key(r) if key else "all", []).append(r)
    res = {}
    for g, rs in groups.items():
        res[g] = {}
        for h in horizons:
            v = [r["h"][h] for r in rs if r["h"].get(h)]
            if not v:
                res[g][h] = {"n": 0, "left_out": len(rs)}
                continue
            bps = sorted(x["bps"] for x in v)
            n = len(v)
            med = bps[n // 2] if n % 2 else 0.5 * (bps[n // 2 - 1] + bps[n // 2])
            sz = [r["size"] for r in rs if r["h"].get(h)]
            res[g][h] = {"n": n, "left_out": len(rs) - n, "mean_bps": sum(bps) / n, "median_bps": med,
                         "losing_pct": 100.0 * sum(1 for x in v if x["toxic"]) / n,
                         "usd": sum(x["usd"] for x in v),
                         "edge_usd": sum(x["edge"] * q for x, q in zip(v, sz)),
                         "move_usd": sum(x["move"] * q for x, q in zip(v, sz))}
    return res


def report(res, horizons, title):
    L = [title, f"  {'horizon':>7}{'fills':>7}{'mean bps':>10}{'median bps':>12}{'losing':>9}{'P&L USD':>11}"
                f"{'= edge at fill':>16}{'+ later move':>14}"]
    for h in horizons:
        x = res[h]
        if not x["n"]:
            L.append(f"  {h:>5g} s{0:>7}   (no fill fits this horizon inside the recording)")
            continue
        L.append(f"  {h:>5g} s{x['n']:>7}{x['mean_bps']:>+10.2f}{x['median_bps']:>+12.2f}{x['losing_pct']:>8.1f}%"
                 f"{x['usd']:>+11.2f}{x['edge_usd']:>+16.2f}{x['move_usd']:>+14.2f}")
    return L


def main(argv=None):
    ap = argparse.ArgumentParser(prog="markout_my_fills", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fills", required=True, help="CSV of your fills (time, side, price, size[, venue])")
    ap.add_argument("--data", required=True, help="a recording folder of record.py (data/<run>)")
    ap.add_argument("--venue", default=None, help="the venue of fills without a venue column (default: coinbase "
                                                  "if recorded, else the first recorded venue)")
    ap.add_argument("--horizons", default="1,5,30", help="seconds, comma-separated (default 1,5,30)")
    ap.add_argument("--shift-ms", type=float, default=0.0,
                    help="add this to every fill time to put it on the recording's clock (default 0)")
    ap.add_argument("--per-fill", default=None, metavar="CSV", help="also write every fill's markouts to this file")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    a = ap.parse_args(argv)
    horizons = tuple(float(x) for x in a.horizons.split(",") if x.strip())
    recorded = [n for n in VENUE_CLASSES if os.path.exists(os.path.join(a.data, f"{n}.jsonl.gz"))]
    if not recorded:
        raise SystemExit(f"{a.data}: no recording (record one: citadel-fastfill record --minutes 30 --out {a.data})")
    default = a.venue or ("coinbase" if "coinbase" in recorded else recorded[0])
    if not os.path.isfile(a.fills):
        raise SystemExit(f"{a.fills}: no such file (a CSV of your fills: time, side, price, size[, venue])")
    fills = read_fills(a.fills, default)
    for f in fills:
        f["t"] += int(round(a.shift_ms * 1e6))
    if not fills:
        raise SystemExit(f"{a.fills}: no fills")
    unknown = sorted({f["venue"] for f in fills if f["venue"] not in recorded})
    if unknown:
        raise SystemExit(f"{a.fills}: venue {unknown[0]!r} is not in {a.data} (recorded: {', '.join(recorded)})")
    series = {v: mid_series(a.data, v) for v in sorted({f["venue"] for f in fills})}
    rows = markouts(fills, series, horizons)
    allr = summarize(rows, horizons)["all"]
    by = summarize(rows, horizons, key=lambda r: f"{r['venue']} {r['side'].lower()}")
    if a.per_fill:
        os.makedirs(os.path.dirname(os.path.abspath(a.per_fill)), exist_ok=True)
        with open(a.per_fill, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["row", "time_ns", "venue", "side", "price", "size", "mid_at_fill"] +
                       [f"markout_{h:g}s_bps" for h in horizons] + [f"markout_{h:g}s_usd" for h in horizons])
            for r in rows:
                w.writerow([r["row"], r["t"], r["venue"], r["side"], r["price"], r["size"], r["mid_at_fill"]] +
                           [round(r["h"][h]["bps"], 4) if r["h"][h] else "" for h in horizons] +
                           [round(r["h"][h]["usd"], 6) if r["h"][h] else "" for h in horizons])
    if a.json:
        def js(d):
            return {f"{h:g}s": v for h, v in d.items()}
        print(json.dumps({"fills": len(rows), "data": a.data, "all": js(allr),
                          "by_venue_side": {g: js(v) for g, v in by.items()}}, indent=1))
        return 0
    print(f"{len(rows)} fills from {a.fills}, books from {a.data} ({', '.join(series)}); markout = the price move "
          f"for you after the fill, + = your way")
    for line in report(allr, horizons, "all fills"):
        print(line)
    if len(by) > 1:
        for g, res in sorted(by.items()):
            print()
            for line in report(res, horizons, g):
                print(line)
    print()
    print("losing = no profit or a loss at that horizon (our horizons). If you provide liquidity, the orders behind")
    print("those fills are what US 7,587,347 calls toxic (\"no profit, reduced profit and/or a loss\", col. 4:1-3);")
    print("if most of your fills lose at 5 s, the flow that hits you is informed (adverse selection).")
    if a.per_fill:
        print(f"per fill: {a.per_fill}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
