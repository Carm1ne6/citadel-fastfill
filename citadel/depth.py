"""The direct-feed view: books built from the depth-of-book feeds, consolidated across venues, any size (SEC 6-7, 20).

"Market participants can use these depth of book feeds to construct their own view of the full displayed limit order
book ... Market participants also can consolidate multiple depth of book feeds." (SEC 7)

Two adjustments make four crypto venues behave like one US national market (both OURS, both documented in RULES.md):
- basis: each venue's prices are shifted by its slow price difference to the reference venue (venue.basis, sim.py).
  Crypto venues keep persistent premia of a few bps; US exchanges trade one fungible stock at one price.
- crossed quotes: even after the basis, the four books cross each other by a few dollars most of the time (fees stop
  arbitrage). Under Reg NMS a venue may not display a quote that locks or crosses another venue's protected quote
  (Rule 610(d)), and SEC para 23 names "how locked and crossed markets are resolved" as a reason views differ. We
  resolve it the same way for both views: a level of another venue that locks or crosses the reference venue's best
  opposite quote is not counted (`clip`): another venue's bid must be below the reference ask, its ask above the
  reference bid. Two other venues can still cross each other inside a wide reference spread; that is left as it is
  and counted (RULES.md section 2).
"""
import heapq

BUY, SELL = "BUY", "SELL"


class Market:
    def __init__(self, venues, ref=None, clip=False, min_level=0.0):
        self.venues = venues             # name -> Venue
        self.ref = ref                   # reference ("protected") venue for the crossed-quote rule
        self.clip = clip and ref is not None
        # A level smaller than one share-equivalent is not a quote in either view (SEC 5: odd lots are under 100
        # shares; OURS: equity quotes are whole shares); the same floor that makes a print dust (params.MIN_LEVEL_BTC).
        self.min_level = min_level

    def _live(self, names):
        for n in names:
            v = self.venues.get(n)
            if v is not None and v.live:
                yield v

    def ref_top(self):
        """Raw best bid / ask of the reference venue (basis 0) of at least one share-equivalent, or (None, None)."""
        r = self.venues.get(self.ref) if self.ref else None
        if r is None or not r.live:
            return None, None
        b, _ = r.bids.best_min(self.min_level)
        a, _ = r.asks.best_min(self.min_level)
        return b, a

    def _limits(self, v):
        """(bid_cap, ask_floor) for venue v: its adjusted bids must stay below the reference ASK, its asks above the
        reference BID (a level that locks or crosses the reference venue's best OPPOSITE quote is not counted). A bid
        may tie or improve the reference bid, an ask may tie or improve the reference ask. None = no limit."""
        if not self.clip or v.name == self.ref:
            return None, None
        ref_bid, ref_ask = self.ref_top()
        return ref_ask, ref_bid

    def vbest(self, v, book_side, min_size=0.0, limits=None):
        """Best adjusted level of one venue that is at least min_size (and one share-equivalent) and does not lock or
        cross the reference. Returns (price, size) or (None, 0)."""
        side = v.asks if book_side == "ask" else v.bids
        bcap, afloor = limits if limits is not None else self._limits(v)
        lim = afloor if book_side == "ask" else bcap
        basis = v.basis
        ms = min_size if min_size > self.min_level else self.min_level
        if lim is None:
            p, s = side.best_min(ms)
            return (None, 0.0) if p is None else (p - basis, s)
        thr = ms - 1e-12
        for p, s in side.levels():
            pa = p - basis
            if book_side == "ask":
                if pa <= lim + 1e-9:
                    continue
            elif pa >= lim - 1e-9:
                continue
            if s >= thr:
                return pa, s
        return None, 0.0

    def best(self, book_side, names):
        """Best price on one side ('ask' or 'bid') across the named feeds.
        Returns (price, total size at that price, [(venue, size), ...]) or (None, 0, [])."""
        best = None
        at = []
        for v in self._live(names):
            p, s = self.vbest(v, book_side)
            if p is None:
                continue
            if best is None or (p < best - 1e-9 if book_side == "ask" else p > best + 1e-9):
                best, at = p, [(v.name, s)]
            elif abs(p - best) <= 1e-9:
                at.append((v.name, s))
        return best, sum(s for _, s in at), at

    def size_through(self, v, book_side, raw_px):
        """Size of venue v at its own (raw) prices at or better than raw_px, levels of one share-equivalent or more."""
        side = v.asks if book_side == "ask" else v.bids
        return side.size_through(raw_px, self.min_level)

    def best_min(self, book_side, names, min_size):
        """Best price on one side using only levels of at least min_size (round lots), no delay."""
        best = None
        for v in self._live(names):
            p, _ = self.vbest(v, book_side, min_size)
            if p is None:
                continue
            if best is None or (p < best if book_side == "ask" else p > best):
                best = p
        return best

    def top(self, names):
        bid, bsz, _ = self.best("bid", names)
        ask, asz, _ = self.best("ask", names)
        return bid, bsz, ask, asz

    def mid(self, names):
        bid, _, _ = self.best("bid", names)
        ask, _, _ = self.best("ask", names)
        if bid is None or ask is None:
            return None
        return 0.5 * (bid + ask)

    def _tagged(self, v, book_side):
        side = v.asks if book_side == "ask" else v.bids
        bcap, afloor = self._limits(v)
        lim = afloor if book_side == "ask" else bcap
        basis = v.basis
        name = v.name
        floor = self.min_level - 1e-12
        for p, s in side.levels():
            if s < floor:
                continue
            pa = p - basis
            if lim is not None:
                if book_side == "ask":
                    if pa <= lim + 1e-9:
                        continue
                elif pa >= lim - 1e-9:
                    continue
            yield pa, s, name

    def levels(self, book_side, names):
        """Consolidated levels of one side, best first: (price, size, venue)."""
        gens = [self._tagged(v, book_side) for v in self._live(names)]
        key = (lambda x: x[0]) if book_side == "ask" else (lambda x: -x[0])
        return heapq.merge(*gens, key=key)

    def walk_gap(self, side, names, sip_px, qty):
        """Executable value the order could have had on the direct feeds: walk the consolidated book from the top while
        the level price is better than the SIP price, up to the order size; sum (SIP - level) x size used.
        Returns (gap_usd, qty_better)."""
        book_side = "ask" if side == BUY else "bid"
        rem = qty
        gap = 0.0
        got = 0.0
        for p, s, _ in self.levels(book_side, names):
            better = (p < sip_px - 1e-9) if side == BUY else (p > sip_px + 1e-9)
            if not better or rem <= 1e-12:
                break
            take = s if s < rem else rem
            gap += abs(sip_px - p) * take
            got += take
            rem -= take
        return gap, got

    def walk_vwap(self, side, names, qty):
        """Average price of taking qty from the displayed consolidated liquidity alone. Returns (vwap, filled)."""
        book_side = "ask" if side == BUY else "bid"
        rem = qty
        cost = 0.0
        got = 0.0
        for p, s, _ in self.levels(book_side, names):
            if rem <= 1e-12:
                break
            take = s if s < rem else rem
            cost += p * take
            got += take
            rem -= take
        return (cost / got if got > 0 else None), got

    def depth_within(self, book_side, names, bps):
        """Displayed size within bps of the consolidated best price (liquidity)."""
        best, _, _ = self.best(book_side, names)
        if best is None:
            return 0.0
        lim = best * (1 + bps * 1e-4) if book_side == "ask" else best * (1 - bps * 1e-4)
        tot = 0.0
        for p, s, _ in self.levels(book_side, names):
            if (p > lim) if book_side == "ask" else (p < lim):
                break
            tot += s
        return tot
