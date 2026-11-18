"""citadel-fastfill: one command for the rebuild (installed by `pip install -e .`; run it from the repository folder).

FastFill and SmartProvide, the two algorithmic strategies the SEC described in its order of January 13, 2017 against
Citadel Securities LLC (Release No. 33-10280), rebuilt rule by rule on public BTC/USD order books. A simulation on
public data: no orders are sent anywhere, and this is not Citadel's code.
"""
import argparse
import asyncio
import importlib
import json
import os
import sys
from dataclasses import fields

from .config import add_config_argument, parse_args, params_from
from .params import SEC_NUMBERS, SOURCE, Params, with_overrides

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "https://github.com/Carm1ne6/citadel-fastfill"

# commands that run a script of the repository with its own options (`citadel-fastfill replay --help` lists them)
SCRIPTS = {
    "record": ("record", "record public order books and trades of the four venues (record.py)"),
    "replay": ("run", "replay a recording: the report with the SEC's numbers next to ours (run.py)"),
    "live": ("live", "the FastFill / SmartProvide rules on live public order books (live.py)"),
    "export-film": ("export_film", "write the data file of the replay animation (candles, events, markout fan) from a "
                                   "recording (export_film.py)"),
    "check-venues": ("check_venues", "check a recording: books never crossed, Kraken checksums, taker sides"),
    "check-film": ("check_film", "check that data file against its contract (check_film.py)"),
    "markout": ("tools/markout_my_fills", "markouts of YOUR OWN fills against recorded books, 1 / 5 / 30 s"),
}
HELP = {
    "sensitivity": "the latency, round-lot, threshold, trade-out and feed grids of a run",
    "explain": "one event, step by step, with the SEC paragraph and patent passage behind each step",
    "sec-check": "the SEC order's numbers next to a run's, with the paragraph of each",
    "params": "every parameter, its value (after --config / --set), its default and its source",
}
EXAMPLES = """examples:
  citadel-fastfill record --minutes 5 --out data/run1      record 5 minutes of public books and trades
  citadel-fastfill replay --data data/run1                 the report (writes out/run1/)
  citadel-fastfill live --seconds 180                      the rules on live books (orders from 60 s on), a summary every 10 s
  citadel-fastfill sensitivity --data data/run1            the sensitivity grids
  citadel-fastfill explain --run out/run1 --id 380         order 380, step by step, with its sources
  citadel-fastfill sec-check --run out/run1                the SEC's numbers next to the run's
  citadel-fastfill params --config my.toml                 the parameters a run would use
any replay / live / export-film option: --config FILE (fastfill.example.toml) and --set KEY=VALUE"""


def usage():
    w = max(len(k) for k in list(SCRIPTS) + list(HELP))
    lines = ["usage: citadel-fastfill COMMAND [options]      (citadel-fastfill COMMAND --help for its options)", "",
             "FastFill / SmartProvide (SEC order 33-10280) rebuilt rule by rule on public BTC/USD order books.",
             "A simulation on public data: no orders are sent anywhere. Not Citadel's code.", "", "commands:"]
    for name in ("record", "replay", "live", "sensitivity", "explain", "sec-check", "params", "export-film",
                 "check-venues", "check-film", "markout"):
        text = SCRIPTS[name][1] if name in SCRIPTS else HELP[name]
        lines.append(f"  {name:<{w}}  {text}")
    return "\n".join(lines + ["", EXAMPLES])


def _script(module):
    """Import a script of the repository (run.py, live.py, ...) from the folder that holds the citadel package."""
    path = os.path.join(ROOT, *module.split("/")) + ".py"
    if not os.path.exists(path):
        raise SystemExit(f"{os.path.basename(path)} is not next to the citadel package ({ROOT}). citadel-fastfill runs "
                         f"from a clone of the repository: git clone {REPO} && cd citadel-fastfill && pip install -e .")
    folder = os.path.dirname(path)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return importlib.import_module(os.path.basename(module))


def run_script(cmd, argv):
    mod = _script(SCRIPTS[cmd][0])
    old = sys.argv
    sys.argv = [f"citadel-fastfill {cmd}"] + list(argv)
    try:
        if cmd == "record":
            asyncio.run(mod.main())
            return 0
        return mod.main() or 0
    except KeyboardInterrupt:
        return 130
    finally:
        sys.argv = old


# ------------------------------------------------------------------------------------------------- shared
def _summary(run_dir):
    path = os.path.join(run_dir, "summary.json")
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found: replay the recording first (citadel-fastfill replay --data data/<run>)")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _latest_summary_dir(base="out"):
    import glob
    paths = glob.glob(os.path.join(base, "*", "summary.json"))
    if not paths:
        raise SystemExit("no run found in out/: give --data data/<run> (a recording) or --run out/<run>")
    return os.path.dirname(max(paths, key=os.path.getmtime))


def _floats(text):
    try:
        return tuple(float(x) for x in text.split(",") if x.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected numbers separated by commas, got {text!r}")


def _replay_options(ap):
    """The parameter options of run.py, so a command that runs the pipeline builds the same Params."""
    ap.add_argument("--preset", default=Params.preset, choices=["sip", "latency"])
    ap.add_argument("--latency-ms", type=float, default=Params.sip_latency_ms)
    ap.add_argument("--round-lot", type=float, default=Params.round_lot_btc)
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override any parameter of citadel/params.py")
    add_config_argument(ap)


def _pct(x):
    return "-" if x is None else f"{x:.1f}%"


# ------------------------------------------------------------------------------------------------- sensitivity
GRIDS = ("latency", "round-lot", "threshold", "trade-out", "feed")


def _norm(obj):
    return json.loads(json.dumps(obj))


def _run_grids(data, base, a):
    """run_all with the requested grids (pipeline's module grids restored afterwards)."""
    from . import pipeline
    saved = {k: getattr(pipeline, k) for k in ("ROUND_LOT_GRID", "TRADE_OUT_GRID", "TOX_GRID")}
    try:
        if a.round_lot_grid:
            pipeline.ROUND_LOT_GRID = a.round_lot_grid
        if a.trade_out_grid:
            pipeline.TRADE_OUT_GRID = a.trade_out_grid
        if a.threshold_grid:
            pipeline.TOX_GRID = a.threshold_grid
        return pipeline.run_all(data, base=base, grid=a.latency_grid or pipeline.LATENCY_GRID,
                                max_minutes=a.max_minutes)
    finally:
        for k, v in saved.items():
            setattr(pipeline, k, v)


def sensitivity_tables(summary, only=GRIDS):
    """The grids of a summary.json as {grid: [row, ...]} plus the text tables."""
    st = summary["stats"]
    meta = summary["meta"]
    p = meta.get("params", {})
    head = f"{p.get('preset', '?')} {p.get('sip_latency_ms', 0):g} ms"
    out, L = {}, []
    L.append(f"sensitivity of {meta.get('data_dir')} ({meta.get('minutes', 0):.1f} min, {st['orders']:,} orders); "
             f"* = headline ({head}, round lot {p.get('round_lot_btc', 0):g} BTC, threshold "
             f"{p.get('tox_threshold', 0):g}). Shares are not comparable with the SEC's (RULES.md section 10).")
    if "latency" in only:
        rows = st.get("sensitivity", []) + st.get("sensitivity_other_preset", [])
        out["latency"] = rows
        L += ["", "SIP latency (SEC 22; fn 9: 4.04 / 5.42 ms in early 2010)",
              f"  {'preset':<9}{'latency':>9}{'FastFill':>10}{'odd lot':>9}{'gap USD':>10}{'SmartProvide':>14}"
              f"{'trade outs filled':>19}"]
        for s in rows:
            mark = "*" if s.get("headline") else " "
            note = "  below the ~1 ms clock of a Windows recorder" if s["sip_latency_ms"] < 1 else ""
            L.append(f"{mark} {s['preset']:<9}{s['sip_latency_ms']:>6g} ms{s['fastfill_share_pct']:>9.1f}%"
                     f"{s['odd_lot_pct']:>8.1f}%{s['gap_usd_total']:>10,.2f}{s['smartprovide_share_pct']:>13.1f}%"
                     f"{s['trade_out_success_pct']:>18.1f}%{note}")
    if "round-lot" in only:
        rows = st.get("sensitivity_round_lot", [])
        out["round_lot"] = rows
        L += ["", "round lot (SEC 5: 100 shares; 0.0155 BTC = 100 shares at the order's $10.00 example)",
              f"  {'round lot':>12}{'FastFill':>10}{'odd lot':>9}{'sub-share':>11}{'gap USD':>10}"]
        for s in rows:
            mark = "*" if s.get("headline") else " "
            L.append(f"{mark} {s['round_lot_btc']:>8g} BTC{s['fastfill_share_pct']:>9.1f}%{s['odd_lot_pct']:>8.1f}%"
                     f"{s['sub_share_pct']:>10.1f}%{s['gap_usd_total']:>10,.2f}")
        if len(rows) <= 1:
            L.append("  (the round-lot grid runs under the sip preset only)")
    if "threshold" in only:
        rows = st.get("sensitivity_tox", [])
        out["threshold"] = rows
        L += ["", f"toxicity threshold (OURS; SEC 31: trade outs < {SEC_NUMBERS['trade_out_share_pct_max']}% of "
                  f"FastFill shares)",
              f"  {'threshold':>10}{'no principal interest':>23}{'FastFill':>10}{'trade outs':>12}"
              f"{'1st half':>10}{'2nd half':>10}"]
        for s in rows:
            mark = "*" if s.get("headline") else " "
            L.append(f"{mark} {s['tox_threshold']:>10g}{s['no_principal_interest_pct']:>22.1f}%"
                     f"{s['fastfill_share_pct']:>9.1f}%{s['trade_out_share_pct']:>11.1f}%"
                     f"{s['trade_out_share_first_half_pct']:>9.1f}%{s['trade_out_share_second_half_pct']:>9.1f}%")
    if "trade-out" in only:
        rows = st.get("sensitivity_trade_out", [])
        out["trade_out"] = rows
        L += ["", f"trade out delay (OURS; fn 13: about {SEC_NUMBERS['trade_out_success_pct']}% filled in full or "
                  f"part)", f"  {'delay':>9}{'filled':>9}"]
        for s in rows:
            mark = "*" if s.get("headline") else " "
            L.append(f"{mark} {s['trade_out_latency_ms']:>6g} ms{s['trade_out_success_pct']:>8.1f}%")
    if "feed" in only:
        rows = st.get("sensitivity_feed", [])
        out["feed"] = rows
        L += ["", "SmartProvide feed (SEC 34: often only one feed; Kraken quotes on a $0.10 grid)",
              f"  {'preset':<9}{'feed':<10}{'SmartProvide':>13}{'of no-trigger':>15}{'FastFill':>10}"
              f"{'at arrival':>12}{'after SP':>10}"]
        for s in rows:
            mark = "*" if s.get("headline") else " "
            L.append(f"{mark} {s['preset']:<9}{s['smartprovide_feed']:<10}{s['smartprovide_share_pct']:>12.1f}%"
                     f"{s['smartprovide_share_no_trigger_pct']:>14.1f}%{s['fastfill_share_pct']:>9.1f}%"
                     f"{s['fastfill_at_arrival']:>12}{s['fastfill_during_smartprovide']:>10}")
    return out, "\n".join(L)


def cmd_sensitivity(argv):
    ap = argparse.ArgumentParser(
        prog="citadel-fastfill sensitivity",
        description="The sensitivity grids of a run: SIP latency, round lot, toxicity threshold, trade-out delay and "
                    "SmartProvide feed, each next to the SEC's number it bears on. Reads out/<run>/summary.json; "
                    "replays the recording first when that file is missing, made with other parameters, or a custom "
                    "grid is asked for.")
    ap.add_argument("--data", default=None, help="recording folder (data/<run>)")
    ap.add_argument("--run", default=None, help="output folder of a replay (default out/<recording name>)")
    ap.add_argument("--rerun", action="store_true", help="replay even when the summary is up to date")
    ap.add_argument("--latency-grid", type=_floats, default=None, metavar="MS,...", help="e.g. 0.5,5,50,250,1000")
    ap.add_argument("--round-lot-grid", type=_floats, default=None, metavar="BTC,...", help="e.g. 0.01,0.0155,0.05,0.1")
    ap.add_argument("--threshold-grid", type=_floats, default=None, metavar="Q,...", help="e.g. 0.5,0.6,0.7,0.8,0.9")
    ap.add_argument("--trade-out-grid", type=_floats, default=None, metavar="MS,...", help="e.g. 5,50,250")
    ap.add_argument("--only", default=",".join(GRIDS), help=f"grids to print, from {','.join(GRIDS)}")
    ap.add_argument("--json", action="store_true", help="print the grids as JSON")
    _replay_options(ap)
    a = parse_args(ap, argv)
    only = tuple(g.strip() for g in a.only.split(",") if g.strip())
    bad = [g for g in only if g not in GRIDS]
    if bad:
        raise SystemExit(f"--only: unknown grid {bad[0]!r}; one of {', '.join(GRIDS)}")
    if a.data is None and a.run is None:
        a.run = _latest_summary_dir()
    out = a.run or os.path.join("out", os.path.basename(a.data.rstrip("/\\")))
    base = with_overrides(Params(preset=a.preset, sip_latency_ms=a.latency_ms, round_lot_btc=a.round_lot), a.set)
    custom = any((a.latency_grid, a.round_lot_grid, a.threshold_grid, a.trade_out_grid))
    path = os.path.join(out, "summary.json")
    need = a.rerun or custom or not os.path.exists(path)
    reason = "asked" if (a.rerun or custom) else "no summary yet"
    if not need and a.data:
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)["meta"]
        same_data = meta.get("data_dir", "").rstrip("/") == a.data.replace("\\", "/").rstrip("/")
        if meta.get("params") != _norm(base.as_dict()) or not same_data or a.max_minutes:
            need, reason = True, "the summary was made with other parameters or data"
    if need:
        if not a.data:
            raise SystemExit("--data data/<run> is needed to compute the grids")
        from .pipeline import write_outputs
        print(f"replaying {a.data} ({reason}) ...", file=sys.stderr, flush=True)
        R = _run_grids(a.data, base, a)
        write_outputs(out, R)
    summary = _summary(out)
    grids, text = sensitivity_tables(summary, only)
    if a.json:
        print(json.dumps({"run": out.replace("\\", "/"), "data_dir": summary["meta"].get("data_dir"),
                          "params": summary["meta"].get("params"), "grids": grids}, indent=1))
    else:
        print(text)
        print("\nfrom " + path.replace("\\", "/"))
    return 0


# ------------------------------------------------------------------------------------------------- explain
def cmd_explain(argv):
    from . import explain as X
    ap = argparse.ArgumentParser(
        prog="citadel-fastfill explain",
        description="Explain one event of a run, step by step: the event's numbers, the RULES.md row behind each "
                    "step, that row's quotes from the SEC order or the patent, and the code. Default: the first "
                    "FastFill of the newest run in out/.")
    ap.add_argument("--run", default=None, help="output folder of a replay (default: the newest in out/)")
    ap.add_argument("--events", default=None, help="an events.jsonl file instead of --run")
    ap.add_argument("--id", type=int, default=None, help="explain every event of this order")
    ap.add_argument("--rule", default=None, help=f"explain the nth event of this rule ({', '.join(X.RULE_NAMES)})")
    ap.add_argument("--nth", type=int, default=1, help="which event of --rule, 1 = the first (default)")
    ap.add_argument("--list", action="store_true", help="list events (with --rule: of that rule) instead")
    ap.add_argument("--limit", type=int, default=20, help="rows of --list (default 20)")
    ap.add_argument("--short", action="store_true", help="cite the paragraphs instead of quoting them")
    ap.add_argument("--json", action="store_true", help="print the explanation as JSON")
    ap.add_argument("--rules-md", default=os.path.join(ROOT, "RULES.md"), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    path = a.events or os.path.join(a.run or X.latest_run(), "events.jsonl")
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found: replay the recording first (citadel-fastfill replay --data data/<run>)")
    events = X.load_events(path)
    if a.list:
        print(path.replace("\\", "/"))
        print(X.listing(events, a.rule, a.limit))
        return 0
    if not os.path.exists(a.rules_md):
        raise SystemExit(f"{a.rules_md} not found: explain quotes RULES.md from a clone of the repository ({REPO})")
    summ = os.path.join(os.path.dirname(path), "summary.json")
    fits = None
    if os.path.exists(summ):
        with open(summ, encoding="utf-8") as fh:
            summary = json.load(fh)
        p = X.params_of_run(summary)
        fits = (summary.get("toxicity") or {}).get("walk_forward_fits")
        src = "from summary.json"
    else:
        p, src = Params(), "defaults of params.py; no summary.json next to the events"
    chain = X.select(events, a.id, a.rule, a.nth)
    x = X.build(events, chain, p, X.load_rules(a.rules_md), src, fits)
    if a.json:
        print(json.dumps(x, indent=1))
    else:
        print(X.render(x, short=a.short))
    return 0


# ------------------------------------------------------------------------------------------------- sec-check
def sec_rows(summary):
    """[metric, where in the order, the SEC's number, this run's number, note]."""
    st = summary["stats"]
    ff, to, sp = st["fastfill"], st["trade_out"], st["smartprovide"]
    halves = st.get("halves", {})
    sec = SEC_NUMBERS
    orders = st["orders"] or 0
    both = 100.0 * (ff["at_arrival"] + sp["count"]) / orders if orders else 0.0
    nc = ("not comparable: the SEC's share follows eligibility conditions that are not public (SEC 10, 30); ours is "
          "of every public taker order")
    period = "June 2008 - January 2010 of CES flow, here one recording"
    return [
        ["FastFill orders", "para 28", f"~{sec['fastfill_orders']:,}", f"{ff['count']:,}", period],
        ["FastFill share of orders", "para 28", f"~{sec['fastfill_share_of_ces_flow_pct']}% of CES flow",
         _pct(ff["share_pct"]), nc],
        ["FastFill + SmartProvide share", "para 18", f"{sec['both_share_of_algo_engine_pct']}% of algo engine",
         _pct(both), f"{sec['both_share_of_ces_flow_pct']}% of all CES flow; {nc}"],
        ["FastFill triggers caused by an odd lot", "para 29", f"~{sec['odd_lot_pct']}%",
         _pct(ff["odd_lot_pct"]), "crypto books are full of odd lots (RULES.md section 10)"],
        ["trade outs, share of FastFill shares", "para 31", f"<{sec['trade_out_share_pct_max']}%",
         _pct(to["share_of_ff_shares_pct"]),
         f"first half {_pct(halves.get('first_half', {}).get('trade_out_share_pct'))}, second half (out of "
         f"sample) {_pct(halves.get('second_half', {}).get('trade_out_share_pct'))}"],
        ["trade outs filled in full or part", "fn 13", f"~{sec['trade_out_success_pct']}%",
         _pct(to["success_pct"]), f"{to['attempts']:,} attempts; depends on trade_out_latency_ms (OURS)"],
        ["SmartProvide orders", "para 33", f"~{sec['smartprovide_orders']:,}", f"{sp['count']:,}", period],
        ["SmartProvide shares filled in the market", "paras 13, 37", f"~{sec['smartprovide_fill_pct']}%",
         _pct(sp["fill_pct"]), "it fires when the feed it reads has moved away from the SIP"],
        ["SmartProvide orders internalized after", "para 33",
         f"~{100 * sec['smartprovide_internalized_after'] / sec['smartprovide_orders']:.0f}% "
         f"({sec['smartprovide_internalized_after'] // 1000}k/{sec['smartprovide_orders'] // 1000}k)",
         _pct(sp["internalized_after_pct"]),
         f"routed after (fn 12; the choice is OURS): {_pct(sp.get('routed_after_pct'))}"],
        ["  of those, worse than at once", "para 39", "some", _pct(sp["worse_than_immediate_pct"]), ""],
        ["smaller FastFill orders that fared worse", "para 32", "substantial", _pct(ff["small_worse_pct"]),
         "smaller = under one round lot (OURS)"],
        ["larger FastFill orders that fared better", "para 32", "substantial", _pct(ff["large_better_pct"]), ""],
        ["price improvement short of the trigger gap", "para 30", "often", _pct(ff.get("pi_short_pct")),
         "price_improvement_usd (OURS)"],
        ["ordered to pay", "V.C", f"${sec['paid_usd']:,}", "-",
         f"${sec['disgorgement_usd']:,} disgorgement + ${sec['interest_usd']:,} prejudgment interest + "
         f"${sec['penalty_usd']:,} civil penalty, neither admitting nor denying the findings"],
    ]


def cmd_sec_check(argv):
    ap = argparse.ArgumentParser(
        prog="citadel-fastfill sec-check",
        description="The SEC order's numbers next to a run's, with the paragraph or footnote of each, and a check "
                    "that the summary quotes the SEC's numbers unchanged (params.SEC_NUMBERS, RULES.md section 7).")
    ap.add_argument("--run", default=None, help="output folder of a replay (default: the newest in out/)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    run = a.run or _latest_summary_dir()
    summary = _summary(run)
    rows = sec_rows(summary)
    quoted = summary["stats"].get("sec", {})
    mismatch = [k for k, v in quoted.items() if SEC_NUMBERS.get(k) != v]
    if a.json:
        print(json.dumps({"run": run.replace("\\", "/"), "rows": [dict(zip(
            ("metric", "source", "sec", "run", "note"), r)) for r in rows], "sec_numbers_match": not mismatch},
            indent=1))
        return 1 if mismatch else 0
    import textwrap
    meta = summary["meta"]
    print(f"{meta.get('data_dir')}  {meta.get('start_utc', '')[:19]} UTC  {meta.get('minutes', 0):.1f} min  "
          f"{summary['stats']['orders']:,} orders  preset {meta.get('preset')}")
    print(f"{'metric':<44}{'source':<14}{'SEC order':<22}{'this run':>10}")
    note_wrap = textwrap.TextWrapper(width=100, initial_indent=" " * 6, subsequent_indent=" " * 6)
    for m, where, s, r, note in rows:
        print(f"{m:<44}{where:<14}{s:<22}{r:>10}")
        if note:
            print("\n".join(note_wrap.wrap(note)))
    print("SEC numbers quoted unchanged from params.SEC_NUMBERS (RULES.md section 7): " +
          ("yes" if not mismatch else f"NO, these differ: {', '.join(mismatch)}"))
    return 1 if mismatch else 0


# ------------------------------------------------------------------------------------------------- params
def cmd_params(argv):
    ap = argparse.ArgumentParser(
        prog="citadel-fastfill params",
        description="Every parameter of citadel/params.py: the value a run would use after --config and --set, the "
                    "default, and the source (SEC paragraph, patent passage, or OURS).")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    add_config_argument(ap)
    ap.add_argument("--changed", action="store_true", help="only the parameters that differ from the defaults")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = params_from(a.config, a.set)
    d0 = Params()
    rows = []
    for f in fields(Params):
        v, v0 = getattr(p, f.name), getattr(d0, f.name)
        if a.changed and v == v0:
            continue
        rows.append({"name": f.name, "value": _norm(v), "default": _norm(v0), "changed": v != v0,
                     "source": SOURCE.get(f.name, "")})
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0

    def txt(v):
        if isinstance(v, list):
            return ",".join(f"{x[0]}:{x[1]:g}" if isinstance(x, list) else str(x) for x in v)
        return f"{v:g}" if isinstance(v, float) else str(v)
    for r in rows:
        mark = "*" if r["changed"] else " "
        default = f"  (default {txt(r['default'])})" if r["changed"] else ""
        print(f"{mark} {r['name']:<22} {txt(r['value'])}{default}")
        print(f"  {'':<22} {r['source']}")
    print("* = changed by --config / --set.  SEC = paragraph of the SEC order, PAT = US 7,587,347, OURS = our "
          "choice (RULES.md sections 8-9).")
    return 0


LOCAL = {"sensitivity": cmd_sensitivity, "explain": cmd_explain, "sec-check": cmd_sec_check, "params": cmd_params}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if argv[0] in ("-V", "--version"):
        try:
            from importlib.metadata import version
            print(f"citadel-fastfill {version('citadel-fastfill')}")
        except Exception:
            print("citadel-fastfill (not installed: pip install -e .)")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in SCRIPTS:
        return run_script(cmd, rest)
    if cmd in LOCAL:
        return LOCAL[cmd](rest)
    print(f"unknown command {cmd!r}\n", file=sys.stderr)
    print(usage(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
