"""Statistics of a run and the plain-text report (every line at most 96 characters, no em dashes)."""
import math
import time as _time

import numpy as np

from .params import SEC_NUMBERS
from .ces import hms, money

W = 96


def _pct(a, b):
    return (100.0 * a / b) if b else 0.0


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def engine_stats(e):
    """The contract's `stats` block (plus extras) for one engine."""
    st = e.st
    orders = st.get("orders", 0)
    ff = st.get("ff_orders", 0)
    ff_btc = st.get("ff_btc", 0.0)
    sp = st.get("sp_orders", 0)
    trig = st.get("ff_trigger_arrival", 0)
    gaps = np.array(e.gap_bps) if e.gap_bps else np.array([0.0])
    delays = np.array(e.sp_delay_ms) if e.sp_delay_ms else None
    out = {
        "orders": orders,
        "orders_btc": _r(st.get("orders_btc", 0.0)),
        "orders_fills": st.get("orders_fills", 0),
        "prints_dust_skipped": st.get("prints_dust", 0),
        "orders_warmup_skipped": st.get("orders_warmup", 0),
        "no_principal_interest_pct": _r(_pct(st.get("no_principal_interest", 0), orders), 2),
        "fastfill": {
            "count": ff,
            "share_pct": _r(_pct(ff, orders), 4),
            "odd_lot_pct": _r(_pct(st.get("ff_odd_lot", 0), ff), 2),
            "gap_usd_total": _r(st.get("ff_gap_exec_usd", 0.0), 2),
            "gap_usd_per_order": _r(st.get("ff_gap_exec_usd", 0.0) / ff if ff else 0.0, 4),
            "gap_bps_median": _r(float(np.median(gaps)), 3),
            "gap_bps_p90": _r(float(np.percentile(gaps, 90)), 3),
            "small_worse_pct": _r(_pct(st.get("ff_small_worse", 0), st.get("ff_small", 0)), 2),
            "large_better_pct": _r(_pct(st.get("ff_large_better", 0), st.get("ff_large", 0)), 2),
            # extras
            "btc": _r(ff_btc),
            "notional_usd": _r(st.get("ff_notional_usd", 0.0), 2),
            "gap_top_usd_total": _r(st.get("ff_gap_top_usd", 0.0), 2),
            "at_arrival": st.get("ff_arrival", 0),
            "during_smartprovide": st.get("ff_during_sp", 0),
            "at_smartprovide_window_end": st.get("ff_sp_window_end", 0),
            "small_orders": st.get("ff_small", 0),
            "large_orders": st.get("ff_large", 0),
            "ineligible_trade_out": st.get("ff_ineligible_trade_out", 0),
            "trigger_at_arrival": trig,
            # triggers that rested only on levels smaller than one share-equivalent (SEC 5: odd lots are under 100
            # shares; OURS: equity quotes are whole shares, so no equity level is under one share)
            "sub_share_pct": _r(_pct(st.get("ff_sub_share", 0), ff), 2),
            # SEC 30: price improvement "often was not sufficient to equal the price difference that had triggered"
            "pi_short_pct": _r(_pct(st.get("ff_pi_short", 0), ff), 2),
        },
        "trade_out": {
            "share_of_ff_shares_pct": _r(_pct(st.get("to_ff_btc", 0.0), ff_btc), 2),
            "success_pct": _r(_pct(st.get("to_success", 0), st.get("to_attempts", 0)), 2),
            "attempts": st.get("to_attempts", 0),
            "full_pct": _r(_pct(st.get("to_full", 0), st.get("to_attempts", 0)), 2),
            "position_limit_attempts": st.get("to_position_limit", 0),
        },
        "smartprovide": {
            "count": sp,
            "fill_pct": _r(_pct(st.get("sp_filled_btc", 0.0), st.get("sp_btc", 0.0)), 2),
            "internalized_after_pct": _r(_pct(st.get("sp_internalized_after", 0), sp), 2),
            "worse_than_immediate_pct": _r(_pct(st.get("sp_worse_than_immediate", 0),
                                                st.get("sp_internalized_after", 0)), 2),
            "btc": _r(st.get("sp_btc", 0.0)),
            "filled_btc": _r(st.get("sp_filled_btc", 0.0)),
            "worse_usd_total": _r(st.get("sp_worse_usd", 0.0), 2),
            "reevaluated": st.get("sp_reeval_orders", 0),
            "routed_after_pct": _r(_pct(st.get("sp_routed_after", 0), sp), 2),
            "routed_worse_pct": _r(_pct(st.get("sp_routed_worse", 0), st.get("sp_routed_after", 0)), 2),
            "share_pct": _r(_pct(sp, orders), 2),
            # SmartProvide can only take an order FastFill did not take at arrival: its share of those orders
            "share_no_trigger_pct": _r(_pct(sp, orders - trig), 2),
            # SEC 38: SmartProvide "likely delayed execution": arrival to last fill (immediate internalization: 0)
            "delay_ms_median": _r(float(np.median(delays)), 1) if delays is not None else None,
            "delay_ms_p90": _r(float(np.percentile(delays, 90)), 1) if delays is not None else None,
            "cut_by_data_end": st.get("sp_cut_by_data_end", 0),
        },
        "profit_tab_usd": _r(e.profit.tab_usd, 2),
        "inventory_btc_end": _r(e.inventory),
    }
    return out


def half_stats(e, t_split):
    """Orders without a principal interest and the trade-out share of FastFill BTC (SEC 31) in each half of the
    recording: the toxicity analyzer's threshold is judged on the first half, the second half is out of sample."""
    out = {}
    for name, first in (("first_half", True), ("second_half", False)):
        pis = [pi for m, pi in e.pi_log if (m < t_split) == first]
        ffl = [(q, to) for m, q, to in e.ff_log if (m < t_split) == first]
        btc = sum(q for q, _ in ffl)
        out[name] = {"orders": len(pis),
                     "no_principal_interest_pct": _r(_pct(sum(1 for pi in pis if not pi), len(pis)), 2),
                     "fastfill": len(ffl),
                     "trade_out_share_pct": _r(_pct(sum(q for q, to in ffl if to), btc), 2)}
    return out


def per_group(events, key):
    rows = {}
    for ev in events:
        if ev["rule"] != "FASTFILL":
            continue
        k = key(ev)
        r = rows.setdefault(k, [0, 0.0, 0])
        r[0] += 1
        r[1] += ev["gap_usd"]
        r[2] += 1 if ev["odd_lot"] else 0
    return rows


def build_report(res, meta, stats, tox, params):
    head = res["head"]
    p = head.p
    L = []

    def add(s=""):
        """Append s; a line longer than W is wrapped at a separator, never cut."""
        while len(s) > W:
            cut = max(s.rfind(", ", 0, W - 1), s.rfind("  ", 0, W - 1), s.rfind("; ", 0, W - 1))
            if cut <= 10:
                cut = s.rfind(" ", 0, W)
            if cut <= 10:
                cut = W - 1
            L.append(s[:cut + 1].rstrip())
            s = "      " + s[cut + 1:].lstrip()
        L.append(s)

    def pc(x, d=1):
        return "-" if x is None else f"{x:.{d}f}%"

    sec = SEC_NUMBERS
    ff = stats["fastfill"]
    to = stats["trade_out"]
    sp = stats["smartprovide"]
    halves = stats.get("halves", {})
    add("FASTFILL / SMARTPROVIDE REBUILD  (rules of SEC order 33-10280, run on public BTC/USD feeds)")
    add("simulation of a wholesaler on public data; no orders were sent anywhere")
    add("-" * 72)
    add(f"data      {meta['data_dir']}  {meta['start_utc'][11:19]}-{meta['end_utc'][11:19]} UTC"
        f"  {meta['minutes']:.1f} min")
    add(f"venues    {', '.join(meta['venues'])}  ({res['n_records']:,} messages)")
    add(f"SIP       {p.preset} preset: best quote >= {p.round_lot_btc:g} BTC per venue, "
        f"published {p.sip_latency_ms:g} ms late")
    add(f"orders    {stats['orders']:,} marketable orders = public taker orders >= {p.min_order_btc:g} BTC "
        f"(the fills of one taker order grouped)")
    add(f"          {res.get('fills', 0):,} fills in {res.get('taker_orders', 0):,} taker orders: "
        f"{stats['prints_dust_skipped']:,} under {p.min_order_btc:g} BTC, {stats['orders_warmup_skipped']:,} in the "
        f"first {p.warmup_s:g} s (warm-up)")
    add(f"decide    principal interest: toxicity quotient below {p.tox_threshold:g} (OURS, the patent's analyzer)")
    add(f"          SmartProvide reads {', '.join(p.smartprovide_feeds)}, displays on {p.sp_post_venue}")
    b = res.get("basis", {})
    add("basis     " + "  ".join(f"{k} {v:+.2f}" for k, v in b.items()) + "  USD vs " + p.basis_ref)
    add("-" * 72)
    add(f"{'metric':<44}{'SEC order':>22}{'this run':>12}")

    def row(name, a, b_):
        add(f"{name:<44}{a:>22}{b_:>12}")

    row("FastFill orders", f"{sec['fastfill_orders']:,}", f"{ff['count']:,}")
    row("FastFill share of orders (simulated)", f"{sec['fastfill_share_of_ces_flow_pct']}% of all CES flow",
        f"{_pct(ff['count'], stats['orders']):.1f}%")
    row("FastFill + SmartProvide share of orders", f"{sec['both_share_of_algo_engine_pct']}% of algo engine",
        f"{_pct(ff['at_arrival'] + sp['count'], stats['orders']):.1f}%")
    row("FastFill triggered by an odd lot (para 29)", f"~{sec['odd_lot_pct']}%", f"{ff['odd_lot_pct']:.1f}%")
    row("trade out share of FastFill shares (31)", f"<{sec['trade_out_share_pct_max']}%",
        f"{to['share_of_ff_shares_pct']:.1f}%")
    row("trade out filled in full or part (fn 13)", f"~{sec['trade_out_success_pct']}%", f"{to['success_pct']:.1f}%")
    row("SmartProvide orders (33)", f"{sec['smartprovide_orders']:,}", f"{sp['count']:,}")
    row("SmartProvide shares filled in market (37)", f"~{sec['smartprovide_fill_pct']}%", f"{sp['fill_pct']:.1f}%")
    sp_after, sp_orders = sec["smartprovide_internalized_after"], sec["smartprovide_orders"]
    row("SmartProvide orders internalized after (33)",     # the order gives the counts, not the ratio
        f"~{100 * sp_after / sp_orders:.0f}% ({sp_after // 1000}k/{sp_orders // 1000}k)",
        f"{sp['internalized_after_pct']:.1f}%")
    row("  of those, worse than immediate (39)", "some", f"{sp['worse_than_immediate_pct']:.1f}%")
    row("SmartProvide orders routed after (39, fn 12)", "not given", f"{sp.get('routed_after_pct', 0.0):.1f}%")
    row("small FastFill orders that fared worse (32)", "substantial", f"{ff['small_worse_pct']:.1f}%")
    row("large FastFill orders that fared better (32)", "substantial", f"{ff['large_better_pct']:.1f}%")
    add("SEC shares are of CES flow or of the orders FastFill/SmartProvide handled, after eligibility "
        "conditions that are not public (SEC 10, 30); ours are of every public taker order: not comparable.")
    op = stats.get("other_preset")
    if op:
        add("-" * 72)
        names = {"sip": "sip = round lots only, late (SEC 5, 19, 22-23)", "latency": "latency = late only (SEC 22)"}
        add(f"both presets at {p.sip_latency_ms:g} ms: {names.get(p.preset, p.preset)}; "
            f"{names.get(op['preset'], op['preset'])}")
        add(f"  {'measure':<44}{p.preset:>14}{op['preset']:>14}")
        o_ff, o_to, o_sp = op["fastfill"], op["trade_out"], op["smartprovide"]
        pairs = (
            ("FastFill, % of simulated orders", f"{_pct(ff['count'], stats['orders']):.1f}%",
             f"{_pct(o_ff['count'], stats['orders']):.1f}%"),
            ("FastFill at arrival / after SmartProvide", f"{ff['at_arrival']}/{ff['during_smartprovide']}",
             f"{o_ff['at_arrival']}/{o_ff['during_smartprovide']}"),
            ("odd-lot triggers (29)", f"{ff['odd_lot_pct']:.1f}%", f"{o_ff['odd_lot_pct']:.1f}%"),
            ("triggers on sub-share levels only", f"{ff['sub_share_pct']:.1f}%", f"{o_ff['sub_share_pct']:.1f}%"),
            ("executable gap, USD", f"${ff['gap_usd_total']:,.2f}", f"${o_ff['gap_usd_total']:,.2f}"),
            ("trade outs, % of FastFill BTC (31)", f"{to['share_of_ff_shares_pct']:.1f}%",
             f"{o_to['share_of_ff_shares_pct']:.1f}%"),
            ("trade outs that filled (fn 13)", f"{to['success_pct']:.1f}%", f"{o_to['success_pct']:.1f}%"),
            ("SmartProvide, % of orders", f"{sp['share_pct']:.1f}%", f"{o_sp['share_pct']:.1f}%"),
            ("SmartProvide BTC filled in market (37)", f"{sp['fill_pct']:.1f}%", f"{o_sp['fill_pct']:.1f}%"),
            ("SmartProvide then internalized (33)", f"{sp['internalized_after_pct']:.1f}%",
             f"{o_sp['internalized_after_pct']:.1f}%"),
        )
        for name, a, b_ in pairs:
            add(f"  {name:<44}{a:>14}{b_:>14}")
    add("-" * 72)
    add(f"FastFill gap  median {ff['gap_bps_median']:.3f} bps  p90 {ff['gap_bps_p90']:.3f} bps  "
        f"top-of-book ${ff['gap_top_usd_total']:,.2f}  executable ${ff['gap_usd_total']:,.2f}")
    add(f"              ${ff['gap_usd_per_order']:.4f} per order on {ff['btc']:.3f} BTC "
        f"(${ff['notional_usd']:,.0f} notional)")
    add(f"FastFill at arrival {ff['at_arrival']}, took over SmartProvide {ff['during_smartprovide']}, "
        f"at window end {ff['at_smartprovide_window_end']}; trigger present at arrival on "
        f"{ff['trigger_at_arrival']:,} orders")
    add(f"FastFill triggers resting only on levels under one share-equivalent ({p.min_order_btc:g} BTC): "
        f"{ff['sub_share_pct']:.1f}% (SEC 5: odd lots are under 100 shares; equity quotes are whole shares)")
    add(f"price improvement ${p.price_improvement_usd:g} short of the trigger gap on {ff['pi_short_pct']:.1f}% of "
        f"FastFill orders (SEC 30: \"often was not sufficient\")")
    add(f"no principal interest {stats['no_principal_interest_pct']:.1f}% of orders; "
        f"{ff['ineligible_trade_out']} not taken (fn 13 90% rule)")
    if halves:
        h1, h2 = halves.get("first_half", {}), halves.get("second_half", {})
        add(f"trade outs by half (SEC 31: <{sec['trade_out_share_pct_max']}% of FastFill shares): first half "
            f"{pc(h1.get('trade_out_share_pct'))}, second half (out of sample) {pc(h2.get('trade_out_share_pct'))}; "
            f"no principal interest {pc(h1.get('no_principal_interest_pct'))} / "
            f"{pc(h2.get('no_principal_interest_pct'))}")
    add(f"SmartProvide {sp['share_pct']:.1f}% of orders = {sp['share_no_trigger_pct']:.1f}% of the orders without "
        f"a FastFill trigger at arrival (only those can reach it)")
    if sp.get("delay_ms_median") is not None:
        add(f"SmartProvide wait, arrival to last fill (SEC 38 \"likely delayed\"): median "
            f"{sp['delay_ms_median']:,.0f} ms, p90 {sp['delay_ms_p90']:,.0f} ms; immediate internalization: 0 ms")
    pa = (tox or {}).get("profit") or {}
    if pa:
        add(f"profit analyzer (patent col. 14): running tab ${pa['tab_usd']:,.2f} on {pa['trades_marked']:,} "
            f"dealer fills marked {pa['mark_s']:g} s later")
        add(f"              = spread at the fill ${pa['from_spread_at_fill_usd']:,.2f} + later price "
            f"${pa['from_later_price_usd']:,.2f}; inventory {stats['inventory_btc_end']:+.4f} BTC")
        fl = pa.get("by_order_flow", {})
        if fl:
            add("  by order flow  " + "  ".join(f"{k} ${v['tab_usd']:,.2f} ({v['trades']})" for k, v in fl.items()))
            add(f"  corrective-action signals (period {pa['period_s']:g} s below the expected level): "
                f"{sum(v['corrective_action_signals'] for v in fl.values())}")
    add("-" * 72)
    add("sensitivity (preset, SIP latency -> FastFill share, executable gap, SmartProvide share)")
    for s in stats.get("sensitivity", []) + stats.get("sensitivity_other_preset", []):
        note = "  (below the ~1 ms clock of a Windows recorder)" if s["sip_latency_ms"] < 1 else ""
        add(f"  {s['preset']:<8}{s['sip_latency_ms']:>8g} ms   FastFill {s['fastfill_share_pct']:6.1f}%   "
            f"gap ${s['gap_usd_total']:>9,.2f}   SmartProvide {s['smartprovide_share_pct']:6.1f}%{note}")
    rl = stats.get("sensitivity_round_lot", [])
    if len(rl) > 1:
        add(f"round lot ({p.preset} preset, {p.sip_latency_ms:g} ms) -> FastFill share, odd-lot share of triggers")
        for s in rl:
            note = "  (100 shares at the SEC's $10.00 example)" if abs(s["round_lot_btc"] - 0.0155) < 1e-9 else ""
            add(f"  {s['round_lot_btc']:>8g} BTC   FastFill {s['fastfill_share_pct']:6.1f}%   "
                f"odd lot {s['odd_lot_pct']:6.1f}%   gap ${s['gap_usd_total']:>9,.2f}{note}")
    fd = stats.get("sensitivity_feed", [])
    if fd:
        add(f"SmartProvide feed ({p.sip_latency_ms:g} ms) -> SmartProvide share (of orders without a FastFill "
            f"trigger), FastFill share")
        for s in fd:
            add(f"  {s['preset']:<8}{s['smartprovide_feed']:<10}SmartProvide {s['smartprovide_share_pct']:5.1f}% "
                f"({s['smartprovide_share_no_trigger_pct']:5.1f}%)   FastFill {s['fastfill_share_pct']:5.1f}% "
                f"({s['fastfill_at_arrival']} at arrival, {s['fastfill_during_smartprovide']} after SmartProvide)")
    tx = stats.get("sensitivity_tox", [])
    if len(tx) > 1:
        add(f"toxicity threshold -> no principal interest, FastFill share, trade outs (SEC 31: "
            f"<{sec['trade_out_share_pct_max']}% of FastFill shares; first / second half)")
        for s in tx:
            mark = "  <- headline" if s["headline"] else ""
            add(f"  {s['tox_threshold']:>4g}   no PI {s['no_principal_interest_pct']:5.1f}%   FastFill "
                f"{s['fastfill_share_pct']:5.1f}%   trade outs {s['trade_out_share_pct']:4.1f}% "
                f"({s['trade_out_share_first_half_pct']:.1f}% / {s['trade_out_share_second_half_pct']:.1f}%){mark}")
    tl = stats.get("sensitivity_trade_out", [])
    if len(tl) > 1:
        add("trade out read on the venue's first book message after -> filled in full or part (SEC fn 13: ~55%)")
        for s in tl:
            add(f"  {s['trade_out_latency_ms']:>8g} ms   {s['trade_out_success_pct']:6.1f}%")
    add("-" * 72)
    if tox:
        add(f"toxicity analyzer (patent US 7,587,347): fit first half, test second half, "
            f"horizon {tox['horizon_s']:g} s")
        hz = tox.get("by_horizon", {})
        for h, r in hz.items():
            a1 = r.get("auc_in")
            a2 = r.get("auc_oos")
            add(f"  {float(h):>4g} s  AUC in-sample {a1 if a1 is None else round(a1, 3)}  "
                f"out-of-sample {a2 if a2 is None else round(a2, 3)}  losing fills "
                f"{100 * (r.get('loss_rate_test') or 0):.1f}%  n={r.get('n_test')}")
            sf = r.get("single_feature_auc") or []
            if sf:
                spread = next((x for x in sf if x[0] == "nbbo_spread_bps"), None)
                txt = f"        best single parameter {sf[0][0]} {sf[0][1]:.3f}"
                if spread and spread is not sf[0]:
                    txt += f", nbbo_spread_bps alone {spread[1]:.3f}"
                if a2 is not None and a2 <= 0.5:
                    txt += "; the model fails out of sample here (AUC at or below 0.5)"
                add(txt)
        add(f"  likely toxic (quotient >= {p.tox_threshold:g}): {tox['toxic_share_pct']:.1f}% of test orders")
        fr, pr = tox.get("test_toxic_rate_flagged"), tox.get("test_toxic_rate_passed")
        if fr is not None and pr is not None:
            add(f"  response test: flagged orders were toxic {100 * fr:.1f}% of the time, passed orders "
                f"{100 * pr:.1f}%")
        add("  the label: a fill at the SIP price is marked against the later direct-feed mid, so a wide SIP spread "
            "alone makes a profit likely; compare the quotient with the single parameters above")
        ws = sorted(tox.get("weights", []), key=lambda x: -abs(x[1]))[:6]
        if ws:
            add("  top weights: " + ", ".join(f"{n} {w:+.2f}" for n, w in ws))
        cs = sorted(tox.get("correlation", []), key=lambda x: -abs(x[1]))[:5]
        if cs:
            add("  correlation with a toxic outcome (first half): " + ", ".join(f"{c[0]} {c[1]:+.2f}" for c in cs))
        mk = tox.get("markouts", {})
        if mk:
            add("  FastFill dealer markout: " + "  ".join(
                f"{h} s {v['mean_bps']:+.2f} bps ({v['losing_pct']:.0f}% losing)" for h, v in mk.items()))
    add("-" * 72)
    add("per venue (FastFill orders, executable gap USD, odd-lot share)")
    for k, (n, g, o) in sorted(per_group(head.events, lambda e: e["venue"]).items()):
        add(f"  {k:<9}{n:>6}   ${g:>9,.4f}   odd lot {_pct(o, n):5.1f}%")
    add("per minute (FastFill orders, executable gap USD)")
    for k, (n, g, o) in sorted(per_group(head.events, lambda e: e["t"] // 60000).items()):
        add(f"  {_time.strftime('%H:%M', _time.gmtime(k * 60))}    {n:>6}   ${g:>9,.4f}")
    add("-" * 72)
    add(f"ordered (SEC order, V.C): disgorgement ${sec['disgorgement_usd']:,} + interest ${sec['interest_usd']:,} "
        f"+ penalty ${sec['penalty_usd']:,}")
    add(f"                         = ${sec['paid_usd']:,}")
    return L


SHORT = {"coinbase": "CB", "kraken": "KR", "bitstamp": "BS", "gemini": "GM"}


def _sz(x):
    return ("%.4f" % x) if x >= 0.0001 else ("%.8f" % x)


def format_line(ev):
    """One terminal line per event, at most 96 characters, ASCII only."""
    t = hms(ev["m"])
    r = ev["rule"]
    side = ev["side"]
    sz = _sz(ev["size"])
    v = ev["venue"]
    opt = []
    if r == "FASTFILL":
        parts = [f"{t} FASTFILL  {side:<4} {sz} BTC {v:<8}", f"SIP {money(ev['sip_px'])}",
                 f"DIRECT {money(ev['depth_px'])}", f"GAP ${ev.get('gap_top_usd', 0.0):.2f}"]
        ph = ev.get("phase")
        if ph == "during_sp":
            opt.append("TOOK OVER SP")
        elif ph == "sp_window_end":
            opt.append("AT SP END")
        if ev["odd_lot"]:
            opt.append("ODD LOT")
    elif r == "TRADE_OUT":
        parts = [f"{t} TRADE OUT {side:<4} {sz} BTC {v:<8}", f"@ {money(ev.get('px', ev['depth_px']))}",
                 "->", ev["outcome"]]
    elif r == "SMARTPROVIDE":
        word = "BID" if side == "BUY" else "ASK"
        parts = [f"{t} SMARTPROV {side:<4} {sz} BTC {v:<8}", f"SIP {money(ev['sip_px'])}",
                 f"{SHORT.get(ev.get('ref_feed', ''), 'FEED')} {money(ev['depth_px'])}",
                 f"{word} {money(ev.get('post_px', 0.0))}",
                 f"{ev.get('window_s', 0.0):.1f}s"]
    elif r == "SP_FILLED":
        better = abs(ev.get("sip_px", 0.0) - ev.get("fill_px", 0.0))
        parts = [f"{t} SP FILLED {side:<4} {sz} BTC {v:<8}", f"@ {money(ev.get('fill_px', 0.0))}",
                 f"${better:.2f} BETTER THAN SIP {money(ev.get('sip_px', 0.0))}"]
    elif r == "SP_REEVAL":
        diff = ev.get("diff_usd_per_btc", 0.0)
        word = ("WORSE THAN" if diff > 1e-9 else ("BETTER THAN" if diff < -1e-9 else "SAME AS"))
        parts = [f"{t} SP REEVAL {side:<4} {sz} BTC {v:<8}", f"@ {money(ev.get('fill_px', 0.0))}",
                 f"{word} SIP {money(ev.get('arrival_sip', 0.0))} AT ARRIVAL"]
        if ev.get("action") == "route":
            opt.append("ROUTED")
    else:
        parts = [f"{t} {r} {side} {sz} BTC {v}"]
    s = " ".join(parts)
    for o in opt:
        if len(s) + 1 + len(o) <= W:
            s += " " + o
    return s[:W]
