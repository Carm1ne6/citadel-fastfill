"""Per-venue order books rebuilt from the recorded public WebSocket messages (the "depth of book feeds", SEC 6-7).

One class per venue. `on_record(rec)` applies one recorded line and returns (book_changed, trades), where each trade
is (aggressor_side, price, size, taker_key). Aggressor sides:
- Coinbase `match.side` is the MAKER side, so the taker (the marketable order) is the opposite side.
- Kraken v2 `trade.side`, Bitstamp `type` (0 = buy, 1 = sell) and Gemini v2 `trade.side` are taker sides.
check_venues.py verifies these against the books (a taker buy prints at or above the venue's mid).

taker_key names the taker order a fill belongs to, so that the fills of one marketable order can be counted as ONE
order (sim.py groups them): Coinbase `taker_order_id`; Bitstamp the newer (larger) of `buy_order_id` / `sell_order_id`
(the taker is the order that arrived last); Kraken and Gemini publish no order ids, so the key is the fill's side and
exchange timestamp (the fills of one sweep share both).

Reconnects (`event: connected` / `error`) clear the venue's book until a new snapshot arrives; a venue with no valid
book is left out of every view.
"""
import bisect
import gzip
import json
import zlib

BUY, SELL = "BUY", "SELL"


def iter_records(path):
    """Yield the JSON records of a recorder file. Files from a killed recorder have no gzip trailer and may end in a
    half-written line: stop cleanly at the first truncated or unreadable line."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.endswith("\n"):
                    return
                try:
                    yield json.loads(line)
                except ValueError:
                    return
    except (EOFError, zlib.error, OSError):
        return


class BookSide:
    """One side of a price-level book: {price: size} plus the prices kept sorted best-first."""
    __slots__ = ("is_bid", "size", "keys")

    def __init__(self, is_bid):
        self.is_bid = is_bid
        self.size = {}
        self.keys = []          # asks: price ascending; bids: -price ascending (best first in both)

    def clear(self):
        self.size = {}
        self.keys = []

    def load(self, levels):
        size = {}
        for p, s in levels:
            if s > 0:
                size[p] = s
        self.size = size
        ks = [-p for p in size] if self.is_bid else list(size)
        ks.sort()
        self.keys = ks

    def set(self, p, s):
        if s > 0:
            if p not in self.size:
                bisect.insort(self.keys, -p if self.is_bid else p)
            self.size[p] = s
        elif p in self.size:
            del self.size[p]
            k = -p if self.is_bid else p
            i = bisect.bisect_left(self.keys, k)
            if i < len(self.keys) and self.keys[i] == k:
                del self.keys[i]

    def truncate(self, n):
        if len(self.keys) > n:
            for k in self.keys[n:]:
                del self.size[-k if self.is_bid else k]
            del self.keys[n:]

    def best(self):
        if not self.keys:
            return None, 0.0
        k = self.keys[0]
        p = -k if self.is_bid else k
        return p, self.size[p]

    def best_min(self, min_size):
        """Best price level whose size is at least min_size (a round-lot quote, SEC 5, 19)."""
        if min_size <= 0:
            return self.best()
        thr = min_size - 1e-12
        for k in self.keys:
            p = -k if self.is_bid else k
            s = self.size[p]
            if s >= thr:
                return p, s
        return None, 0.0

    def levels(self, n=None):
        ks = self.keys if n is None else self.keys[:n]
        for k in ks:
            p = -k if self.is_bid else k
            yield p, self.size[p]

    def size_through(self, px, min_size=0.0):
        """Total size at prices at or better than px, counting only levels of at least min_size."""
        tot = 0.0
        floor = min_size - 1e-12
        for k in self.keys:
            p = -k if self.is_bid else k
            if (p < px - 1e-9) if self.is_bid else (p > px + 1e-9):
                break
            s = self.size[p]
            if s >= floor:
                tot += s
        return tot


class Venue:
    name = "venue"

    def __init__(self):
        self.bids = BookSide(True)
        self.asks = BookSide(False)
        self.valid = False
        self.basis = 0.0            # USD added to the reference venue's price scale (sim.py), 0 = unadjusted
        self.n_msgs = 0
        self.n_trades = 0
        self.n_resets = 0
        self.n_bad = 0

    @property
    def live(self):
        return self.valid

    def reset(self):
        self.bids.clear()
        self.asks.clear()
        self.valid = False

    def on_record(self, rec):
        return self.apply(rec, self.decode(rec))

    @staticmethod
    def decode(rec):
        """The message of a recorded line as a dict, None for an event line or a line that is not a JSON object.
        No side effects: sim.Session looks at a message before it is applied."""
        raw = rec.get("raw")
        if raw is None:
            return None
        try:
            msg = json.loads(raw)
        except ValueError:
            return None
        return msg if isinstance(msg, dict) else None

    def apply(self, rec, msg):
        """Apply one recorded line whose message decode() returned. Returns (book_changed, trades)."""
        if rec.get("raw") is None:
            if rec.get("event") in ("connected", "error"):
                self.reset()
                self.n_resets += 1
                return True, ()
            return False, ()
        self.n_msgs += 1
        if msg is None:
            self.n_bad += 1
            return False, ()
        changed, trades = self.on_msg(msg)
        self.n_trades += len(trades)
        return changed, trades

    def is_trade(self, msg):
        """True if msg is a trade message (it carries prints and never changes the book)."""
        raise NotImplementedError

    def on_msg(self, msg):
        raise NotImplementedError


class Coinbase(Venue):
    name = "coinbase"
    product = "BTC-USD"

    def is_trade(self, msg):
        return msg.get("type") == "match"

    def on_msg(self, msg):
        typ = msg.get("type")
        if typ == "l2update":
            if not self.valid or msg.get("product_id") != self.product:
                return False, ()
            for side, p, s in msg.get("changes", ()):
                (self.bids if side == "buy" else self.asks).set(float(p), float(s))
            return True, ()
        if typ == "match":
            if msg.get("product_id") != self.product:
                return False, ()
            aggr = SELL if msg.get("side") == "buy" else BUY      # side = maker side
            return False, ((aggr, float(msg["price"]), float(msg["size"]), msg.get("taker_order_id")),)
        if typ == "snapshot" and msg.get("product_id") == self.product:
            self.bids.load((float(p), float(s)) for p, s in msg.get("bids", ()))
            self.asks.load((float(p), float(s)) for p, s in msg.get("asks", ()))
            self.valid = True
            return True, ()
        return False, ()      # last_match (history), heartbeat, subscriptions


def kraken_checksum(asks, bids, price_dec=1, qty_dec=8):
    """Kraken v2 book checksum: CRC32 over the top 10 asks (low to high) then the top 10 bids (high to low), each
    level as price and qty printed at the pair's precision with the '.' and the leading zeros removed."""
    parts = []
    for p, q in list(asks)[:10] + list(bids)[:10]:
        parts.append(f"{p:.{price_dec}f}".replace(".", "").lstrip("0"))
        parts.append(f"{q:.{qty_dec}f}".replace(".", "").lstrip("0"))
    return zlib.crc32("".join(parts).encode()) & 0xFFFFFFFF


class Kraken(Venue):
    name = "kraken"
    symbol = "BTC/USD"
    depth = 25            # the recorder subscribes with depth 25; levels pushed below it must be truncated

    def __init__(self):
        super().__init__()
        self.crc_ok = True
        self.n_crc = 0
        self.n_crc_bad = 0

    @property
    def live(self):
        return self.valid and self.crc_ok

    def reset(self):
        super().reset()
        self.crc_ok = True

    def is_trade(self, msg):
        return msg.get("channel") == "trade"

    def on_msg(self, msg):
        ch = msg.get("channel")
        if ch == "book":
            typ = msg.get("type")
            changed = False
            for d in msg.get("data", ()):
                if d.get("symbol") != self.symbol:
                    continue
                if typ == "snapshot":
                    self.bids.load((float(x["price"]), float(x["qty"])) for x in d.get("bids", ()))
                    self.asks.load((float(x["price"]), float(x["qty"])) for x in d.get("asks", ()))
                    self.valid = True
                elif typ == "update":
                    if not self.valid:
                        continue
                    for x in d.get("bids", ()):
                        self.bids.set(float(x["price"]), float(x["qty"]))
                    for x in d.get("asks", ()):
                        self.asks.set(float(x["price"]), float(x["qty"]))
                    self.bids.truncate(self.depth)
                    self.asks.truncate(self.depth)
                else:
                    continue
                changed = True
                cs = d.get("checksum")
                if cs is not None:
                    self.n_crc += 1
                    ok = kraken_checksum(self.asks.levels(10), self.bids.levels(10)) == int(cs)
                    if not ok:
                        self.n_crc_bad += 1
                    self.crc_ok = ok       # a book that fails its checksum is left out until it matches again
            return changed, ()
        if ch == "trade" and msg.get("type") in ("update",):
            out = []
            for x in msg.get("data", ()):
                if x.get("symbol") != self.symbol:
                    continue
                side = BUY if x.get("side") == "buy" else SELL
                out.append((side, float(x["price"]), float(x["qty"]), (side, x.get("timestamp"))))
            return False, tuple(out)
        return False, ()


class Bitstamp(Venue):
    name = "bitstamp"

    def is_trade(self, msg):
        return msg.get("event") == "trade"

    def on_msg(self, msg):
        ch = msg.get("channel", "") or ""
        ev = msg.get("event")
        if ev == "data" and ch.startswith("order_book"):
            d = msg.get("data") or {}
            self.bids.load((float(p), float(s)) for p, s in d.get("bids", ()))
            self.asks.load((float(p), float(s)) for p, s in d.get("asks", ()))
            self.valid = True        # every message is a full top-20 snapshot (replace)
            return True, ()
        if ev == "trade" and ch.startswith("live_trades"):
            d = msg.get("data") or {}
            aggr = BUY if int(d.get("type", 0)) == 0 else SELL
            px = float(d.get("price_str") or d.get("price"))
            sz = float(d.get("amount_str") or d.get("amount"))
            try:                                  # the taker is the newer order: the larger id
                key = max(int(d["buy_order_id"]), int(d["sell_order_id"]))
            except (KeyError, TypeError, ValueError):
                key = d.get("id")
            return False, ((aggr, px, sz, key),)
        return False, ()


class Gemini(Venue):
    name = "gemini"
    symbol = "BTCUSD"

    def is_trade(self, msg):
        return msg.get("type") == "trade"

    def on_msg(self, msg):
        typ = msg.get("type")
        if typ == "l2_updates":
            if msg.get("symbol", self.symbol) != self.symbol:
                return False, ()
            ch = msg.get("changes", ())
            if not self.valid:     # the first l2_updates after (re)connecting carries the full book
                self.bids.load((float(p), float(q)) for s, p, q in ch if s == "buy")
                self.asks.load((float(p), float(q)) for s, p, q in ch if s == "sell")
                self.valid = True
                return True, ()
            for s, p, q in ch:
                (self.bids if s == "buy" else self.asks).set(float(p), float(q))
            return True, ()
        if typ == "trade" and msg.get("symbol", self.symbol) == self.symbol:
            aggr = BUY if msg.get("side") == "buy" else SELL
            return False, ((aggr, float(msg["price"]), float(msg["quantity"]), (aggr, msg.get("timestamp"))),)
        return False, ()


VENUE_CLASSES = {"coinbase": Coinbase, "kraken": Kraken, "bitstamp": Bitstamp, "gemini": Gemini}


def make_venue(name):
    return VENUE_CLASSES[name]()
