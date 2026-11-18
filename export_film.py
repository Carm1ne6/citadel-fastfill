"""Write the data file of the replay animation (candles, event lines, markout fan, report) from a recording; the
animation itself is not part of this repository.

    python -X utf8 export_film.py --data data/run1 --out out/film_data.json

The same pipeline as run.py (citadel/pipeline.py), so the report lines inside the file are run.py's report.
A simulation of a wholesaler on public data: no orders are sent anywhere.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from citadel.config import add_config_argument, parse_args  # noqa: E402
from citadel.params import Params, with_overrides  # noqa: E402
from citadel.pipeline import run_all, write_outputs  # noqa: E402
from citadel.report import format_line  # noqa: E402
from citadel.toxicity import dealer_pnl  # noqa: E402

FAN_H_S = 30
FAN_STEP_MS = 250
FAN_MAX = 1000
EVENT_RULES = ("FASTFILL", "SMARTPROVIDE", "SP_FILLED", "SP_REEVAL", "TRADE_OUT")


def r2(x):
    return None if x is None else round(float(x), 2)


def candles(tape, width_ms):
    """OHLCV of Coinbase BTC-USD prints in width_ms buckets (receive time); empty buckets repeat the last close."""
    src = tape.candles                        # 5 s buckets
    if not src:
        return []
    keys = sorted(src)
    first = keys[0] // width_ms * width_ms
    last = keys[-1] // width_ms * width_ms
    out = []
    prev_c = src[keys[0]][0]
    t = first
    while t <= last:
        o = h = l = c = None
        v = 0.0
        for k in range(t, t + width_ms, 5000):
            b = src.get(k)
            if b is None:
                continue
            if o is None:
                o, h, l = b[0], b[1], b[2]
            else:
                h = max(h, b[1])
                l = min(l, b[2])
            c = b[3]
            v += b[4]
        if o is None:
            o = h = l = c = prev_c
        out.append([t, r2(o), r2(h), r2(l), r2(c), round(v, 8)])
        prev_c = c
        t += width_ms
    return out


def candles_1s(tape):
    """Coinbase BTC-USD 1-second trade candles [t_ms, o, h, l, c, v]: the same prints as candles_5s (receive time),
    one row per second from the first print to the last. A second without a print repeats the last close with v = 0,
    so the film's 5 s and 15 s candles can form second by second. A 5 s candle is its 1 s candles with prints: same
    open (first), high (max), low (min), close (last) and volume (sum); check_film.py checks it."""
    src = tape.candles_1s
    if not src:
        return []
    t, last = min(src), max(src)
    out = []
    prev_c = None
    while t <= last:
        b = src.get(t)
        if b is None:
            out.append([t, r2(prev_c), r2(prev_c), r2(prev_c), r2(prev_c), 0.0])
        else:
            out.append([t, r2(b[0]), r2(b[1]), r2(b[2]), r2(b[3]), round(b[4], 8)])
            prev_c = b[3]
        t += 1000
    return out


def lines(rows):
    """1 s samples of the SIP NBBO and the direct-feed best bid / offer; leading seconds without quotes dropped."""
    out = [[t, r2(a), r2(b), r2(c), r2(d)] for t, a, b, c, d in rows]
    i = 0
    while i < len(out) and (any(x is None for x in out[i][1:]) or out[i][1] >= out[i][2]
                            or out[i][3] >= out[i][4]):
        i += 1          # the first second(s): not every book has its snapshot yet
    return out[i:]


def fan(head, tape, c1):
    """Dealer markout after each FastFill fill, in bps of the fill price, 0 to 30 s; positive = dealer gained."""
    step = FAN_STEP_MS * 1_000_000
    n = FAN_H_S * 1000 // FAN_STEP_MS + 1
    full = [f for f in head.ff_fills if f[0] + FAN_H_S * 1_000_000_000 <= c1]
    if len(full) > FAN_MAX:
        idx = [round(i * (len(full) - 1) / (FAN_MAX - 1)) for i in range(FAN_MAX)]
        pick = [full[i] for i in idx]
    else:
        pick = full
    paths = []
    for m, side, venue, px, qty, oid in pick:
        vals = []
        for k in range(n):
            mid = tape.mid_at(m + k * step)
            vals.append(None if mid is None else round(dealer_pnl(side, px, mid) / px * 1e4, 3))
        paths.append({"id": oid, "t": m // 1_000_000, "side": side, "venue": venue, "size": round(qty, 8),
                      "fill_px": r2(px), "dealer_bps": vals})
    # n_total: FastFill fills whose 30 s path fits inside the recording (the fan samples them); the rest of the
    # n_fastfill fills came in the last 30 s and are left out (n_excluded_end)
    return {"horizon_s": FAN_H_S, "step_ms": FAN_STEP_MS, "anchor": "FastFill fill", "n_total": len(full),
            "n_fastfill": len(head.ff_fills), "n_excluded_end": len(head.ff_fills) - len(full),
            "paths": paths}


def event_out(ev):
    d = {
        "t": ev["t"], "venue": ev["venue"], "side": ev["side"], "size": round(ev["size"], 8), "rule": ev["rule"],
        "sip_px": r2(ev["sip_px"]), "depth_px": r2(ev["depth_px"]), "gap_usd": round(ev["gap_usd"], 6),
        "gap_bps": round(ev["gap_bps"], 4), "odd_lot": ev["odd_lot"], "principal_interest": ev["principal_interest"],
        "toxicity": round(ev["toxicity"], 4), "outcome": ev["outcome"], "line": format_line(ev), "id": ev["id"],
    }
    for k in ("phase", "fill_px", "gap_top_usd", "post_px", "window_s", "arrival_sip", "px", "filled", "venue_out",
              "print_px", "displayed_vwap", "diff_usd_per_btc", "ref_feed", "post_venue", "sub_share", "action",
              "resolved_ms"):
        if k in ev and ev[k] is not None:
            v = ev[k]
            d[k] = round(v, 6) if isinstance(v, float) else v
    return d


def build(R):
    res = R["res"]
    head, tape = res["head"], res["tape"]
    c1 = res["c_last"]
    evs = sorted((e for e in head.events if e["rule"] in EVENT_RULES), key=lambda e: (e["m"], e["id"]))
    late = [e for e in evs if e["m"] > c1]
    if late:                     # ces.CES.finish decides everything still open at the last message's time
        raise ValueError(f"{len(late)} events after the end of the data")
    tox = R["tox"]
    rnd = (lambda x: None if x is None else round(x, 4))
    return {
        "meta": R["meta"],
        "candles_1s": candles_1s(tape),
        "candles_5s": candles(tape, 5000),
        "candles_15s": candles(tape, 15000),
        "lines_1s": lines(res["lines_1s"]),
        "events": [event_out(e) for e in evs],
        # receive time (ms) of every order, order id i at index i - 1: the number of orders at any time t (the
        # largest event id is not it: orders with no event, such as the last ones of a run, have no line)
        "orders_t": [m // 1_000_000 for m, _ in head.pi_log],
        "fan": fan(head, tape, c1),
        "stats": R["stats"],
        "toxicity": {"auc_oos": rnd(tox["auc_oos"]), "toxic_share_pct": round(tox["toxic_share_pct"] or 0.0, 2),
                     "horizon_s": tox["horizon_s"], "weights": tox["weights"], "auc_in": rnd(tox["auc_in"]),
                     "threshold": tox["threshold"], "loss_rate_test": tox["loss_rate_test"],
                     "by_horizon": tox["by_horizon"], "markouts": tox["markouts"],
                     "walk_forward_fits": tox["walk_forward_fits"], "correlation": tox["correlation"],
                     "single_feature_auc": tox.get("single_feature_auc", []),
                     "test_toxic_rate_flagged": tox["test_toxic_rate_flagged"],
                     "test_toxic_rate_passed": tox["test_toxic_rate_passed"], "label": tox["label"],
                     "profit": tox["profit"]},
        "report_lines": R["report"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, help="recording folder (data/<run>), made by citadel-fastfill record")
    ap.add_argument("--out", required=True)
    ap.add_argument("--preset", default=Params.preset, choices=["sip", "latency"])
    ap.add_argument("--latency-ms", type=float, default=Params.sip_latency_ms)
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--also-run-outputs", action="store_true", help="also write out/<run>/events.jsonl, summary.json")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override any parameter of citadel/params.py")
    add_config_argument(ap)
    a = parse_args(ap)
    t0 = time.time()
    base = with_overrides(Params(preset=a.preset, sip_latency_ms=a.latency_ms), a.set)
    R = run_all(a.data, base=base, max_minutes=a.max_minutes)
    film = build(R)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(film, fh, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, a.out)
    if a.also_run_outputs:
        write_outputs(os.path.join("out", os.path.basename(a.data.rstrip("/\\"))), R)
    print(f"wrote {a.out}: {len(film['events'])} events, {len(film['candles_1s'])} 1 s / "
          f"{len(film['candles_5s'])} 5 s / {len(film['candles_15s'])} 15 s candles, "
          f"{len(film['fan']['paths'])} fan paths, provisional={film['meta']['provisional']} "
          f"({time.time() - t0:.1f} s)")


if __name__ == "__main__":
    main()
