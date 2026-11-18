"""Every number the rebuild uses, with where it comes from.

SEC = In the Matter of Citadel Securities LLC, Securities Act Release No. 10280 (Jan 13, 2017), paragraph numbers.
PAT = US 7,587,347 B2 (Griffin, Andresen; Citadel Investment Group), column:line on the scan in
      docs/patents/US7587347.pdf; its division US 7,987,128 B2 has the same specification.
OURS = not fixed by any document; chosen by us and reported as a parameter.
"""
from dataclasses import dataclass, field, asdict

VENUES = ("coinbase", "kraken", "bitstamp", "gemini")


@dataclass
class Params:
    # --- market views -------------------------------------------------------------------------------------------
    venues: tuple = VENUES
    # "sip": the SIP NBBO = best ROUND-LOT quote of each venue, consolidated, published late (SEC 5, 19, 22, 23).
    # "latency": same consolidated top of book including odd lots, only delayed (isolates SEC 22).
    # SPEC named "latency" the headline; the headline is "sip" because the SEC's SIP carries round lots only (SEC 5:
    # "The SIPs do not disseminate odd lot quotations") and SEC 29 measures odd-lot triggers, which "latency" cannot
    # produce. Both presets are always reported side by side (report.py).
    preset: str = "sip"
    sip_latency_ms: float = 5.0          # SEC fn 9: 4.04 ms (NYSE SIP) / 5.42 ms (Nasdaq SIP), start of 2010
    # SEC 5: a round lot is "at least 100 shares". OURS: 0.05 BTC (about $3,200 at the recording's price). No document
    # fixes a share price; at the order's own example price ($10.00, SEC 11, 13, fn 11) 100 shares are $1,000, about
    # 0.0155 BTC, which the round-lot sensitivity reports with 0.01 and 0.10 BTC.
    round_lot_btc: float = 0.05
    # --- one national market out of four crypto venues ---------------------------------------------------------------
    # US exchanges trade one fungible stock at one price (Reg NMS); crypto venues keep persistent differences of a few
    # bps (fees, settlement). OURS: shift each venue onto the reference venue's price scale by its slow basis
    # (time-weighted EMA of venue mid minus reference mid, past data only) before consolidating.
    basis_adjust: bool = True
    basis_ref: str = "coinbase"
    basis_tau_s: float = 60.0
    # Even after the basis the four books cross each other by a few dollars most of the time. OURS, after Reg NMS
    # Rule 610(d) and SEC 23 ("how locked and crossed markets are resolved"): a level of another venue that locks or
    # crosses the reference venue's best OPPOSITE quote is not counted, in both views (its bids must stay below the
    # reference ask, its asks above the reference bid).
    clip_crossed: bool = True
    # --- orders -------------------------------------------------------------------------------------------------
    # Every public taker order (its fills on one venue grouped, sim.py) is one marketable order (SEC 1-2). OURS: orders
    # below one share-equivalent (ROUND_LOT / 100 = 0.0005 BTC, ~$32) are dust, not orders, and are skipped.
    min_order_btc: float = 0.0005
    # A floor for book levels: a level smaller than this is not a quote in either view (SEC 5: an odd lot is a
    # quotation "for less than 100 shares"; OURS: equity quotes are whole shares, so never less than one share; SEC 23:
    # views differ by "how odd lots are treated"). OURS: off (0). At one share-equivalent (0.0005) it removes
    # Coinbase's sub-share levels, Coinbase's spread widens, and the other three venues cross each other inside it: the
    # SIP of run_final is crossed or locked in 260 of the 1,199 one-second samples instead of 1. Instead every FastFill
    # reports whether its trigger rested only on levels smaller than one share-equivalent
    # (stats.fastfill.sub_share_pct). The Kraken checksum always covers every level.
    min_level_btc: float = 0.0
    # OURS: no order is handled before the basis has had one time constant of data (BASIS_TAU_S): before that the
    # basis is a single, noisy price difference.
    warmup_s: float = 60.0
    # --- CES internalization ------------------------------------------------------------------------------------
    price_improvement_usd: float = 0.0   # SEC 27, fn 11 ($10.00 -> $9.999). OURS: 0, the BTC books quote in $0.01
    tick_usd: float = 0.01               # SEC 13: "such as $10.00" under a $10.01 NBO; SEC 37: "at least one penny";
    #                                      one cent exactly is OURS
    venue_ticks: tuple = (("coinbase", 0.01), ("kraken", 0.1), ("bitstamp", 0.01), ("gemini", 0.01))  # price grids
    fastfill_feeds: tuple = VENUES       # SEC 10: "one or more of the depth of book feeds that FastFill referenced"
    # --- trade out (SEC 31, fn 13) -------------------------------------------------------------------------------
    trade_out_cap: float = 0.90          # SEC fn 13: "no greater than 90% of the quantity associated with the best"
    #                                      (an eligibility condition; OURS: that size is measured on the single venue
    #                                      with the most size at the best price, ces.py)
    # OURS: the trade out reaches its venue this long after it is sent and is read on the first book message of that
    # venue received from then on (Coinbase's level2_batch arrives every 51 / 60 / 62 ms, p10 / p50 / p90, in
    # run_final; sensitivity 5 / 50 / 250 ms in the report).
    trade_out_latency_ms: float = 50.0
    position_limit_btc: float = 0.0      # SEC 31 names a position limit; its value is not public. OURS: 0 = off
    # --- SmartProvide (SEC 12-13, 33-39) ---------------------------------------------------------------------------
    # SEC 12, 34: triggered when the SIP beats "at least one of" the feeds SmartProvide references, which "referenced
    # only one depth of book feed for many securities"; SEC 34: "at times" it was triggered when the SIP price came from
    # an exchange whose feed it did not reference. Which one is OURS: Coinbase (SPEC), the venue that sets the price
    # most often. Kraken quotes on a $0.10 grid, so its best price is behind the SIP on nearly every order: with Kraken
    # the trigger is a standing condition, not an event. Kraken is reported as a sensitivity row
    # (pipeline.FEED_VARIANTS).
    smartprovide_feeds: tuple = ("coinbase",)
    sp_post_venue: str = "coinbase"      # OURS: the order is displayed on the venue whose feed SmartProvide reads
    sp_display_min_s: float = 1.0        # SEC 13, 38: "displayed for up to one to five seconds"
    sp_display_max_s: float = 5.0
    sp_small_btc: float = 0.10           # OURS: 1 s at or below this size ...
    sp_large_btc: float = 1.00           # OURS: ... 5 s at or above this size, linear in between (the direction too)
    # SEC 39: the unfilled rest is re-evaluated: "displaying an order in the market using another strategy, routing an
    # order to the market in an attempt to take liquidity, or internalizing the order". OURS: with a principal interest
    # (SEC 26) internalize at the SIP price; without one, "route" = take the displayed liquidity, the riskless
    # principal handling of fn 12 ("internalize" = internalize every order, the simpler reading)
    sp_reeval_no_pi: str = "route"
    # --- toxicity analyzer (PAT col. 9:36-10:19) and principal interest (SEC 26) -----------------------------------
    tox_horizon_s: float = 5.0           # OURS: the patent gives no horizon; also tested at 1 and 30 s
    # OURS: principal interest when P(no profit or a loss) is below this. Neither document gives a value; two passages
    # bound it. PAT col. 9:1-5: with a standard investing population "a certain level of toxic trades ... is to be
    # expected due to the random arrival of orders in conjunction with the random movement of equity bid and offer
    # levels" (at 5 s about half of all fills lose, so a 0.5 cut screens half of an ordinary flow); SEC 31: FastFill
    # without a principal interest was "a narrow set of circumstances", trade outs "less than 6.9% of the shares".
    # 0.7 screens only orders the analyzer expects to lose at least 7 times in 10; pipeline.TOX_GRID reports 0.5-0.9.
    tox_threshold: float = 0.7
    tox_refit_s: float = 30.0            # OURS: walk-forward refit period of the running model
    tox_min_train: int = 300             # OURS: outcomes needed before the analyzer is used
    tox_ridge: float = 1.0               # OURS: L2 penalty of the logistic fit
    # --- profit analyzer (PAT col. 13:45-14:11, claims 18-19, 33-35) -------------------------------------------------
    profit_mark_s: float = 5.0           # OURS: when a fill's profit (or loss) is marked ("after a trade has been
    #                                      executed", col. 13:60-62)
    profit_period_s: float = 60.0        # OURS: the patent's periods are "days, weeks, or months" (col. 14:6-7)
    profit_signal_z: float = 2.0         # OURS: a period this many standard errors below the expected level signals
    #                                      "the need to take corrective action" (col. 14:8-10)
    profit_min_trades: int = 20          # OURS: trades of an order flow before its expected level is used
    # --- misc -----------------------------------------------------------------------------------------------------
    eps: float = 1e-9

    def as_dict(self):
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, tuple):
                d[k] = list(v)
        return d


SOURCE = {
    "basis_adjust": "OURS (crypto venues are not one Reg NMS market; see params.py)",
    "basis_tau_s": "OURS",
    "clip_crossed": "OURS, after Reg NMS Rule 610(d) and SEC 23",
    "min_order_btc": "OURS: one share-equivalent = ROUND_LOT / 100",
    "warmup_s": "OURS: one basis time constant before the first order",
    "min_level_btc": "OURS: off; SEC 5 (odd lots: 'quotations for less than 100 shares'), SEC 23 ('how odd lots are "
                     "treated')",
    "preset": "OURS: 'sip' follows SEC 5/19/23 (round lots only) + SEC 22 (latency); 'latency' isolates SEC 22",
    "sip_latency_ms": "SEC fn 9 (4.04 / 5.42 ms in early 2010; 0.49 / 0.92 ms in early 2016)",
    "round_lot_btc": "SEC 5 (100 shares); BTC size is OURS",
    "price_improvement_usd": "SEC 27, fn 11; value OURS",
    "fastfill_feeds": "SEC 10, 20, 29 (which feeds FastFill referenced is not stated; OURS: all four)",
    "trade_out_cap": "SEC fn 13 (eligibility: no greater than 90% of the size at the best price); measuring that size "
                     "on the single venue with the most size is OURS",
    "trade_out_latency_ms": "OURS (read on the venue's first book message from then on)",
    "position_limit_btc": "SEC 31 (value not public; OURS: off)",
    "smartprovide_feeds": "SEC 12, 34-35 (SIP better than at least one referenced feed; often only one feed); "
                          "choice of feed OURS (Coinbase, as SPEC; Kraken reported as a sensitivity)",
    "sp_post_venue": "OURS",
    "sp_display_min_s": "SEC 13, 38",
    "sp_display_max_s": "SEC 13, 38",
    "sp_small_btc": "OURS (the size scale of SEC 13, 38 and its direction)",
    "sp_large_btc": "OURS (the size scale of SEC 13, 38 and its direction)",
    "sp_reeval_no_pi": "SEC 39 (the three options) + SEC 26, fn 12 (no principal interest -> riskless principal); "
                       "choice OURS",
    "tox_horizon_s": "OURS (PAT col. 9:65: 'correlating these parameters with the outcome of trades', no horizon)",
    "tox_threshold": "OURS, bounded by PAT col. 9:1-5 (an expected level of toxic trades) and SEC 31 ('narrow', <6.9%)",
    "tox_refit_s": "OURS",
    "tox_min_train": "OURS",
    "tox_ridge": "OURS",
    "profit_mark_s": "OURS (PAT col. 13:60-62: profit 'can only be determined after a trade has been executed')",
    "profit_period_s": "OURS (PAT col. 14:6-7: 'over various periods of time (days, weeks, or months)')",
    "profit_signal_z": "OURS (PAT col. 14:8-10: 'Deviations from these averages ... signal')",
    "profit_min_trades": "OURS",
    "venues": "SPEC: four public BTC/USD venues stand for the exchanges (SEC 4, 6)",
    "basis_ref": "OURS",
    "tick_usd": "SEC 13 ('such as $10.00' under a $10.01 NBO), 37 ('at least one penny'); the one cent is OURS "
                "(RULES.md section 4); Coinbase BTC-USD price grid (NOTES.md)",
    "venue_ticks": "venue price grids (Kraken BTC/USD $0.1, others $0.01), checked in NOTES.md",
    "eps": "numerical tolerance",
}

SEC_NUMBERS = {
    "fastfill_orders": 2700000,            # SEC 28
    "fastfill_share_of_ces_flow_pct": 0.4, # SEC 28
    "both_share_of_algo_engine_pct": 2.6,  # SEC 18
    "both_share_of_ces_flow_pct": 0.6,     # SEC 18
    "odd_lot_pct": 20,                     # SEC 29
    "trade_out_share_pct_max": 6.9,        # SEC 31
    "trade_out_success_pct": 55,           # SEC fn 13
    "smartprovide_orders": 690000,         # SEC 33
    "smartprovide_internalized_after": 490000,  # SEC 33
    "smartprovide_fill_pct": 18,           # SEC 13, 37
    "disgorgement_usd": 5200000,           # Section V.C of the order
    "interest_usd": 1465268,
    "penalty_usd": 16000000,
    "paid_usd": 22665268,
}


def with_overrides(p, items):
    """Apply KEY=VALUE overrides (run.py / live.py / export_film.py --set, and --config files through
    citadel/config.py) to a Params, parsing by the field's type. A list is comma-separated
    (smartprovide_feeds=coinbase,kraken); a list of pairs is NAME:VALUE items (venue_ticks=coinbase:0.01,kraken:0.1)."""
    from dataclasses import fields, replace
    types = {f.name: f.type for f in fields(Params)}
    kw = {}
    for it in items or ():
        k, _, v = it.partition("=")
        k = k.strip().lower()
        if k not in types:
            raise SystemExit(f"unknown parameter {k!r}; see citadel/params.py")
        cur = getattr(p, k)
        if isinstance(cur, bool):
            kw[k] = v.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(cur, int):
            kw[k] = int(v)
        elif isinstance(cur, float):
            kw[k] = float(v)
        elif isinstance(cur, tuple):
            parts = [x.strip() for x in v.split(",") if x.strip()]
            if cur and all(isinstance(x, tuple) and len(x) == 2 for x in cur):
                typ = type(cur[0][1])
                pairs = []
                for x in parts:
                    name, sep, val = x.partition(":")
                    if not sep:
                        raise SystemExit(f"{k}: expected NAME:VALUE items, e.g. coinbase:0.01, got {x!r}")
                    pairs.append((name.strip(), typ(val.strip())))
                kw[k] = tuple(pairs)
            else:
                kw[k] = tuple(parts)
        else:
            kw[k] = v.strip()
    return replace(p, **kw)
