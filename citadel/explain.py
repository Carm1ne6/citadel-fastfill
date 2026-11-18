"""Explain one event of a run, step by step, with the SEC paragraph or patent passage behind each step.

    citadel-fastfill explain                                   the first FastFill of the newest run in out/
    citadel-fastfill explain --run out/run1 --id 380           every event of order 380 (its FastFill, then its trade out)
    citadel-fastfill explain --run out/run1 --rule TRADE_OUT --nth 2
    citadel-fastfill explain --run out/run1 --list --rule SMARTPROVIDE

Reads out/<run>/events.jsonl (written by run.py), summary.json next to it (the parameters of that run; the defaults
of params.py when it is missing) and the tables of RULES.md, which quote the source of every step. Each step prints
the event's own numbers, the RULES.md row it rests on, that row's quotes from the SEC order or the patent, and the
code that implements it. Read only: it changes no file.
"""
import glob
import json
import os
import re
import textwrap
from dataclasses import fields

from .ces import money
from .params import Params
from .report import format_line

RULE_NAMES = ("FASTFILL", "TRADE_OUT", "SMARTPROVIDE", "SP_FILLED", "SP_REEVAL")
WIDTH = 100
CITE = re.compile(r"\b(SEC \d+(?:-\d+)?|[Ff]n \d+|col\. \d+:\d+(?:-\d+(?::\d+)?)?|claims? \d+(?:-\d+)?|FIG\. \d+)")

# Every RULES.md row the steps below rest on: (section number, start of the row's first cell, bold marks removed).
# tests/test_cli.py checks that each one names exactly one row, so a RULES.md edit cannot silently break explain.
ROWS = {
    "order": ("3", "Every public taker order"),
    "sip": ("1", "The slow view is a SIP"),
    "latency_preset": ("1", "A second preset, `latency`"),
    "sip_late": ("1", "The SIP is late"),
    "principal_interest": ("4", "Principal interest."),
    "riskless": ("4", "Without a principal interest, the order is worked in the market"),
    "price": ("4", "Internalization price"),
    "ff_trigger": ("4", "FastFill trigger"),
    "ff_checked": ("4", "Checked when handling starts"),
    "ff_fill": ("4", "FastFill fill"),
    "odd_lot": ("4", "Odd-lot trigger"),
    "gap": ("4", "Gap"),
    "sub_share": ("4", "Sub-share triggers"),
    "small_large": ("4", "Small orders worse, large orders better"),
    "trade_out": ("4", "Trade out attempt"),
    "ninety": ("4", "Only if that order is at most 90%"),
    "trade_out_result": ("4", "Trade out result"),
    "sp_trigger": ("4", "SmartProvide trigger"),
    "sp_feed": ("4", "Which feed"),
    "sp_reach": ("4", "SmartProvide can only reach"),
    "display": ("4", "Display"),
    "window": ("4", "Window"),
    "fills": ("4", "Fills"),
    "delay": ("4", "Delay"),
    "takeover": ("4", "Takeover"),
    "reeval": ("4", "Re-evaluation"),
    "data_end": ("4", "The end of the data"),
    "tox_using": ("5", "Using it"),
    "tox_responding": ("5", "Responding"),
    "tox_threshold": ("5", "Threshold"),
    "tox_earlier": ("5", "Only earlier orders count"),
    "profit_watch": ("6", "Watch every executed trade"),
    "profit_spread": ("6", "Log the spread at the fill"),
}


# ------------------------------------------------------------------------------------------------- RULES.md
def _plain(s):
    return s.replace("**", "").strip()


def load_rules(path):
    """Every table row of RULES.md as {"section": "4", "title": section heading, "cells": [...]}."""
    rows = []
    sec, title = "", ""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("## "):
                title = line[3:].strip()
                sec = title.split(".")[0] if title[:1].isdigit() else title
                continue
            if not line.startswith("|") or line.startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append({"section": sec, "title": title, "cells": cells})
    return rows


def find_row(rows, key):
    sec, start = ROWS[key]
    hits = [r for r in rows if r["section"] == sec and _plain(r["cells"][0]).startswith(start)]
    if len(hits) != 1:
        raise KeyError(f"RULES.md section {sec}: {len(hits)} rows start with {start!r} (explain.ROWS[{key!r}])")
    return hits[0]


def citations(text):
    """The SEC paragraphs and footnotes, patent columns, claims and figures a RULES.md cell cites, in order."""
    out = []
    for c in CITE.findall(text):
        c = "fn" + c[2:] if c.startswith("Fn") else c
        if c not in out:
            out.append(c)
    return out


# ------------------------------------------------------------------------------------------------- runs
def latest_run(base="out"):
    paths = glob.glob(os.path.join(base, "*", "events.jsonl"))
    if not paths:
        raise SystemExit("no run found in out/: replay a recording first, e.g. citadel-fastfill replay --data "
                         "data/run1 (or pass --run / --events)")
    return os.path.dirname(max(paths, key=os.path.getmtime))


def load_events(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def params_of_run(summary):
    """The Params a run used, from summary.json's meta.params (lists back to tuples)."""
    d = (summary or {}).get("meta", {}).get("params") or {}
    kw = {}
    for f in fields(Params):
        if f.name in d:
            v = d[f.name]
            if isinstance(v, list):
                v = tuple(tuple(x) if isinstance(x, list) else x for x in v)
            kw[f.name] = v
    return Params(**kw)


# ------------------------------------------------------------------------------------------------- steps
def _sz(x):
    return (("%.4f" % x).rstrip("0").rstrip(".") if x >= 0.0001 else "%.8f" % x) + " BTC"


def _step(title, keys, detail):
    return {"title": title, "keys": list(keys), "detail": detail}


def order_steps(ev, p, fits=None):
    """What every event of an order shares: the order itself and the principal-interest decision (SEC 26).
    fits = the analyzer's walk-forward fits over the whole run (summary.json), when known."""
    q, th, pi = ev["toxicity"], p.tox_threshold, ev["principal_interest"]
    if pi and fits == 0:
        d = (f"principal interest: the toxicity analyzer never fitted in this run (fewer than {p.tox_min_train} "
             f"outcomes known), and a silent analyzer screens nothing; {q:.4f} is the share of losing fills so far, "
             f"not a quotient")
        keys = ("principal_interest", "tox_earlier", "tox_responding")
    elif pi and q >= th:
        d = (f"principal interest: the toxicity analyzer was still silent (fewer than {p.tox_min_train} outcomes "
             f"known) and a silent analyzer screens nothing; {q:.4f} is the share of losing fills so far, not a "
             f"quotient")
        keys = ("principal_interest", "tox_earlier", "tox_responding")
    elif pi:
        d = (f"toxicity quotient {q:.4f} below the threshold {th:g} (or the analyzer still silent): EXECUTE, so CES "
             f"has a principal interest and may internalize the order")
        keys = ("principal_interest", "tox_using", "tox_responding", "tox_threshold")
    else:
        d = (f"toxicity quotient {q:.4f} at or above the threshold {th:g}: ANOTHER_MARKET_CENTER, so CES has no "
             f"principal interest and works the order as riskless principal")
        keys = ("principal_interest", "tox_using", "tox_responding", "tox_threshold", "riskless")
    whole = ev["rule"] == "SMARTPROVIDE" or (ev["rule"] == "FASTFILL" and ev.get("phase") == "arrival")
    size = f" of {_sz(ev['size'])}" if whole else ""
    return [
        _step("The order", ("order",), f"a public taker order{size} on {ev['venue']} ({ev['side']}), its fills on "
                                       f"that venue grouped, stands for one marketable retail order"),
        _step("Principal interest", keys, d),
    ]


def event_steps(ev, p, wait_ms=None):
    """The steps behind one event, in the order the engine takes them."""
    r = ev["rule"]
    buy = ev["side"] == "BUY"
    side_word = "offer" if buy else "bid"
    sip_word = "NBO" if buy else "NBB"
    better = "lower" if buy else "higher"
    size = _sz(ev["size"])
    sip, depth = ev.get("sip_px"), ev.get("depth_px")
    S = []
    if r == "FASTFILL":
        view = ("each venue's best quote of at least one round lot (%g BTC), consolidated" % p.round_lot_btc
                if p.preset == "sip" else "each venue's best quote of any size, consolidated")
        S.append(_step("The SIP's price", ("sip" if p.preset == "sip" else "latency_preset", "sip_late"),
                       f"the SIP {sip_word} was {money(sip)}: {view}, published {p.sip_latency_ms:g} ms late"))
        phase = ev.get("phase", "arrival")
        d = (f"the best {side_word} on the direct feeds was {money(depth)}, {better} than the SIP {sip_word} by "
             f"${ev.get('gap_top_usd', abs(sip - depth)):.2f} per BTC ({ev['gap_bps']:.2f} bps)")
        keys = ["ff_trigger"]
        if phase == "during_sp":
            keys += ["ff_checked", "takeover"]
            d += "; it arose while SmartProvide displayed the order, so FastFill took the order over"
        elif phase == "sp_window_end":
            keys += ["reeval"]
            d += "; it was there when the SmartProvide window ended"
        else:
            d += ", when CES began handling the order"
        S.append(_step("FastFill trigger", keys, d))
        if ev["odd_lot"]:
            d = (f"no level of at least one round lot ({p.round_lot_btc:g} BTC) on the direct feeds beat the SIP: "
                 f"an odd-lot trigger")
        else:
            d = "a round lot on the direct feeds beat the SIP: the SIP had not published it yet (latency)"
        S.append(_step("Odd lot or round lot", ("odd_lot",), d))
        if ev.get("sub_share"):
            S.append(_step("Sub-share levels", ("sub_share",),
                           f"the trigger rested only on levels under one share-equivalent ({p.min_order_btc:g} BTC)"))
        fill = ev.get("fill_px", sip)
        pi_txt = (f"the SIP {sip_word} improved by ${p.price_improvement_usd:g}" if p.price_improvement_usd
                  else f"the SIP {sip_word}, no price improvement")
        S.append(_step("Fill at the SIP price", ("ff_fill", "price"),
                       f"FastFill internalized the whole {size} at {money(fill)} ({pi_txt}), not at the direct "
                       f"feeds' {money(depth)}"))
        S.append(_step("What the order did not get", ("gap",),
                       f"executable gap ${ev['gap_usd']:.4f}: {_sz(ev.get('qty_better', 0.0))} of the order was "
                       f"shown at better direct prices; top-of-book gap ${ev.get('gap_top_usd', 0.0):.2f} per BTC"))
        small = ev["size"] < p.round_lot_btc
        vw = ev.get("displayed_vwap")
        kind = "smaller" if small else "larger"
        if ev.get("displayed_better"):
            d = (f"taking it from the displayed direct liquidity would have averaged {money(vw)}, better than the "
                 f"SIP fill: a {kind} order that fared worse")
        else:
            d = (f"taking it from the displayed direct liquidity would not have been better (or could not fill it "
                 f"all): a {kind} order that fared better at the SIP price")
        S.append(_step(f"{kind.capitalize()} order", ("small_large",), d))
        if not ev["principal_interest"]:
            S.append(_step("Trade out attempt", ("trade_out", "ninety"),
                           f"no principal interest: FastFill still internalized the order and at the same moment "
                           f"sent CES's own order for {size} at the direct {money(depth)} (the TRADE_OUT event of "
                           f"this order); eligible because it was at most {p.trade_out_cap:.0%} of the size at the "
                           f"best price on its venue"))
        S.append(_step("Profit analyzer", ("profit_watch", "profit_spread"),
                       f"the fill is logged with the bid and offer at the fill and marked {p.profit_mark_s:g} s "
                       f"later in the dealer's running tab"))
    elif r == "TRADE_OUT":
        venue = ev.get("venue_out", ev["venue"])
        px = ev.get("px", depth)
        S.append(_step("Trade out attempt", ("trade_out",),
                       f"sent with this order's FastFill: CES's own order to {'buy' if buy else 'sell'} {size} at "
                       f"{money(px)} on {venue}, while the customer got the SIP price {money(sip)}"))
        S.append(_step("90% rule", ("ninety",),
                       f"eligible: {size} was at most {p.trade_out_cap:.0%} of the size at the best {side_word} "
                       f"on {venue}"))
        S.append(_step("Result", ("trade_out_result",),
                       f"it reaches {venue} {p.trade_out_latency_ms:g} ms after it is sent and was read on {venue}'s "
                       f"first book message after that, {ev.get('resolved_ms', 0.0):.0f} ms after sending: "
                       f"{ev['outcome']}"))
        if (ev.get("filled") or 0) > 0:
            S.append(_step("Profit analyzer", ("profit_watch",),
                           "a filled trade out is the dealer's own trade: it counts in the running tab and belongs "
                           "to no order flow"))
    elif r == "SMARTPROVIDE":
        feed = ev.get("ref_feed", ",".join(p.smartprovide_feeds))
        post_venue = ev.get("post_venue", p.sp_post_venue)
        S.append(_step("No FastFill trigger at arrival", ("sp_reach",),
                       "no direct feed beat the SIP price when CES began handling the order, so FastFill did not "
                       "take it"))
        S.append(_step("SmartProvide trigger", ("sp_trigger", "sp_feed"),
                       f"the SIP {sip_word} {money(sip)} was better than {feed}'s best {side_word} {money(depth)} "
                       f"(by ${ev.get('ref_gap_usd', abs(sip - depth)):.2f} per BTC): the feed SmartProvide reads "
                       f"no longer showed the SIP price"))
        S.append(_step("Display", ("display",),
                       f"displayed {'a bid' if buy else 'an offer'} at {money(ev.get('post_px', 0.0))} on {post_venue}: "
                       f"one cent inside the SIP {sip_word}, rounded onto {post_venue}'s price grid; nothing was "
                       f"internalized meanwhile"))
        S.append(_step("Window", ("window",),
                       f"for {ev.get('window_s', 0.0):.1f} s: {size} on a scale from {p.sp_display_min_s:g} s at "
                       f"{p.sp_small_btc:g} BTC to {p.sp_display_max_s:g} s at {p.sp_large_btc:g} BTC, or until "
                       f"FastFill takes over"))
    elif r == "SP_FILLED":
        fill = ev.get("fill_px", 0.0)
        S.append(_step("Fill in the market", ("fills",),
                       f"a later print on {ev.get('post_venue') or p.sp_post_venue} at "
                       f"{money(ev.get('print_px', depth))} went through the "
                       f"displayed price: {size} filled at {money(fill)}, ${abs(sip - fill):.2f} per BTC better than "
                       f"the SIP {sip_word} at arrival {money(sip)}"))
        if wait_ms is not None:
            S.append(_step("Delay", ("delay",), f"filled {wait_ms:,.0f} ms after the order arrived; immediate "
                                                f"internalization would have taken 0 ms"))
    elif r == "SP_REEVAL":
        fill = ev.get("fill_px", 0.0)
        arrival = ev.get("arrival_sip", depth)
        if ev.get("action") == "route":
            keys = ("reeval", "riskless")
            d = (f"the window ended without a FastFill trigger; with no principal interest CES routed the rest "
                 f"({size}) to take the displayed liquidity at an average {money(fill)}")
        else:
            keys = ("reeval", "price")
            d = (f"the window ended without a FastFill trigger; CES internalized the rest ({size}) at the SIP price "
                 f"then, {money(fill)}")
        diff = ev.get("diff_usd_per_btc", 0.0)
        word = "worse" if diff > p.eps else ("better" if diff < -p.eps else "the same")
        d += f": {word} than the SIP {sip_word} {money(arrival)} at arrival"
        if word != "the same":
            d += f" by ${abs(diff):.2f} per BTC"
        S.append(_step("Re-evaluation", keys, d))
        if "cut" in ev.get("outcome", ""):
            S.append(_step("End of the data", ("data_end",), "decided at the last message of the recording"))
        if wait_ms is not None:
            S.append(_step("Delay", ("delay",), f"finished {wait_ms:,.0f} ms after the order arrived; immediate "
                                                f"internalization would have taken 0 ms"))
    return S


# ------------------------------------------------------------------------------------------------- output
def _with_sources(steps, rows):
    out = []
    for s in steps:
        refs = []
        for k in s["keys"]:
            row = find_row(rows, k)
            c = row["cells"]
            refs.append({"section": row["section"], "row": ROWS[k][1],
                         "source": _plain(c[1]) if len(c) > 1 else "", "code": _plain(c[2]) if len(c) > 2 else ""})
        out.append(dict(s, rules=refs))
    return out


def build(events, chain, p, rows, params_from="defaults", fits=None):
    """The explanation of the events in `chain` (all of one order), as a JSON-ready dict. fits = the toxicity
    analyzer's walk-forward fits over the run (summary.json's toxicity.walk_forward_fits), None when unknown."""
    first = chain[0]
    t0 = first["t"]
    sp = next((e for e in chain if e["rule"] == "SMARTPROVIDE"), None)
    items = []
    for i, ev in enumerate(chain):
        steps = order_steps(ev, p, fits) if i == 0 else []
        wait = (ev["t"] - t0) if (ev["rule"] in ("SP_FILLED", "SP_REEVAL") and
                                  any(e["rule"] == "SMARTPROVIDE" for e in chain[:i])) else None
        if ev["rule"] == "SP_FILLED" and sp is not None:
            ev = dict(ev, post_venue=sp.get("post_venue"))     # where the order was displayed
        steps += event_steps(ev, p, wait)
        items.append({"rule": ev["rule"], "line": format_line(dict(ev, m=ev["t"] * 1_000_000)), "event": ev,
                      "steps": _with_sources(steps, rows)})
    others = [e for e in events if e["id"] == first["id"] and e not in chain]
    return {"order": first["id"], "venue": first["venue"], "side": first["side"], "params_from": params_from,
            "params": {"preset": p.preset, "sip_latency_ms": p.sip_latency_ms, "round_lot_btc": p.round_lot_btc,
                       "tox_threshold": p.tox_threshold, "trade_out_latency_ms": p.trade_out_latency_ms,
                       "smartprovide_feeds": list(p.smartprovide_feeds)},
            "events": items, "other_events": [e["rule"] for e in others]}


def render(x, short=False):
    w = textwrap.TextWrapper(width=WIDTH, initial_indent="    ", subsequent_indent="    ")
    ws = textwrap.TextWrapper(width=WIDTH, initial_indent="    source  ", subsequent_indent=" " * 12)
    wc = textwrap.TextWrapper(width=WIDTH, initial_indent="    code    ", subsequent_indent=" " * 12)
    pr = x["params"]
    L = [f"ORDER {x['order']}  {x['venue']}  {x['side']}   parameters ({x['params_from']}): {pr['preset']} preset, "
         f"SIP {pr['sip_latency_ms']:g} ms late, round lot {pr['round_lot_btc']:g} BTC, threshold "
         f"{pr['tox_threshold']:g}", ""]
    n = 0
    for item in x["events"]:
        L.append(item["line"])
        for s in item["steps"]:
            n += 1
            where = "; ".join(f"{r['section']} \"{r['row']}\"" for r in s["rules"])
            L += textwrap.wrap(f" {n:>2}. {s['title']}   [RULES.md {where}]", WIDTH, subsequent_indent=" " * 8)
            L += w.wrap(s["detail"])
            if short:
                cites = []
                for r in s["rules"]:
                    cites += [c for c in citations(r["source"]) if c not in cites]
                L += textwrap.wrap("cites   " + (", ".join(cites) or "OURS"), WIDTH, initial_indent="    ",
                                   subsequent_indent=" " * 12)
            else:
                for r in s["rules"]:
                    L += ws.wrap(r["source"])
                    if r["code"] and r["code"] != "-":
                        L += wc.wrap(r["code"])
        L.append("")
    if x["other_events"]:
        L.append(f"order {x['order']} also has: {', '.join(x['other_events'])} (--id {x['order']} shows them all)")
    L += textwrap.wrap("Quotes from RULES.md: the SEC order (docs/sec_order_2017_33-10280.txt) and US 7,587,347 B2 "
                       "(docs/patents/). OURS = our choice, fixed by neither document.", WIDTH)
    return "\n".join(L)


def select(events, oid=None, rule=None, nth=1):
    """The chain to explain: every event of order `oid`, or the nth event of `rule` (default the first FastFill)."""
    if oid is not None:
        chain = [e for e in events if e["id"] == oid]
        if not chain:
            raise SystemExit(f"no event for order {oid}: an order with no FastFill or SmartProvide was handled by "
                             f"CES's other logic, which is not modelled")
        return chain
    rule = (rule or "FASTFILL").upper()
    if rule not in RULE_NAMES:
        raise SystemExit(f"unknown rule {rule!r}; one of {', '.join(RULE_NAMES)}")
    hits = [e for e in events if e["rule"] == rule]
    if len(hits) < nth or nth < 1:
        raise SystemExit(f"the run has {len(hits)} {rule} events; --nth {nth} is out of range")
    return [hits[nth - 1]]


def listing(events, rule=None, limit=20):
    rule = rule.upper() if rule else None
    hits = [e for e in events if rule is None or e["rule"] == rule]
    lines = [f"{e['id']:>7}  {format_line(dict(e, m=e['t'] * 1_000_000))}" for e in hits[:limit]]
    more = len(hits) - len(lines)
    if more > 0:
        lines.append(f"... {more} more (--limit)")
    counts = {}
    for e in events:
        counts[e["rule"]] = counts.get(e["rule"], 0) + 1
    lines.append("events: " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
    return "\n".join(["  order  line"] + lines)
