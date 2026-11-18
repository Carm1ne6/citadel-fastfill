"""SIP emulation: the slow, consolidated view (SEC 4-5, 19, 22-23).

"a SIP consolidates all of the best-priced, round lot quotations from each exchange ... calculates the NBBO for the
security, and disseminates both the exchanges' best quotations and the NBBO." (SEC 19)
"The SIPs do not disseminate odd lot quotations" (SEC 5)
"Because of the time required for this consolidation process and the additional physical distance ... a market
participant may be able to receive information from the direct feeds and process it before the market participant
has received the same information from the SIP." (SEC 22; fn 9: 4.04 ms NYSE SIP / 5.42 ms Nasdaq SIP, early 2010)

Each venue's best quote of at least `min_size` (one round lot; 0 = any size for the `latency` preset) enters a FIFO
queue when the venue's book changes and is published `latency` later. At time m the NBBO uses only quotes received at
or before m - latency: no look-ahead.
"""
from collections import deque


class SipEmulator:
    def __init__(self, venue_names, latency_ms, min_size):
        self.lat = int(round(latency_ms * 1e6))
        self.min_size = min_size
        self.names = list(venue_names)
        self.q = deque()
        self.src = {}                                      # last quote queued per venue (dedupe)
        self.pub = {n: (None, 0.0, None, 0.0) for n in self.names}
        self.nbb = self.nbo = None
        self.nbb_size = self.nbo_size = 0.0
        self.nbb_venues = self.nbo_venues = ()
        self.n_published = 0

    def source(self, m, name, quote):
        """quote = (bid, bid_size, ask, ask_size) of one venue at receive time m; (None, 0, None, 0) = no quote."""
        if self.src.get(name) == quote:
            return
        self.src[name] = quote
        self.q.append((m + self.lat, name, quote))

    def next_time(self):
        return self.q[0][0] if self.q else None

    def advance(self, m):
        """Publish every queued quote whose publication time is <= m. Returns True if the NBBO inputs changed."""
        q = self.q
        if not q or q[0][0] > m:
            return False
        while q and q[0][0] <= m:
            _, name, quote = q.popleft()
            self.pub[name] = quote
            self.n_published += 1
        self._consolidate()
        return True

    def _consolidate(self):
        nbb = nbo = None
        bs = as_ = 0.0
        bv = []
        av = []
        for name, (b, bsz, a, asz) in self.pub.items():
            if b is not None:
                if nbb is None or b > nbb + 1e-9:
                    nbb, bs, bv = b, bsz, [name]
                elif abs(b - nbb) <= 1e-9:
                    bs += bsz
                    bv.append(name)
            if a is not None:
                if nbo is None or a < nbo - 1e-9:
                    nbo, as_, av = a, asz, [name]
                elif abs(a - nbo) <= 1e-9:
                    as_ += asz
                    av.append(name)
        self.nbb, self.nbb_size, self.nbb_venues = nbb, bs, tuple(bv)
        self.nbo, self.nbo_size, self.nbo_venues = nbo, as_, tuple(av)

    def price(self, side):
        """SIP price a marketable order of `side` would be internalized at: NBO for a buy, NBB for a sell."""
        return self.nbo if side == "BUY" else self.nbb

    def size(self, side):
        return self.nbo_size if side == "BUY" else self.nbb_size

    def venues_at(self, side):
        return self.nbo_venues if side == "BUY" else self.nbb_venues
