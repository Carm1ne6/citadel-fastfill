"""Check a film_data.json (the replay animation's data file, written by export_film.py) against its contract.

    python -X utf8 check_film.py out/film_data.json
"""
import json
import os
import sys

RULES = {"FASTFILL", "SMARTPROVIDE", "SP_FILLED", "SP_REEVAL", "TRADE_OUT"}
META = {"symbol": str, "chart_symbol": str, "venues": list, "data_dir": str, "start_utc": str, "end_utc": str,
        "minutes": float, "sip_latency_ms": float, "round_lot_btc": float, "orders_total": int, "provisional": bool}
EVENT = {"t": int, "venue": str, "side": str, "size": float, "rule": str, "sip_px": float, "depth_px": float,
         "gap_usd": float, "gap_bps": float, "odd_lot": bool, "principal_interest": bool, "toxicity": float,
         "outcome": str, "line": str}
FF = ["count", "share_pct", "odd_lot_pct", "gap_usd_total", "gap_usd_per_order", "gap_bps_median", "gap_bps_p90",
      "small_worse_pct", "large_better_pct"]
SEC = {"fastfill_orders": 2700000, "odd_lot_pct": 20, "trade_out_share_pct_max": 6.9, "trade_out_success_pct": 55,
       "smartprovide_orders": 690000, "smartprovide_internalized_after": 490000, "smartprovide_fill_pct": 18,
       "paid_usd": 22665268}


def is_num(x, typ):
    if typ is float:
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    if typ is int:
        return isinstance(x, int) and not isinstance(x, bool)
    return isinstance(x, typ)


def check_candles_1s(d):
    """candles_1s (optional): one row a second, a second without prints repeats the last close with v = 0, and the
    1 s candles with prints rebuild every 5 s candle exactly (open, high, low, close, volume)."""
    err = []
    c1 = d.get("candles_1s")
    if c1 is None:
        return err
    if not c1 or any(len(r) != 6 for r in c1):
        return ["candles_1s shape"]
    if any(c1[i + 1][0] - c1[i][0] != 1000 for i in range(len(c1) - 1)) or c1[0][0] % 1000:
        err.append("candles_1s not one row per second")
    for i, (t, o, h, l, c, v) in enumerate(c1):
        if not (l <= min(o, c) and h >= max(o, c)) or v < 0:
            err.append(f"candles_1s[{i}] ohlc")
            break
        if v == 0 and (i == 0 or not (o == h == l == c == c1[i - 1][4])):
            err.append(f"candles_1s[{i}] empty second does not repeat the last close")
            break
    by5 = {}
    for r in c1:
        if r[5] > 0:
            by5.setdefault(r[0] // 5000 * 5000, []).append(r)
    bad = 0
    for t, o, h, l, c, v in d["candles_5s"]:
        rows = by5.get(t)
        if not rows:
            continue
        if (rows[0][1] != o or max(r[2] for r in rows) != h or min(r[3] for r in rows) != l or rows[-1][4] != c
                or abs(sum(r[5] for r in rows) - v) > 1e-6):
            bad += 1
    if bad:
        err.append(f"candles_1s do not rebuild {bad} candles_5s")
    if set(by5) - {r[0] for r in d["candles_5s"]}:
        err.append("candles_1s has prints outside candles_5s")
    return err


def check(d):
    err = []
    for k, typ in META.items():
        if not is_num(d["meta"].get(k), typ):
            err.append(f"meta.{k}: {d['meta'].get(k)!r}")
    err += check_candles_1s(d)
    for name in ("candles_5s", "candles_15s"):
        c = d[name]
        if not c or any(len(r) != 6 for r in c):
            err.append(f"{name} shape")
        if any(c[i][0] >= c[i + 1][0] for i in range(len(c) - 1)):
            err.append(f"{name} not increasing")
        if any(not (r[3] <= min(r[1], r[4]) and r[2] >= max(r[1], r[4])) for r in c):
            err.append(f"{name} ohlc")
    ln = d["lines_1s"]
    if not ln or any(len(r) != 5 for r in ln):
        err.append("lines_1s shape")
    crossed = sum(1 for r in ln if None not in r[1:] and r[1] >= r[2])
    ev = d["events"]
    for i, e in enumerate(ev):
        for k, typ in EVENT.items():
            if not is_num(e.get(k), typ):
                err.append(f"events[{i}].{k}: {e.get(k)!r}")
                break
        if e["rule"] not in RULES or e["side"] not in ("BUY", "SELL"):
            err.append(f"events[{i}] rule/side")
        if len(e["line"]) > 96 or "—" in e["line"] or not e["line"].isascii():
            err.append(f"events[{i}].line: {e['line']!r}")
    if any(ev[i]["t"] > ev[i + 1]["t"] for i in range(len(ev) - 1)):
        err.append("events not time-sorted")
    ot = d.get("orders_t")
    if ot is not None:
        if len(ot) != d["stats"]["orders"] or any(ot[i] > ot[i + 1] for i in range(len(ot) - 1)):
            err.append("orders_t: one non-decreasing time per order")
        elif ev and (max(e["id"] for e in ev) > len(ot) or ev[0]["t"] < ot[0]):
            err.append("orders_t does not cover the events")
    f = d["fan"]
    if f["horizon_s"] != 30 or f["step_ms"] != 250 or f["anchor"] != "FastFill fill":
        err.append("fan header")
    if len(f["paths"]) > 1000 or any(len(p["dealer_bps"]) != 121 for p in f["paths"]):
        err.append("fan paths")
    st = d["stats"]
    for k in FF:
        if not is_num(st["fastfill"].get(k), float):
            err.append(f"stats.fastfill.{k}")
    for k in ("share_of_ff_shares_pct", "success_pct"):
        if not is_num(st["trade_out"].get(k), float):
            err.append(f"stats.trade_out.{k}")
    for k in ("count", "fill_pct", "internalized_after_pct", "worse_than_immediate_pct"):
        if not is_num(st["smartprovide"].get(k), float):
            err.append(f"stats.smartprovide.{k}")
    for s in st["sensitivity"]:
        if not all(is_num(s.get(k), float) for k in ("sip_latency_ms", "fastfill_share_pct", "gap_usd_total")):
            err.append("stats.sensitivity row")
    for k, v in SEC.items():
        if st["sec"].get(k) != v:
            err.append(f"stats.sec.{k}")
    t = d["toxicity"]
    if not all(k in t for k in ("auc_oos", "toxic_share_pct", "horizon_s", "weights")):
        err.append("toxicity keys")
    if any(len(x) > 96 or "—" in x for x in d["report_lines"]):
        err.append("report_lines too long or em dash")
    return err, crossed


def main():
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print("usage: citadel-fastfill check-film FILM_DATA.json\n\n" + __doc__.split("\n\n")[0])
        return 0 if len(sys.argv) == 2 else 2
    path = sys.argv[1]
    if not os.path.isfile(path):
        raise SystemExit(f"{path}: no such file (write one with citadel-fastfill export-film)")
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    err, crossed = check(d)
    n = {}
    for e in d["events"]:
        n[e["rule"]] = n.get(e["rule"], 0) + 1
    c1 = d.get("candles_1s") or []
    print(f"{path}: {len(d['events'])} events {n}, {len(d['lines_1s'])} lines ({crossed} crossed SIP), "
          f"{len(c1)} 1 s candles ({sum(1 for r in c1 if r[5] > 0)} with prints), "
          f"{len(d['fan']['paths'])} fan paths, provisional={d['meta']['provisional']}")
    for e in err[:30]:
        print("  ERROR", e)
    print("OK" if not err else f"{len(err)} errors")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
