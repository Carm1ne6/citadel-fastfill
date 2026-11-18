"""One call that runs everything for a recording: the headline engine, the sensitivity grids, the toxicity test,
the statistics and the report. run.py and export_film.py both use it, so the report and the film always agree."""
import datetime as _dt
import json
import os
from dataclasses import replace

import numpy as np

from .params import Params, SEC_NUMBERS
from .report import build_report, engine_stats, half_stats
from .sim import simulate
from .toxicity import FEATURE_NAMES, NOT_AVAILABLE, PARAMETERS, RESPONSES, dealer_pnl, evaluate

LATENCY_GRID = (0.5, 5.0, 50.0, 250.0, 1000.0)
# 0.0155 BTC = 100 shares at the SEC order's own example price ($10.00, SEC 11, 13, fn 11) at the recording's price
ROUND_LOT_GRID = (0.01, 0.0155, 0.05, 0.10)
TRADE_OUT_GRID = (5.0, 50.0, 250.0)
TOX_GRID = (0.5, 0.6, 0.7, 0.8, 0.9)
FEED_VARIANTS = ("coinbase", "kraken")


def _iso(c_ns):
    return _dt.datetime.fromtimestamp(c_ns / 1e9, tz=_dt.timezone.utc).isoformat(timespec="milliseconds") \
        .replace("+00:00", "Z")


def run_finished(data_dir):
    log = data_dir.rstrip("/\\") + ".log"
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            return any(line.startswith("done") for line in fh)
    except OSError:
        return False


def _feed(p):
    return ",".join(p.smartprovide_feeds)


def _with_feed(p, feed):
    return replace(p, smartprovide_feeds=(feed,), sp_post_venue=feed)


def engine_grid(base, grid=LATENCY_GRID):
    """The headline engine first, then every sensitivity engine: [(Params, kind)]. Every engine shares the
    headline's market settings (sim.SHARED_PARAMS); each varies one thing."""
    out = [(base, "headline")]
    seen = {(base.preset, base.sip_latency_ms)}
    for preset in ("sip", "latency"):
        for lat in grid:
            if (preset, lat) in seen:
                continue
            seen.add((preset, lat))
            out.append((replace(base, preset=preset, sip_latency_ms=lat), "latency"))
    if base.preset == "sip":
        for rl in ROUND_LOT_GRID:
            if abs(rl - base.round_lot_btc) > 1e-12:
                out.append((replace(base, round_lot_btc=rl), "round_lot"))
    for tl in TRADE_OUT_GRID:
        if abs(tl - base.trade_out_latency_ms) > 1e-12:
            out.append((replace(base, trade_out_latency_ms=tl), "trade_out"))
    for th in TOX_GRID:
        if abs(th - base.tox_threshold) > 1e-12:
            out.append((replace(base, tox_threshold=th), "tox"))
    other = "latency" if base.preset == "sip" else "sip"
    for preset in (base.preset, other):
        for feed in FEED_VARIANTS:
            if (feed,) == tuple(base.smartprovide_feeds):
                continue            # the headline, and the other preset's row of the latency grid
            out.append((_with_feed(replace(base, preset=preset), feed), "feed"))
    return out


def _row(e, kind, t_split):
    s = engine_stats(e)
    h = half_stats(e, t_split)
    ff, sp, to = s["fastfill"], s["smartprovide"], s["trade_out"]
    return {"kind": "latency" if kind == "headline" else kind, "headline": kind == "headline",
            "round_lot_btc": e.p.round_lot_btc, "trade_out_latency_ms": e.p.trade_out_latency_ms,
            "preset": e.p.preset, "sip_latency_ms": e.p.sip_latency_ms, "tox_threshold": e.p.tox_threshold,
            "smartprovide_feed": _feed(e.p),
            "fastfill_share_pct": ff["share_pct"], "gap_usd_total": ff["gap_usd_total"],
            "smartprovide_share_pct": sp["share_pct"],
            "smartprovide_share_no_trigger_pct": sp["share_no_trigger_pct"],
            "odd_lot_pct": ff["odd_lot_pct"], "sub_share_pct": ff["sub_share_pct"],
            "trade_out_success_pct": to["success_pct"], "trade_out_share_pct": to["share_of_ff_shares_pct"],
            "trade_out_share_first_half_pct": h["first_half"]["trade_out_share_pct"],
            "trade_out_share_second_half_pct": h["second_half"]["trade_out_share_pct"],
            "no_principal_interest_pct": s["no_principal_interest_pct"],
            "fastfill_at_arrival": ff["at_arrival"], "fastfill_during_smartprovide": ff["during_smartprovide"],
            "smartprovide_fill_pct": sp["fill_pct"]}


def run_all(data_dir, base=None, grid=LATENCY_GRID, max_minutes=None, progress=False):
    base = base or Params()
    plan = engine_grid(base, grid)
    engines = [p for p, _ in plan]
    kinds = [k for _, k in plan]
    res = simulate(data_dir, engines, venues=base.venues, headline=0, max_minutes=max_minutes, progress=progress)
    head = res["head"]
    tape = res["tape"]
    c0, c1 = res["c_first"], res["c_last"]
    if c0 is None:
        raise SystemExit(f"{data_dir}: no recording found (<venue>.jsonl.gz files); record one with "
                         f"citadel-fastfill record --minutes 5 --out {data_dir}")
    t_split = split_time(c0, c1, base)
    meta = {
        "symbol": "BTC/USD", "chart_symbol": "COINBASE:BTCUSD", "venues": res["names"],
        "data_dir": data_dir.replace("\\", "/"), "start_utc": _iso(c0), "end_utc": _iso(c1),
        "minutes": round((c1 - c0) / 60e9, 3), "sip_latency_ms": base.sip_latency_ms,
        "round_lot_btc": base.round_lot_btc, "orders_total": head.st.get("orders", 0),
        "provisional": not run_finished(data_dir),
        "preset": base.preset, "min_order_btc": base.min_order_btc,
        "smartprovide_feed": _feed(base), "tox_threshold": base.tox_threshold, "warmup_s": base.warmup_s,
        "fills": res.get("fills"), "taker_orders": res.get("taker_orders"),
        "split_utc": _iso(t_split),
        "basis_usd": res.get("basis", {}), "params": base.as_dict(),
    }
    stats = engine_stats(head)
    stats["halves"] = half_stats(head, t_split)
    rows = [_row(e, kind, t_split) for e, kind in zip(res["engines"], kinds)]
    sens = sorted(rows, key=lambda s: (s["kind"], s["preset"] != base.preset, s["preset"], s["sip_latency_ms"],
                                       s["round_lot_btc"], s["trade_out_latency_ms"], s["tox_threshold"],
                                       s["smartprovide_feed"]))
    lat_rows = [s for s in sens if s["kind"] == "latency"]
    stats["sensitivity"] = [s for s in lat_rows if s["preset"] == base.preset]
    stats["sensitivity_other_preset"] = [s for s in lat_rows if s["preset"] != base.preset]
    stats["sensitivity_round_lot"] = sorted((s for s in sens if s["kind"] == "round_lot" or s["headline"]),
                                            key=lambda s: s["round_lot_btc"])
    stats["sensitivity_trade_out"] = sorted((s for s in sens if s["kind"] == "trade_out" or s["headline"]),
                                            key=lambda s: s["trade_out_latency_ms"])
    stats["sensitivity_tox"] = sorted((s for s in sens if s["kind"] == "tox" or s["headline"]),
                                      key=lambda s: s["tox_threshold"])
    same_lat = [s for s in lat_rows if s["sip_latency_ms"] == base.sip_latency_ms]     # both presets, this feed
    stats["sensitivity_feed"] = sorted([s for s in sens if s["kind"] == "feed"] + same_lat,
                                       key=lambda s: (s["preset"] != base.preset, s["smartprovide_feed"]
                                                      != _feed(base), s["smartprovide_feed"]))
    # the same statistics for the other preset at the headline's latency: printed next to the headline (report.py)
    other = [e for e, kind in zip(res["engines"], kinds)
             if kind == "latency" and e.p.preset != base.preset and e.p.sip_latency_ms == base.sip_latency_ms]
    if other:
        stats["other_preset"] = dict(engine_stats(other[0]), preset=other[0].p.preset)
    stats["sec"] = {
        "fastfill_orders": SEC_NUMBERS["fastfill_orders"], "odd_lot_pct": SEC_NUMBERS["odd_lot_pct"],
        "trade_out_share_pct_max": SEC_NUMBERS["trade_out_share_pct_max"],
        "trade_out_success_pct": SEC_NUMBERS["trade_out_success_pct"],
        "smartprovide_orders": SEC_NUMBERS["smartprovide_orders"],
        "smartprovide_internalized_after": SEC_NUMBERS["smartprovide_internalized_after"],
        "smartprovide_fill_pct": SEC_NUMBERS["smartprovide_fill_pct"], "paid_usd": SEC_NUMBERS["paid_usd"],
        "fastfill_share_of_ces_flow_pct": SEC_NUMBERS["fastfill_share_of_ces_flow_pct"],
        "both_share_of_algo_engine_pct": SEC_NUMBERS["both_share_of_algo_engine_pct"],
        "both_share_of_ces_flow_pct": SEC_NUMBERS["both_share_of_ces_flow_pct"],
    }
    tox = toxicity_block(head, tape, c0, c1, base)
    report = build_report(res, meta, stats, tox, base)
    return {"res": res, "meta": meta, "stats": stats, "sens": sens, "tox": tox, "report": report}


def split_time(c0, c1, p):
    """The middle of the time orders are handled (after the warm-up): the first half fits, the second tests."""
    t_orders = min(c0 + int(p.warmup_s * 1e9), c1)
    return (t_orders + c1) // 2


def toxicity_block(head, tape, c0, c1, p):
    t_split = split_time(c0, c1, p)
    ev = evaluate(head.rows, tape.mid_at, t_split, horizons=(1.0, 5.0, 30.0), main_h=p.tox_horizon_s,
                  ridge=p.tox_ridge, threshold=p.tox_threshold)
    main = ev["horizons"].get(str(float(p.tox_horizon_s)), {})
    # dealer markouts of the FastFill fills at 1 / 5 / 30 s
    mk = {}
    for h in (1, 5, 30):
        v = []
        for m, side, venue, px, qty, oid in head.ff_fills:
            mid = tape.mid_at(m + int(h * 1e9))
            if mid is None or m + int(h * 1e9) > c1:
                continue
            v.append(dealer_pnl(side, px, mid) / px * 1e4)
        if v:
            a = np.array(v)
            mk[str(h)] = {"mean_bps": round(float(a.mean()), 3), "median_bps": round(float(np.median(a)), 3),
                          "losing_pct": round(float(100 * np.mean(a < 0)), 2), "n": len(v)}
    return {
        "auc_oos": main.get("auc_oos"), "auc_in": main.get("auc_in"),
        "toxic_share_pct": main.get("toxic_share_pct", 0.0), "horizon_s": p.tox_horizon_s,
        "weights": main.get("weights", []), "threshold": p.tox_threshold,
        "loss_rate_test": main.get("loss_rate_test"), "n_train": main.get("n_train"), "n_test": main.get("n_test"),
        "by_horizon": ev["horizons"], "markouts": mk, "features": FEATURE_NAMES,
        "walk_forward_fits": head.tox.n_fits,
        "correlation": main.get("correlation", []),
        "single_feature_auc": main.get("single_feature_auc", []),
        "test_toxic_rate_flagged": main.get("test_toxic_rate_flagged"),
        "test_toxic_rate_passed": main.get("test_toxic_rate_passed"),
        "label": "toxic = no profit or a loss for the dealer had it internalized the order at the SIP price "
                 "(patent col. 4:1-3: \"no profit, reduced profit and/or a loss\"; reduced profit is the profit "
                 "analyzer's test)",
        "parameters": [[n, kind, words] for n, kind, words, _ in PARAMETERS],
        "not_available": [list(x) for x in NOT_AVAILABLE],
        "responses": RESPONSES,
        "profit": head.profit.summary(),
    }


def write_outputs(out_dir, R):
    os.makedirs(out_dir, exist_ok=True)
    head = R["res"]["head"]
    with open(os.path.join(out_dir, "events.jsonl"), "w", encoding="utf-8") as fh:
        for ev in sorted(head.events, key=lambda e: (e["m"], e["id"])):
            d = {k: v for k, v in ev.items() if k != "m"}
            fh.write(json.dumps(d, separators=(",", ":"), default=_js) + "\n")
    summ = {"meta": R["meta"], "stats": R["stats"], "sensitivity_all": R["sens"], "toxicity": R["tox"],
            "report_lines": R["report"]}
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summ, fh, indent=1, default=_js)


def _js(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(type(o))
