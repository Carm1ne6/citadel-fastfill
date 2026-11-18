"""The order-handling state machine of SEC paragraphs 26-39 (CES's FastFill and SmartProvide), run on one engine's
views of the market. Every branch names the paragraph it comes from; every number it uses is in params.py.

Per marketable order (one public taker order: its fills on one venue grouped, see sim.py):
1. Principal interest (SEC 26: CES "used proprietary algorithms to decide"; how it decided is not public). OURS: the
   toxicity analyzer of Citadel Investment Group's patent US 7,587,347 (toxicity.py) measures the order's order,
   market condition and execution parameters, scores it and responds (patent FIG. 5, step 506): EXECUTE (quotient
   below TOX_THRESHOLD) = principal interest; otherwise "another market center" = no principal interest, i.e.
   riskless principal in the market (SEC fn 12). Neither the SEC order nor the patent links the two; the link is our
   assumption.
2. FastFill trigger at arrival (SEC 10, 29, 30): the best price on one or more depth-of-book feeds is better than the
   SIP price for the order -> FastFill "immediately internalized the order (or remaining unexecuted quantity) using the
   SIP NBB or NBO" (30), the ENTIRE remaining quantity regardless of displayed size (32), plus PRICE_IMPROVEMENT (27).
   If the trigger rests on odd lots only, the event is flagged odd_lot (29: ~20% of FastFill orders).
3. No principal interest, or over the position limit (SEC 31): FastFill still internalizes, and at the same time sends
   a proprietary "trade out attempt" at the better depth-of-book price; eligible only if that order is no greater than
   90% of the quantity at the best bid/offer on a depth feed (fn 13; OURS: the order is for the customer's whole
   quantity, and the 90% base is the largest single venue's size at the best price). Not eligible -> not FastFill; the
   order is worked riskless principal (fn 12), outside these two algorithms. Result (OURS): the order reaches the venue
   TRADE_OUT_LATENCY_MS after it is sent and is read on the first book message of that venue received from then on:
   filled, in full or in part, if the level is still there (SEC fn 13: ~55% filled in full or part).
4. SmartProvide trigger (SEC 12, 34-35): the SIP price is better than the best price on at least one of the feeds
   SmartProvide references, often only ONE feed (34; default SMARTPROVIDE_FEEDS = coinbase), even when another venue
   shows the SIP price (35) -> display a non-marketable order one tick inside the SIP price (13, 36-37; the one cent
   is OURS) for up to 1 to 5 s by size (13, 38).
   Fills come from later prints on the display venue at or through the displayed price (37: ~18% of shares).
   Venue prices are compared in the venue's own (raw) prices, fixed when the order is sent or displayed.
5. While SmartProvide is handling the order, every market-data update re-checks the FastFill trigger; if it arises,
   FastFill internalizes the whole remaining quantity at the SIP price (30, 38: "until CES chose a new order handling
   strategy (which might be prompted by updates in market data)").
6. Window end (SEC 39): the rest is re-evaluated: "routing an order to the market in an attempt to take liquidity,
   or internalizing the order". With principal interest: internalize at the SIP price then. Without it (fn 12:
   riskless principal): route, i.e. take the displayed direct-feed liquidity (SP_REEVAL_NO_PI = "route", ours).
   Both are compared with immediate internalization at the arrival SIP price (39: some got a worse price).
7. Every trade CES executes for its own account (internalized fills, trade outs) goes to the patent's profit analyzer:
   its running tab of profit (or loss) from the spread at the fill and the later price (patent col. 13-14).
8. Two observations of the SEC get a number: price improvement that "often was not sufficient to equal the price
   difference that had triggered the strategy" (30) and SmartProvide "likely delayed execution" (38): how long a
   SmartProvide order waited for its last fill, against 0 for immediate internalization.
"""
import heapq
import math
import time as _time
from collections import deque

from .sip import SipEmulator
from .toxicity import EXECUTE, ProfitAnalyzer, ToxicityAnalyzer, measure

BUY, SELL = "BUY", "SELL"
VSHORT = {"coinbase": "coinbase", "kraken": "kraken", "bitstamp": "bitstamp", "gemini": "gemini"}


def hms(c_ns):
    s = c_ns / 1e9
    return _time.strftime("%H:%M:%S", _time.gmtime(s)) + ".%03d" % int((s - math.floor(s)) * 1000)


def money(x):
    return "{:,.2f}".format(x)


class Order:
    __slots__ = ("id", "m", "venue", "side", "size", "rem", "print_px", "state", "q", "pi", "arrival_sip",
                 "post_px", "post_raw", "t_post", "expire", "fills", "ref_px", "n_fills")

    def __init__(self, oid, m, venue, side, size, print_px, n_fills=1):
        self.id = oid
        self.m = m
        self.venue = venue
        self.side = side
        self.size = size
        self.rem = size
        self.print_px = print_px
        self.state = "new"
        self.q = None
        self.pi = True
        self.arrival_sip = None
        self.post_px = None
        self.post_raw = None
        self.t_post = None
        self.expire = None
        self.fills = []
        self.ref_px = None
        self.n_fills = n_fills


class Stats(dict):
    def add(self, k, v=1):
        self[k] = self.get(k, 0) + v


class CES:
    def __init__(self, p, market, tape, record=False, label=""):
        self.p = p
        self.market = market
        self.tape = tape
        self.record = record
        self.label = label or f"{p.preset}@{p.sip_latency_ms:g}ms"
        self.min_size = p.round_lot_btc if p.preset == "sip" else 0.0
        self.sip = SipEmulator(p.venues, p.sip_latency_ms, self.min_size)
        self.tox = ToxicityAnalyzer(p)
        self.profit = ProfitAnalyzer(p.profit_mark_s, p.profit_period_s, p.profit_signal_z, p.profit_min_trades)
        self.timers = []
        self._seq = 0
        self.active_sp = []
        self.events = []
        self.rows = []            # (m, features, side, sip fill price) of every order: offline toxicity test
        self.ff_fills = []        # (m, side, venue, fill_px, qty, id) for markouts / fan
        self.ff_log = []          # (m, qty, trade_out) of every FastFill: the trade-out share by half (pipeline.py)
        self.pi_log = []          # (m, principal interest) of every order
        self.sp_delay_ms = []     # arrival to last fill of every SmartProvide order (SEC 38)
        self.to_pending = {}      # venue -> deque of trade outs waiting for that venue's next book message
        self.st = Stats()
        self.gap_bps = []
        self.ff_sizes = []
        self.inventory = 0.0
        self.n_orders = 0
        self.eps = p.eps
        self.to_lat = int(p.trade_out_latency_ms * 1e6)
        self.t_orders = None      # orders before this time are skipped (warm-up, set by sim.Session)
        self.m_end = None         # end of the data once finish() is called

    # ---------------------------------------------------------------------------------------------- plumbing
    def _timer(self, t, kind, obj):
        self._seq += 1
        heapq.heappush(self.timers, (t, self._seq, kind, obj))

    def advance(self, m):
        """Bring this engine to time m: publish SIP quotes and fire timers in time order (no look-ahead)."""
        sip = self.sip
        timers = self.timers
        while True:
            tt = timers[0][0] if timers else None
            ts = sip.next_time()
            if tt is None and ts is None:
                break
            if ts is not None and (tt is None or ts <= tt):
                if ts > m:
                    break
                sip.advance(ts)
                if self.active_sp:
                    self._check_takeovers(ts)
            else:
                if tt > m:
                    break
                _, _, kind, obj = heapq.heappop(timers)
                if kind == "sp_end":
                    self._sp_end(obj, tt)
        self.tox.on_time(m, self.tape.cur_mid)
        self.profit.on_time(m, self.tape.cur_mid)

    def finish(self, m_end):
        """End of data: what is still open is decided at the last message's time with the last known books (a display
        window or a trade out cannot be followed past the data), so every event lies inside the recording."""
        self.m_end = m_end
        self.advance(m_end)
        while self.timers:
            _, _, kind, obj = heapq.heappop(self.timers)
            if kind == "sp_end":
                self._sp_end(obj, m_end, cut=True)
        for venue in list(self.to_pending):
            pend = self.to_pending[venue]
            while pend:
                self._trade_out_result(pend.popleft()[1], m_end)
        self.advance(m_end)

    def on_venue_update(self, m, name, quote):
        self.sip.source(m, name, quote)
        if self.active_sp:
            self._check_takeovers(m)

    def on_book(self, m, name):
        """A book message of venue `name` was applied at time m. A trade out sent to that venue that has reached it
        (TRADE_OUT_LATENCY_MS after sending) is read on this book: the first one received from then on."""
        pend = self.to_pending.get(name)
        while pend and pend[0][0] <= m:
            self._trade_out_result(pend.popleft()[1], m)

    def on_print(self, m, venue, side, px, size):
        """One print (fill) of `venue` at its raw price: it can fill a SmartProvide order displayed there (SEC 37)."""
        if self.active_sp and venue == self.p.sp_post_venue:
            self._sp_fill_from_print(m, side, px, size)

    def on_order(self, m, venue, side, px, size, n_fills=1):
        """One marketable order: a taker order of `venue`, `size` = the sum of its fills, `px` = its last (worst) fill
        price in the venue's own prices (sim.py groups the fills)."""
        if size < self.p.min_order_btc - 1e-12:
            self.st.add("prints_dust")                  # below one share-equivalent: not an order (params.py)
            return
        if self.t_orders is not None and m < self.t_orders:
            self.st.add("orders_warmup")                # the basis has not had one time constant of data yet
            return
        self._new_order(m, venue, side, px - self.market.venues[venue].basis, size, n_fills)

    # ---------------------------------------------------------------------------------------------- triggers
    def _fast(self, side, feeds):
        return self.market.best("ask" if side == BUY else "bid", feeds)

    def _better(self, side, a, b):
        """True if price a is better than price b for a marketable order of `side` (lower ask / higher bid)."""
        return a < b - self.eps if side == BUY else a > b + self.eps

    def _ff_trigger(self, side):
        """SEC 29: "the best bid or offer from one or more of the depth of book feeds was better than the SIP best
        bid or best offer, as applicable"."""
        sip_px = self.sip.price(side)
        if sip_px is None:
            return False
        fast_px, _, _ = self._fast(side, self.p.fastfill_feeds)
        return fast_px is not None and self._better(side, fast_px, sip_px)

    def _sp_trigger(self, side, sip_px):
        """SEC 12: triggered "when the SIP NBB or NBO, as applicable, was better than the best price from at least one
        of the depth of book feeds"; SEC 34: SmartProvide "referenced only one depth of book feed for many securities
        and fewer than all of the depth of book feeds for other securities", and "sometimes could be triggered when the
        difference existed between the SIP and only one of the depth of book feeds SmartProvide referenced, and not the
        others". So each referenced feed is compared with the SIP on its own (with one feed, the default, this is the
        same as comparing with that feed). Returns (triggered, that feed's best price, that feed)."""
        book_side = "ask" if side == BUY else "bid"
        for name in self.p.smartprovide_feeds:
            ref_px, _, _ = self.market.best(book_side, (name,))
            if ref_px is not None and self._better(side, sip_px, ref_px):
                return True, ref_px, name
        return False, None, None

    # ---------------------------------------------------------------------------------------------- features
    def _features(self, m, venue, side, size, print_px, sip_px, fill_px):
        """The patent's order, market condition and execution parameters of this order (toxicity.measure), from what
        this engine sees at time m: its SIP, the direct feeds, the tape of past prints and mids."""
        mk = self.market
        feeds = self.p.fastfill_feeds
        bid, bsz, ask, asz = mk.top(feeds)
        return measure(side, size, print_px, sip_px, fill_px, self.sip.nbb, self.sip.nbo, self.sip.size(side),
                       self.sip.venues_at(side), bid, bsz, ask, asz,
                       mk.depth_within("ask" if side == BUY else "bid", feeds, 10.0),
                       self.tape.print_rate(venue, m), self.tape.mid_at(m - 1_000_000_000))

    # ---------------------------------------------------------------------------------------------- orders
    def _new_order(self, m, venue, side, px, size, n_fills=1):
        sip_px = self.sip.price(side)
        fast_px, _, _ = self._fast(side, self.p.fastfill_feeds)
        if sip_px is None or fast_px is None:
            self.st.add("orders_no_quote")
            return
        self.n_orders += 1
        self.st.add("orders")
        self.st.add("orders_btc", size)
        self.st.add("orders_fills", n_fills)
        o = Order(self.n_orders, m, venue, side, size, px, n_fills)
        fill_px = sip_px - self.p.price_improvement_usd if side == BUY else sip_px + self.p.price_improvement_usd
        x = self._features(m, venue, side, size, px, sip_px, fill_px)
        q = self.tox.quotient(x)
        o.q = q if q is not None else self.tox.base_rate()
        # SEC 26 "principal interest"; OURS: decided by the patent's toxicity analyzer (FIG. 5 step 506)
        o.pi = self.tox.respond(q) == EXECUTE
        self.tox.observe(m, x, side, fill_px, size)
        self.pi_log.append((m, o.pi))
        if self.record:
            self.rows.append((m, x, side, fill_px))
        if not o.pi:
            self.st.add("no_principal_interest")
        if self._better(side, fast_px, sip_px):                     # SEC 29-30: FastFill at arrival
            self.st.add("ff_trigger_arrival")
            if self._fastfill(o, m, "arrival"):
                return
            self.st.add("other_orders")
            return
        trig, ref_px, ref_feed = self._sp_trigger(side, sip_px)    # SEC 12, 34: SmartProvide
        if trig:
            self._smartprovide(o, m, sip_px, ref_px, ref_feed)
            return
        self.st.add("other_orders")                                  # handled by CES's other logic, not modelled

    def _fastfill(self, o, m, phase):
        p = self.p
        side = o.side
        feeds = p.fastfill_feeds
        mk = self.market
        sip_px = self.sip.price(side)
        fast_px, fast_sz, fast_at = self._fast(side, feeds)
        if sip_px is None or fast_px is None or not self._better(side, fast_px, sip_px):
            return False
        qty = o.rem
        book_side = "ask" if side == BUY else "bid"
        # SEC 31 / fn 13: trade out attempt when there is no principal interest or the position limit would break
        inv_after = self.inventory + (-qty if side == BUY else qty)
        over_limit = p.position_limit_btc > 0 and abs(inv_after) > p.position_limit_btc + self.eps
        trade_out = None
        if (not o.pi) or over_limit:
            # fn 13: "no greater than 90% of the quantity associated with the best bid or offer, as relevant, from one
            # or more of the depth of book feeds". OURS: the proprietary order is for the customer's whole quantity,
            # sent to the venue with the most size at the best price, and that venue's size is the 90% base.
            tv, tsz = max(fast_at, key=lambda a: a[1])
            if qty > p.trade_out_cap * tsz + self.eps:              # fn 13 eligibility criterion
                if phase == "arrival":
                    self.st.add("ff_ineligible_trade_out")
                return False
            # the price in the venue's own prices, fixed now: the check later must not move with the basis
            trade_out = (tv, fast_px, fast_px + mk.venues[tv].basis, qty)
        fill_px = sip_px - p.price_improvement_usd if side == BUY else sip_px + p.price_improvement_usd
        gap_top = abs(sip_px - fast_px)
        gap_bps = gap_top / sip_px * 1e4
        gap_exec, qty_better = mk.walk_gap(side, feeds, sip_px, qty)
        rl = mk.best_min(book_side, feeds, p.round_lot_btc)
        odd_lot = not (rl is not None and self._better(side, rl, sip_px))   # SEC 29: trigger caused by an odd lot
        one = mk.best_min(book_side, feeds, p.min_order_btc)
        sub_share = not (one is not None and self._better(side, one, sip_px))   # only levels under one share-equiv.
        vwap, got = mk.walk_vwap(side, feeds, qty)                         # SEC 32: displayed liquidity alone
        if got + 1e-12 < qty or vwap is None:
            displayed_better = False
        else:
            displayed_better = self._better(side, vwap, fill_px)
        # book-keeping
        was_sp = o.state == "sp"
        o.fills.append((fill_px, qty))
        o.rem = 0.0
        o.state = "ff"
        self.inventory = inv_after
        st = self.st
        st.add("ff_orders")
        st.add("ff_btc", qty)
        st.add("ff_" + phase)
        st.add("ff_gap_exec_usd", gap_exec)
        st.add("ff_gap_top_usd", gap_top * qty)
        st.add("ff_notional_usd", fill_px * qty)
        if odd_lot:
            st.add("ff_odd_lot")
        if sub_share:
            st.add("ff_sub_share")
        small = qty < p.round_lot_btc
        st.add("ff_small" if small else "ff_large")
        if small and displayed_better:
            st.add("ff_small_worse")
        if (not small) and (not displayed_better):
            st.add("ff_large_better")
        if not o.pi:
            st.add("ff_no_pi")
        if p.price_improvement_usd < gap_top - self.eps:
            st.add("ff_pi_short")        # SEC 30: price improvement "not sufficient to equal the price difference"
        self.gap_bps.append(gap_bps)
        self.ff_sizes.append(qty)
        self.ff_fills.append((m, side, o.venue, fill_px, qty, o.id))
        self.ff_log.append((m, qty, trade_out is not None))
        bid, _, ask, _ = mk.top(feeds)
        self.profit.on_fill(m, o.venue, side, fill_px, qty, bid, ask)    # patent: log the spread at the fill
        ev = None
        if self.record:
            ev = self._event(m, o, "FASTFILL", qty, sip_px, fast_px, gap_exec, gap_bps, odd_lot,
                             outcome=("filled %s @ SIP" % _q(qty)) + ("" if phase == "arrival" else
                                                                       (" (took over SmartProvide)" if phase == "during_sp"
                                                                        else " (SmartProvide window end)")),
                             extra={"phase": phase, "fill_px": fill_px, "gap_top_usd": gap_top,
                                    "qty_better": qty_better, "displayed_better": displayed_better,
                                    "displayed_vwap": vwap, "sub_share": sub_share})
        if trade_out is not None:
            tv, tpx, traw, tq = trade_out
            st.add("to_attempts")
            st.add("to_ff_btc", qty)
            if over_limit:
                st.add("to_position_limit")
            due = m + self.to_lat
            if self.m_end is not None:
                due = m                     # after the data ends: read on the last known book (finish)
            self.to_pending.setdefault(tv, deque()).append((due, (o, tv, tpx, traw, tq, m, side, ev)))
        if was_sp:
            if o in self.active_sp:
                self.active_sp.remove(o)
            self._finish_sp(o, m, how="internalized")
        return True

    def _trade_out_result(self, obj, t):
        o, venue, px, raw_px, qty, m0, side, ev_ff = obj
        v = self.market.venues.get(venue)
        avail = 0.0
        if v is not None and v.live:
            avail = self.market.size_through(v, "ask" if side == BUY else "bid", raw_px)
        filled = min(qty, avail)
        st = self.st
        if filled > 1e-12:
            st.add("to_success")
            st.add("to_filled_btc", filled)
            if filled + 1e-12 >= qty:
                st.add("to_full")
        self.inventory += filled if side == BUY else -filled
        if filled > 1e-12:                         # the dealer's own trade: opposite side to the customer's order
            bid, _, ask, _ = self.market.top(self.p.fastfill_feeds)
            self.profit.on_fill(t, venue, SELL if side == BUY else BUY, px, filled, bid, ask, flow=False)
        if self.record:
            outcome = ("FILLED %s" % _q(filled)) if filled + 1e-12 >= qty else \
                (("PARTIAL %s of %s" % (_q(filled), _q(qty))) if filled > 1e-12 else "NO FILL (level gone)")
            sip_px = ev_ff["sip_px"] if ev_ff else None
            gap_usd = abs(sip_px - px) * filled if sip_px is not None else 0.0
            ev = self._event(m0, o, "TRADE_OUT", qty, sip_px, px, gap_usd,
                             ev_ff["gap_bps"] if ev_ff else 0.0, ev_ff["odd_lot"] if ev_ff else False,
                             outcome=outcome, extra={"venue_out": venue, "filled": filled, "px": px,
                                                     "resolved_ms": (t - m0) / 1e6})
            ev["venue"] = venue

    def _smartprovide(self, o, m, sip_px, ref_px, ref_feed):
        p = self.p
        side = o.side
        o.state = "sp"
        o.arrival_sip = sip_px
        o.ref_px = ref_px
        # SEC 36-37: displayed "at a price that was less than the SIP NBO for a buy order or greater than the SIP NBB
        # for a sell order", e.g. NBO $10.01 -> bid $10.00: one cent inside, then rounded away from the SIP onto the
        # display venue's price grid (OURS; Coinbase $0.01: exactly one cent inside; Kraken $0.10: 1 to 10 cents)
        pv = self.market.venues.get(p.sp_post_venue)
        basis = pv.basis if pv is not None else 0.0
        vt = dict(p.venue_ticks).get(p.sp_post_venue, p.tick_usd)
        raw = (sip_px - p.tick_usd + basis) if side == BUY else (sip_px + p.tick_usd + basis)
        raw = (math.floor(raw / vt + 1e-9) * vt) if side == BUY else (math.ceil(raw / vt - 1e-9) * vt)
        o.post_raw = round(raw, 8)                 # the displayed price in the venue's own prices
        o.post_px = round(raw - basis, 8)          # the same price on the reference scale, what the customer gets
        # SEC 13, 38: "for up to one to five seconds, depending on the size of the order". OURS: the size scale and
        # its direction (larger orders displayed longer), linear between SP_SMALL_BTC and SP_LARGE_BTC
        x = (o.size - p.sp_small_btc) / (p.sp_large_btc - p.sp_small_btc)
        x = 0.0 if x < 0 else (1.0 if x > 1 else x)
        window = p.sp_display_min_s + x * (p.sp_display_max_s - p.sp_display_min_s)
        o.t_post = m
        o.expire = m + int(window * 1e9)
        self.active_sp.append(o)
        self._timer(o.expire, "sp_end", o)
        st = self.st
        st.add("sp_orders")
        st.add("sp_btc", o.size)
        if self.record:
            self._event(m, o, "SMARTPROVIDE", o.size, sip_px, ref_px, 0.0, abs(ref_px - sip_px) / sip_px * 1e4,
                        False, outcome="display %s %s for %.1f s" % ("bid" if side == BUY else "offer",
                                                                    money(o.post_px), window),
                        extra={"post_px": o.post_px, "window_s": window, "ref_gap_usd": abs(ref_px - sip_px),
                               "ref_feed": ref_feed, "post_venue": p.sp_post_venue})

    def _sp_fill_from_print(self, m, aggr, raw_px, size):
        """A print of the display venue at its own (raw) price fills displayed orders at or through their price."""
        avail = size
        basis = self.market.venues[self.p.sp_post_venue].basis
        for o in list(self.active_sp):
            if avail <= 1e-12:
                break
            if m <= o.t_post:
                continue
            if o.side == BUY and aggr == SELL and raw_px <= o.post_raw + self.eps:
                pass
            elif o.side == SELL and aggr == BUY and raw_px >= o.post_raw - self.eps:
                pass
            else:
                continue
            q = min(o.rem, avail)
            o.rem -= q
            avail -= q
            o.fills.append((o.post_px, q))
            self.st.add("sp_filled_btc", q)
            if self.record:
                px = raw_px - basis
                self._event(m, o, "SP_FILLED", q, o.arrival_sip, px, abs(o.arrival_sip - o.post_px) * q,
                            abs(o.arrival_sip - o.post_px) / o.arrival_sip * 1e4, False,
                            outcome="filled %s @ %s, %s better than SIP" % (_q(q), money(o.post_px),
                                                                            money(abs(o.arrival_sip - o.post_px))),
                            extra={"fill_px": o.post_px, "print_px": px, "window_s": (o.expire - o.t_post) / 1e9})
            if o.rem <= 1e-12:
                o.rem = 0.0
                self.active_sp.remove(o)
                o.state = "sp_done"
                self._finish_sp(o, m, how="filled")

    def _check_takeovers(self, m):
        for o in list(self.active_sp):
            if self._ff_trigger(o.side):
                self._fastfill(o, m, "during_sp")

    def _sp_end(self, o, t, cut=False):
        """SEC 39 at the end of the display window (cut = the data ended first: decided at its last message)."""
        if o.state != "sp" or o.rem <= 1e-12:
            return
        if cut:
            self.st.add("sp_cut_by_data_end")
        if self._ff_trigger(o.side) and self._fastfill(o, t, "sp_window_end"):
            return
        if o in self.active_sp:
            self.active_sp.remove(o)
        p = self.p
        side = o.side
        qty = o.rem
        sip_px = self.sip.price(side)
        if sip_px is None:
            sip_px = o.arrival_sip
        action = "internalize"
        fill_px = None
        if not o.pi and p.sp_reeval_no_pi == "route":
            # SEC 39: "routing an order to the market in an attempt to take liquidity"; fn 12: without a principal
            # interest CES worked the order "in the market on a riskless principal basis"
            fill_px = self._take(side, qty)
            if fill_px is not None:
                action = "route"
        if fill_px is None:
            # SEC 39: "internalizing the order", at the SIP price at that time (SEC 27)
            fill_px = sip_px - p.price_improvement_usd if side == BUY else sip_px + p.price_improvement_usd
            self.inventory += -qty if side == BUY else qty
            bid, _, ask, _ = self.market.top(p.fastfill_feeds)
            self.profit.on_fill(t, o.venue, side, fill_px, qty, bid, ask)
        o.fills.append((fill_px, qty))
        o.rem = 0.0
        o.state = "sp_reeval"
        self.st.add("sp_reeval_orders")
        self.st.add("sp_reeval_btc", qty)
        diff = (fill_px - o.arrival_sip) if side == BUY else (o.arrival_sip - fill_px)   # >0 = worse for customer
        if self.record:
            word = "worse than" if diff > self.eps else ("better than" if diff < -self.eps else "same as")
            verb = "routed" if action == "route" else "internalized"
            note = " (no principal interest)" if action == "route" else ""
            self._event(t, o, "SP_REEVAL", qty, sip_px, o.arrival_sip, abs(diff) * qty, abs(diff) / o.arrival_sip * 1e4,
                        False, outcome="%s %s @ %s%s, %s %s at arrival" % (
                            verb, _q(qty), money(fill_px), note, word, money(o.arrival_sip)),
                        extra={"fill_px": fill_px, "arrival_sip": o.arrival_sip, "diff_usd_per_btc": diff,
                               "action": action})
        self._finish_sp(o, t, how="routed" if action == "route" else "internalized")

    def _take(self, side, qty):
        """Average price of taking qty from the displayed direct-feed liquidity (riskless principal: the customer
        gets the market's prices). The part beyond the displayed size is priced at the last level. None = no book."""
        book_side = "ask" if side == BUY else "bid"
        rem = qty
        cost = 0.0
        last = None
        for px, sz, _ in self.market.levels(book_side, self.p.fastfill_feeds):
            if rem <= 1e-12:
                break
            take = sz if sz < rem else rem
            cost += px * take
            rem -= take
            last = px
        if last is None:
            return None
        if rem > 1e-12:
            cost += last * rem
        return cost / qty

    def _finish_sp(self, o, t, how):
        tot = sum(q for _, q in o.fills)
        if tot <= 0:
            return
        avg = sum(px * q for px, q in o.fills) / tot
        worse = (avg > o.arrival_sip + self.eps) if o.side == BUY else (avg < o.arrival_sip - self.eps)
        self.st.add("sp_done")
        self.sp_delay_ms.append((t - o.m) / 1e6)     # SEC 38: immediate internalization would take 0 ms
        if how == "internalized":
            self.st.add("sp_internalized_after")
            if worse:
                self.st.add("sp_worse_than_immediate")
                self.st.add("sp_worse_usd", abs(avg - o.arrival_sip) * tot)
        elif how == "routed":
            self.st.add("sp_routed_after")
            if worse:
                self.st.add("sp_routed_worse")
                self.st.add("sp_routed_worse_usd", abs(avg - o.arrival_sip) * tot)
        o.state = "done"

    # ---------------------------------------------------------------------------------------------- events
    def _event(self, m, o, rule, size, sip_px, depth_px, gap_usd, gap_bps, odd_lot, outcome, extra=None):
        ev = {
            "m": m, "t": int(m // 1_000_000), "id": o.id, "venue": o.venue, "side": o.side, "size": size,
            "rule": rule, "sip_px": sip_px, "depth_px": depth_px, "gap_usd": gap_usd, "gap_bps": gap_bps,
            "odd_lot": bool(odd_lot), "principal_interest": bool(o.pi), "toxicity": float(o.q),
            "outcome": outcome,
        }
        if extra:
            ev.update(extra)
        self.events.append(ev)
        return ev


def _q(x):
    return ("%.4f" % x).rstrip("0").rstrip(".") + " BTC" if x >= 0.0001 else "%.8f BTC" % x
